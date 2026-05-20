#!/usr/bin/env bash
# eks-autoscaling-test.sh
#
# AWS EKS Node 장애 + Cluster Autoscaler 오토스케일링 시나리오 테스트
#
# 목적:
#   Running Node 강제 drain → 해당 Node의 Pod이 Pending 상태로 전환되고
#   Cluster Autoscaler가 ASG를 scale-up하여 신규 Node를 프로비저닝하는 동안
#   Pod가 정상 Node로 재배치되어 서비스 무중단임을 검증
#   테스트 완료 후 drain된 Node를 uncordon하여 원복
#
# 사용법:
#   bash eks-autoscaling-test.sh                        # 전체 실행 (자동 Node 선택)
#   bash eks-autoscaling-test.sh all <node-name>        # 지정 Node로 전체 실행
#   bash eks-autoscaling-test.sh check                  # 현재 Node/Pod/ASG 상태만 확인
#   bash eks-autoscaling-test.sh run   [node-name]      # Node drain + 복구 모니터링
#   bash eks-autoscaling-test.sh verify                 # CA 로그 + Pod 상태 검증만 실행
#   bash eks-autoscaling-test.sh restore [node-name]    # drain된 Node uncordon 원복
#
# 사전 조건:
#   kubectl (bookflow 네임스페이스 접근), aws CLI (ap-northeast-1)
#   Cluster Autoscaler 설치 (kube-system/cluster-autoscaler)
#
# 환경 변수 (선택):
#   AWS_REGION        (기본: ap-northeast-1)
#   NAMESPACE         (기본: bookflow)
#   NODE_TIMEOUT      신규 Node Ready 대기 최대 초 (기본: 180)
#   POD_TIMEOUT       Pod Running 대기 최대 초 (기본: 120)

set -euo pipefail

# ── 설정 ─────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
NAMESPACE="${NAMESPACE:-bookflow}"
NODE_TIMEOUT="${NODE_TIMEOUT:-180}"
POD_TIMEOUT="${POD_TIMEOUT:-120}"

# EKS 클러스터 / ASG
CLUSTER_NAME="bookflow-eks"
ASG_NAME="eks-bookflow-eks-ng-0acf1735-0732-7dd7-389c-28dc3b9ca9cd"

# ALB / Target Group (EKS NGINX Ingress NLB)
TG_ARN_1="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-2a7ed77e21/d5014d44dd4d2298"
TG_ARN_2="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-8cccae40c7/cf5c9b4eb0aae397"
ALB_SUFFIX="app/bookflow-alb-external/57e62cdd02356761"

# Cluster Autoscaler
CA_NS="kube-system"
CA_LABEL="app=cluster-autoscaler"

# NGINX Ingress (5xx 로그 소스)
INGRESS_NS="ingress-nginx"
INGRESS_LABEL="app.kubernetes.io/name=ingress-nginx"

# ── 색상 출력 ──────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}    $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}      $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC}   $*"; }
step()    { echo -e "${CYAN}[STEP]${NC}    $*"; }
section() {
    echo ""
    echo -e "${BLUE}══════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $*${NC}"
    echo -e "${BLUE}══════════════════════════════════════════════════${NC}"
}

# ── Ready Node 목록 (SchedulingDisabled 제외) ─────────────────────
get_ready_nodes() {
    kubectl get nodes --no-headers 2>/dev/null \
        | awk '$2=="Ready" {print $1}' \
        || true
}

# ── Node당 bookflow Pod 수 (가장 많은 Node 반환) ─────────────────
auto_select_node() {
    local best_node="" best_count=0
    while IFS= read -r node; do
        local count
        count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
            --field-selector=status.phase=Running 2>/dev/null \
            | grep -v Completed \
            | awk -v n="$node" '$7==n {c++} END {print c+0}') || count=0
        if [[ "$count" -gt "$best_count" ]]; then
            best_count=$count
            best_node=$node
        fi
    done < <(get_ready_nodes)
    echo "$best_node"
}

# ── ASG 현재 상태 조회 ────────────────────────────────────────────
get_asg_desired() {
    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].DesiredCapacity" \
        --output text 2>/dev/null || echo "N/A"
}

# ── ALB Target Health 출력 ────────────────────────────────────────
print_target_health() {
    local label="$1"
    echo ""
    info "${label}"
    for arn in "$TG_ARN_1" "$TG_ARN_2"; do
        aws elbv2 describe-target-health \
            --target-group-arn "$arn" \
            --region "$AWS_REGION" \
            --query "TargetHealthDescriptions[].{Target:Target.Id, Port:Target.Port, State:TargetHealth.State}" \
            --output table 2>&1
    done
}

# ── CloudWatch 5xx 조회 ───────────────────────────────────────────
check_5xx_cloudwatch() {
    local minutes="${1:-15}"
    local start end count

    if date -u -d "${minutes} minutes ago" +%Y-%m-%dT%H:%M:%SZ &>/dev/null 2>&1; then
        start=$(date -u -d "${minutes} minutes ago" +%Y-%m-%dT%H:%M:%SZ)
    else
        start=$(date -u -v-${minutes}M +%Y-%m-%dT%H:%M:%SZ)
    fi
    end=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    count=$(aws cloudwatch get-metric-statistics \
        --namespace AWS/ApplicationELB \
        --metric-name HTTPCode_Target_5XX_Count \
        --dimensions Name=LoadBalancer,Value="$ALB_SUFFIX" \
        --start-time "$start" --end-time "$end" \
        --period $((minutes * 60)) --statistics Sum \
        --region "$AWS_REGION" \
        --query "sum(Datapoints[].Sum)" \
        --output text 2>/dev/null || echo "None")

    if [[ "$count" == "None" || "$count" == "0" || -z "$count" ]]; then
        echo "0"
    else
        printf "%.0f" "$count" 2>/dev/null || echo "$count"
    fi
}

# ── NGINX Ingress 5xx 로그 조회 ───────────────────────────────────
check_5xx_nginx() {
    local since="${1:-15m}"
    local ingress_pod count

    ingress_pod=$(kubectl get pods -n "$INGRESS_NS" -l "$INGRESS_LABEL" \
        --no-headers 2>/dev/null | awk '$3=="Running" {print $1; exit}')

    if [[ -z "$ingress_pod" ]]; then
        echo "N/A (ingress pod not found)"
        return
    fi

    count=$(kubectl logs -n "$INGRESS_NS" "$ingress_pod" \
        --since="${since}" 2>/dev/null \
        | grep -cE 'HTTP/[0-9.]+" 5[0-9]{2} ' || echo "0")

    echo "$count"
}

# ════════════════════════════════════════════════════════════════
# check: 현재 상태 확인
# ════════════════════════════════════════════════════════════════
cmd_check() {
    section "현재 EKS Node / Pod / ASG 상태"

    step "1/4  Node 목록"
    kubectl get nodes -o wide --no-headers \
        | awk '{printf "  %-50s %-25s %-15s %s\n", $1, $2, $3, $6}' \
        | column -t

    echo ""
    step "2/4  bookflow Pod 배치 현황 (Node별)"
    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v Completed \
        | awk '{printf "  %-45s %-8s %-15s %s\n", $1, $2, $3, $7}' \
        | sort -k4 \
        | column -t

    echo ""
    step "3/4  ASG 상태 (${ASG_NAME})"
    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:length(Instances)}" \
        --output table 2>&1

    step "4/4  Cluster Autoscaler Pod"
    kubectl get pods -n "$CA_NS" -l "$CA_LABEL" --no-headers \
        | awk '{printf "  %-45s %s\n", $1, $2}'

    print_target_health "5/4  ALB Target Health"
}

# ════════════════════════════════════════════════════════════════
# run: Node drain + 신규 Node 프로비저닝 모니터링
# ════════════════════════════════════════════════════════════════
cmd_run() {
    local target_node="${1:-}"

    section "Step 1 — Node 강제 장애 유발 (drain)"

    if [[ -z "$target_node" ]]; then
        target_node=$(auto_select_node)
    fi

    if [[ -z "$target_node" ]]; then
        error "drain할 Ready Node를 찾을 수 없습니다."
        exit 1
    fi

    # drain 전 정보 수집
    local pre_desired pre_pod_count
    pre_desired=$(get_asg_desired)
    pre_pod_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v Completed | awk '$2=="1/1" || $2~/^[2-9]\/[2-9]$/ {c++} END {print c+0}')

    info "대상 Node    : ${target_node}"
    info "drain 전 ASG Desired: ${pre_desired}"
    info "drain 전 Running Pod: ${pre_pod_count}개"
    echo ""

    local t0
    t0=$(date +%s)

    step "kubectl drain --ignore-daemonsets --delete-emptydir-data ${target_node}"
    kubectl drain "$target_node" --ignore-daemonsets --delete-emptydir-data 2>&1 \
        | grep -v "^I[0-9]" | sed 's/^/  /'
    ok "drain 완료 — Pod Eviction 시작, CA scale-up 트리거 대기"

    # ── Step 2: Pending Pod → 신규 Node 프로비저닝 모니터링 ────────
    section "Step 2 — Pending Pod + 신규 Node 프로비저닝 모니터링"
    info "신규 Node Ready + 모든 Pod Running 대기 (최대 ${NODE_TIMEOUT}s)..."
    echo ""

    local elapsed=0 new_node="" node_ready=false all_pods_ok=false
    local post_desired="$pre_desired"

    while [[ $elapsed -lt $NODE_TIMEOUT ]]; do
        sleep 5
        elapsed=$(( $(date +%s) - t0 ))

        echo -e "  [+${elapsed}s] ── Nodes ──"
        kubectl get nodes --no-headers \
            | awk '{printf "    %-50s %s\n", $1, $2}'

        # ASG Desired 변화 감지
        local cur_desired
        cur_desired=$(get_asg_desired)
        if [[ "$cur_desired" != "$post_desired" ]]; then
            echo ""
            ok "ASG Desired 변경: ${post_desired} → ${cur_desired}  (+${elapsed}s)"
            post_desired=$cur_desired
        fi

        echo ""
        echo -e "  [+${elapsed}s] ── bookflow Pods ──"
        kubectl get pods -n "$NAMESPACE" --no-headers \
            | grep -v Completed \
            | awk '{printf "    %-45s %-8s %s\n", $1, $2, $4}'

        # 신규 Node 감지 (target_node 외에 Ready 상태)
        new_node=$(kubectl get nodes --no-headers 2>/dev/null \
            | awk -v tn="$target_node" '$1!=tn && $2=="Ready" {print $1}' \
            | grep -v "$( kubectl get nodes --no-headers | awk -v tn="$target_node" 'NR==1 && $1!=tn {print $1}' )" \
            2>/dev/null | head -1 || true)

        # 좀 더 안정적인 신규 Node 감지: 모든 Ready Node 중 drained 제외하고 2개 이상인지
        local ready_count
        ready_count=$(kubectl get nodes --no-headers 2>/dev/null \
            | awk '$2=="Ready" {c++} END {print c+0}')

        if [[ "$ready_count" -ge 2 && "$node_ready" != "true" ]]; then
            # 새 Node가 Ready 상태로 올라왔는지 확인 (target_node 제외)
            local actually_new
            actually_new=$(kubectl get nodes --no-headers 2>/dev/null \
                | awk -v tn="$target_node" '$1!=tn && $2=="Ready" {print $1}')
            local new_count
            new_count=$(echo "$actually_new" | grep -c . || true)

            if [[ "$new_count" -ge 2 ]]; then
                echo ""
                ok "신규 Node Ready 확인 (+${elapsed}s)"
                node_ready=true
            fi
        fi

        # 모든 Pod Running 확인
        local pending_count
        pending_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
            | grep -v Completed | grep -cE "Pending|ContainerCreating|Init" || true)

        if [[ "$node_ready" == "true" && "$pending_count" -eq 0 ]]; then
            echo ""
            ok "모든 bookflow Pod Running 확인 (+${elapsed}s)"
            all_pods_ok=true
            break
        fi

        echo ""
    done

    if [[ "$node_ready" != "true" ]]; then
        warn "신규 Node 프로비저닝 타임아웃 (${NODE_TIMEOUT}s)"
    fi
    if [[ "$all_pods_ok" != "true" ]]; then
        warn "Pod 재배치 완료 미확인 — 현재 상태 확인 필요"
    fi

    info "drain된 Node 복구 (uncordon)..."
    kubectl uncordon "$target_node"
    ok "uncordon 완료: ${target_node}"
}

# ════════════════════════════════════════════════════════════════
# verify: CA 로그 + Pod/Node 최종 검증
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    section "Step 3 — Cluster Autoscaler scale-up 이벤트 확인"

    step "CA 상태 configmap"
    kubectl describe configmap cluster-autoscaler-status -n "$CA_NS" 2>/dev/null \
        | grep -A 30 "^Data" | grep -E "(status:|autoscalerStatus|health:|scaleUp:|scaleDown:|cloudProviderTarget|minSize|maxSize|lastScaleUpTime|NoActivity|InProgress|lastTransition)" \
        | sed 's/^/  /' \
        || warn "cluster-autoscaler-status configmap 없음"

    echo ""
    step "CA 로그 (scale-up 관련)"
    local scale_up_log
    scale_up_log=$(kubectl logs -n "$CA_NS" -l "$CA_LABEL" --tail=200 2>/dev/null \
        | grep -E "(IncreaseSize|TriggeredScaleUp|ScaleUp|scale-up|lastScaleUpTime|unschedulable|Pending|FoundASG|node group)" \
        | tail -10)

    if [[ -n "$scale_up_log" ]]; then
        echo "$scale_up_log" | sed 's/^/  /'
    else
        warn "CA 로그에서 scale-up 이벤트를 찾지 못했습니다."
    fi

    # ── Step 4: 최종 Node 상태 ─────────────────────────────────
    section "Step 4 — Node 최종 상태"

    local node_list ready_count total_count
    node_list=$(kubectl get nodes --no-headers)
    ready_count=$(echo "$node_list" | awk '$2=="Ready" {c++} END {print c+0}')
    total_count=$(echo "$node_list" | wc -l)

    echo "$node_list" | awk '{printf "  %-50s %-25s %s\n", $1, $2, $5}' | column -t
    echo ""
    info "Ready: ${ready_count}/${total_count} Nodes"

    local new_node_status
    if [[ "$ready_count" -ge 2 ]]; then
        new_node_status="✅ Ready Node ${ready_count}개"
    else
        new_node_status="❌ Ready Node ${ready_count}개 (부족)"
    fi

    # ── Step 5: Pod 최종 상태 ──────────────────────────────────
    section "Step 5 — bookflow Pod 최종 배치"

    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v Completed \
        | awk '{printf "  %-45s %-8s %-15s %s\n", $1, $2, $3, $7}' \
        | sort -k4 \
        | column -t

    local running_count pending_count
    running_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v Completed | awk '$2=="1/1" || $2~/^[2-9]\/[2-9]$/ {c++} END {print c+0}')
    pending_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v Completed | grep -cE "Pending|ContainerCreating" || true)

    local pod_status
    if [[ "$pending_count" -eq 0 ]]; then
        pod_status="✅ Running ${running_count}개 / Pending 0개"
    else
        pod_status="❌ Pending ${pending_count}개 잔존"
    fi

    # ── Step 6: ASG 상태 ───────────────────────────────────────
    section "Step 6 — ASG 최종 상태"

    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:Instances[].{ID:InstanceId,State:LifecycleState}}" \
        --output json 2>&1 | sed 's/^/  /'

    local asg_desired
    asg_desired=$(get_asg_desired)
    local asg_status
    if [[ "$asg_desired" -ge 3 ]]; then
        asg_status="✅ Desired ${asg_desired} (scale-up 완료)"
    else
        asg_status="❌ Desired ${asg_desired} (scale-up 미완료)"
    fi

    # ── Step 7: ALB Target Health ──────────────────────────────
    section "Step 7 — ALB Target Health 확인"

    local all_healthy=true
    for arn in "$TG_ARN_1" "$TG_ARN_2"; do
        local result unhealthy
        result=$(aws elbv2 describe-target-health \
            --target-group-arn "$arn" \
            --region "$AWS_REGION" \
            --query "TargetHealthDescriptions[].{Target:Target.Id, Port:Target.Port, State:TargetHealth.State}" \
            --output table 2>&1)
        echo "$result"
        unhealthy=$(echo "$result" | grep -c "unhealthy" || true)
        if [[ "$unhealthy" -gt 0 ]]; then
            all_healthy=false
        fi
    done

    local alb_status
    [[ "$all_healthy" == "true" ]] && alb_status="✅ 모든 타깃 healthy" || alb_status="❌ unhealthy 타깃 존재"

    # ── Step 8: 5xx 확인 ──────────────────────────────────────
    section "Step 8 — 서비스 5xx 미발생 확인"

    step "CloudWatch ALB 5xx (최근 15분)"
    local cw_count
    cw_count=$(check_5xx_cloudwatch 15)
    echo "  HTTPCode_Target_5XX_Count: ${cw_count}건"
    [[ "$cw_count" == "0" ]] && ok "CloudWatch 5xx 0건" || warn "CloudWatch 5xx ${cw_count}건 감지"

    step "NGINX Ingress 로그 5xx (최근 15분)"
    local nginx_count
    nginx_count=$(check_5xx_nginx "15m")
    echo "  NGINX 5xx 로그: ${nginx_count}건"
    if [[ "$nginx_count" == "0" ]]; then
        ok "NGINX 5xx 0건"
    else
        warn "NGINX 5xx ${nginx_count}건 감지"
        kubectl logs -n "$INGRESS_NS" \
            -l "$INGRESS_LABEL" --since=15m 2>/dev/null \
            | grep -E 'HTTP/[0-9.]+" 5[0-9]{2} ' | tail -5 \
            | sed 's/^/  /' || true
    fi

    local cw_status ng_status
    [[ "$cw_count" == "0" ]] && cw_status="✅ 0건" || cw_status="❌ ${cw_count}건"
    [[ "$nginx_count" == "0" ]] && ng_status="✅ 0건" || ng_status="❌ ${nginx_count}건"

    # ── 검증 요약 ─────────────────────────────────────────────
    section "검증 결과"
    printf "  %-30s %-28s %s\n" "항목" "기대 결과" "실제 결과"
    printf "  %-30s %-28s %s\n" "──────────────────────────────" "────────────────────────────" "──────────"
    printf "  %-30s %-28s %s\n" "Pod 재배치"            "Pending 0개, Running 전체" "$pod_status"
    printf "  %-30s %-28s %s\n" "신규 Node"             "STATUS=Ready Node 추가됨"  "$new_node_status"
    printf "  %-30s %-28s %s\n" "ASG scale-up"          "Desired 증가"              "$asg_status"
    printf "  %-30s %-28s %s\n" "ALB Target Health"     "정상 Pod만 healthy"        "$alb_status"
    printf "  %-30s %-28s %s\n" "5xx (CloudWatch)"      "0건"                       "$cw_status"
    printf "  %-30s %-28s %s\n" "5xx (NGINX 로그)"      "0건"                       "$ng_status"
    echo ""
}

# ════════════════════════════════════════════════════════════════
# restore: drain된 Node uncordon 원복
# ════════════════════════════════════════════════════════════════
cmd_restore() {
    local target_node="${1:-}"

    section "Node Uncordon 원복"

    if [[ -z "$target_node" ]]; then
        # SchedulingDisabled 상태 Node 자동 탐지
        target_node=$(kubectl get nodes --no-headers 2>/dev/null \
            | awk '$2=="Ready,SchedulingDisabled" {print $1; exit}')
    fi

    if [[ -z "$target_node" ]]; then
        warn "SchedulingDisabled Node가 없습니다 — 원복 불필요."
        kubectl get nodes
        return
    fi

    info "대상 Node: ${target_node}"
    kubectl uncordon "$target_node"
    ok "uncordon 완료: ${target_node}"
    echo ""
    kubectl get nodes
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    local target_node="${1:-}"

    section "EKS Node 오토스케일링 시나리오 전체 실행"
    echo ""
    echo "  단계: 상태확인 → Node drain → 복구 모니터링 → CA/ALB/5xx 검증"
    echo ""

    cmd_check

    echo ""
    if [[ -z "$target_node" ]]; then
        target_node=$(auto_select_node)
        warn "drain 대상: ${target_node}  (Enter 계속 / Ctrl+C 취소)"
    else
        warn "drain 대상: ${target_node}  (Enter 계속 / Ctrl+C 취소)"
    fi
    read -r

    cmd_run "$target_node"
    cmd_verify

    ok "시나리오 완료"
}

# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
MODE="${1:-all}"
ARG2="${2:-}"

case "$MODE" in
    check)   cmd_check ;;
    run)     cmd_run "$ARG2" ;;
    verify)  cmd_verify ;;
    restore) cmd_restore "$ARG2" ;;
    all)     cmd_all "$ARG2" ;;
    *)
        echo "사용법: $0 [check|run|verify|restore|all] [node-name]"
        echo ""
        echo "  check            현재 Node/Pod/ASG 상태 확인"
        echo "  run [node]       Node drain + 신규 Node 프로비저닝 모니터링 + uncordon"
        echo "  verify           CA scale-up 로그 + Pod/Node/ALB/5xx 검증"
        echo "  restore [node]   SchedulingDisabled Node uncordon 원복"
        echo "  all [node]       전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
