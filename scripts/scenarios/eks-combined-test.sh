#!/usr/bin/env bash
# eks-combined-test.sh
#
# EKS 통합 테스트 스크립트
#   Scenario 12 — Pod 장애   (pod):   Pod 강제 삭제 → 재생성 → ALB 무중단 검증
#   Scenario 13 — 오토스케일링 (scale): Node drain → CA scale-up → 신규 Node 프로비저닝 검증
#
# 사용법:
#   bash eks-combined-test.sh                                    # 두 시나리오 순서대로 전체 실행
#   bash eks-combined-test.sh pod   [check|run|verify|all]         [pod-name]
#   bash eks-combined-test.sh scale [check|run|verify|restore|all] [node-name]
#
# 사전 조건:
#   kubectl (bookflow 네임스페이스 접근), aws CLI (ap-northeast-1)
#   Cluster Autoscaler 설치 (kube-system/cluster-autoscaler)
#
# 환경 변수 (선택):
#   AWS_REGION       기본: ap-northeast-1
#   NAMESPACE        기본: bookflow
#   RECOVER_TIMEOUT  Pod Ready 대기 최대 초 (기본: 60)   — Scenario 12
#   NODE_TIMEOUT     신규 Node Ready 대기 최대 초 (기본: 180) — Scenario 13
#   POD_TIMEOUT      Pod Running 대기 최대 초 (기본: 120)    — Scenario 13

set -euo pipefail

# ── 공통 설정 ─────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
NAMESPACE="${NAMESPACE:-bookflow}"

RECOVER_TIMEOUT="${RECOVER_TIMEOUT:-60}"
NODE_TIMEOUT="${NODE_TIMEOUT:-180}"
POD_TIMEOUT="${POD_TIMEOUT:-120}"

CLUSTER_NAME="bookflow-eks"
ASG_NAME="eks-bookflow-eks-ng-0acf1735-0732-7dd7-389c-28dc3b9ca9cd"

TG_ARN_1="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-2a7ed77e21/d5014d44dd4d2298"
TG_ARN_2="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-8cccae40c7/cf5c9b4eb0aae397"
ALB_SUFFIX="app/bookflow-alb-external/57e62cdd02356761"

INGRESS_NS="ingress-nginx"
INGRESS_LABEL="app.kubernetes.io/name=ingress-nginx"

CA_NS="kube-system"
CA_LABEL="app=cluster-autoscaler"

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

# ══════════════════════════════════════════════════════════════════
# 공통 헬퍼
# ══════════════════════════════════════════════════════════════════

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

check_5xx_cloudwatch() {
    local minutes="${1:-10}"
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

check_5xx_nginx() {
    local since="${1:-10m}"
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

# ══════════════════════════════════════════════════════════════════
# Scenario 12 — Pod 장애
# ══════════════════════════════════════════════════════════════════

pf_get_running_pods() {
    kubectl get pods -n "$NAMESPACE" --no-headers \
        --field-selector=status.phase=Running 2>/dev/null \
        | awk '$2=="1/1" || $2~/^[2-9]\/[2-9]$/ {print $1}' \
        | grep -vE '^(bq-|forecast-bq-|intervention-auto|publisher-watcher|reservation-ttl)' \
        || true
}

pf_auto_select_pod() {
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        --field-selector=status.phase=Running 2>/dev/null \
        | awk '$1~/^notification-svc/ && $2=="1/1" {print $1; exit}')
    if [[ -z "$pod" ]]; then
        pod=$(pf_get_running_pods | head -1)
    fi
    echo "$pod"
}

pf_check() {
    section "[Scenario 12] 현재 EKS Pod / ALB 상태"

    step "1/2  bookflow 네임스페이스 Running Pod"
    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v "Completed" \
        | awk '{printf "  %-45s %-8s %-6s %s\n", $1, $2, $3, $7}' \
        | column -t

    print_target_health "2/2  ALB Target Health"
}

pf_run() {
    local target_pod="${1:-}"

    section "[Scenario 12] Step 1 — Pod 강제 장애 유발"

    if [[ -z "$target_pod" ]]; then
        target_pod=$(pf_auto_select_pod)
    fi
    if [[ -z "$target_pod" ]]; then
        error "삭제할 Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    info "대상 Pod: ${target_pod}"

    local old_deploy
    old_deploy=$(kubectl get pod -n "$NAMESPACE" "$target_pod" \
        -o jsonpath='{.metadata.labels.app}' 2>/dev/null || echo "unknown")

    local t0
    t0=$(date +%s)

    step "kubectl delete pod -n ${NAMESPACE} ${target_pod}"
    kubectl delete pod -n "$NAMESPACE" "$target_pod"
    ok "Pod 삭제 완료 — Deployment Controller 재생성 시작"

    section "[Scenario 12] Step 2 — 실시간 Pod 상태 모니터링"
    info "신규 Pod가 1/1 Running 될 때까지 대기 (최대 ${RECOVER_TIMEOUT}s)..."
    echo ""

    local elapsed=0 new_pod="" recovered=false
    while [[ $elapsed -lt $RECOVER_TIMEOUT ]]; do
        sleep 3
        elapsed=$(( $(date +%s) - t0 ))

        echo -e "  [+${elapsed}s]"
        kubectl get pods -n "$NAMESPACE" --no-headers \
            | grep -v "Completed" \
            | awk '{printf "    %-45s %-8s %s\n", $1, $2, $4}' 2>/dev/null || true

        new_pod=$(kubectl get pods -n "$NAMESPACE" --no-headers \
            -l "app=${old_deploy}" 2>/dev/null \
            | awk '$1!="'"$target_pod"'" && $2=="1/1" && $4!="Terminating" {print $1; exit}')

        if [[ -n "$new_pod" ]]; then
            echo ""
            ok "신규 Pod Running 확인: ${new_pod} (+${elapsed}s)"
            recovered=true
            break
        fi
    done

    if [[ "$recovered" != "true" ]]; then
        warn "Pod 복구 타임아웃 (${RECOVER_TIMEOUT}s) — 현재 상태 확인 필요"
    fi
}

pf_verify() {
    section "[Scenario 12] Step 3 — ALB Target Health 확인"

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
        [[ "$unhealthy" -gt 0 ]] && all_healthy=false
    done
    [[ "$all_healthy" == "true" ]] && ok "모든 ALB 타깃 healthy" || warn "unhealthy 타깃 존재 — 복구 대기 필요"

    section "[Scenario 12] Step 4 — 서비스 5xx 미발생 확인"

    step "CloudWatch ALB 5xx (최근 10분)"
    local cw_count
    cw_count=$(check_5xx_cloudwatch 10)
    echo "  HTTPCode_Target_5XX_Count: ${cw_count}건"
    [[ "$cw_count" == "0" ]] && ok "CloudWatch 5xx 0건" || warn "CloudWatch 5xx ${cw_count}건 감지"

    step "NGINX Ingress 로그 5xx (최근 10분)"
    local nginx_count
    nginx_count=$(check_5xx_nginx "10m")
    echo "  NGINX 5xx 로그: ${nginx_count}건"
    if [[ "$nginx_count" != "0" ]]; then
        warn "NGINX 5xx ${nginx_count}건 감지"
        kubectl logs -n "$INGRESS_NS" -l "$INGRESS_LABEL" --since=10m 2>/dev/null \
            | grep -E 'HTTP/[0-9.]+" 5[0-9]{2} ' | tail -5 | sed 's/^/  /' || true
    else
        ok "NGINX 5xx 0건"
    fi

    section "[Scenario 12] 검증 결과"
    printf "  %-30s %-25s %s\n" "항목" "기대 결과" "실제 결과"
    printf "  %-30s %-25s %s\n" "──────────────────────────────" "─────────────────────────" "──────────"

    local pod_status alb_status cw_status ng_status
    pod_status=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v "Completed" | awk '$2=="1/1"' | wc -l)
    [[ "$all_healthy" == "true" ]] && alb_status="✅ 정상 Pod만 healthy" || alb_status="❌ unhealthy 존재"
    [[ "$cw_count" == "0" ]]       && cw_status="✅ 0건"                  || cw_status="❌ ${cw_count}건"
    [[ "$nginx_count" == "0" ]]    && ng_status="✅ 0건"                  || ng_status="❌ ${nginx_count}건"

    printf "  %-30s %-25s %s\n" "Pod 상태"          "신규 Pod Running"   "✅ Running ${pod_status}개"
    printf "  %-30s %-25s %s\n" "ALB Target Health" "정상 Pod만 healthy" "$alb_status"
    printf "  %-30s %-25s %s\n" "5xx (CloudWatch)"  "0건"                "$cw_status"
    printf "  %-30s %-25s %s\n" "5xx (NGINX 로그)"  "0건"                "$ng_status"
    echo ""
}

pf_all() {
    local target_pod="${1:-}"

    section "Scenario 12 — EKS Pod 장애 전체 실행"
    echo ""
    echo "  단계: 상태확인 → Pod 삭제 → 복구 모니터링 → ALB/5xx 검증"
    echo ""

    pf_check

    echo ""
    [[ -z "$target_pod" ]] && target_pod=$(pf_auto_select_pod)
    warn "삭제 대상: ${target_pod}  (Enter 계속 / Ctrl+C 취소)"
    read -r

    pf_run "$target_pod"
    pf_verify
    ok "Scenario 12 완료"
}

# ══════════════════════════════════════════════════════════════════
# Scenario 13 — 오토스케일링
# ══════════════════════════════════════════════════════════════════

as_get_ready_nodes() {
    kubectl get nodes --no-headers 2>/dev/null \
        | awk '$2=="Ready" {print $1}' \
        || true
}

as_auto_select_node() {
    local best_node="" best_count=-1
    while IFS= read -r node; do
        local count
        count=$(kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
            --field-selector=status.phase=Running 2>/dev/null \
            | grep -v Completed \
            | awk -v n="$node" '$7==n {c++} END {print c+0}') || count=0
        if [[ "$count" -gt "$best_count" ]]; then
            best_count=$count
            best_node=$node
        fi
    done < <(as_get_ready_nodes)
    echo "$best_node"
}

as_get_asg_desired() {
    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].DesiredCapacity" \
        --output text 2>/dev/null || echo "N/A"
}

as_check() {
    section "[Scenario 13] 현재 EKS Node / Pod / ASG 상태"

    step "1/5  Node 목록"
    kubectl get nodes -o wide --no-headers \
        | awk '{printf "  %-50s %-25s %-15s %s\n", $1, $2, $3, $6}' \
        | column -t

    echo ""
    step "2/5  bookflow Pod 배치 현황 (Node별)"
    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v Completed \
        | awk '{printf "  %-45s %-8s %-15s %s\n", $1, $2, $3, $7}' \
        | sort -k4 | column -t

    echo ""
    step "3/5  ASG 상태 (${ASG_NAME})"
    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:length(Instances)}" \
        --output table 2>&1

    step "4/5  Cluster Autoscaler Pod"
    kubectl get pods -n "$CA_NS" -l "$CA_LABEL" --no-headers \
        | awk '{printf "  %-45s %s\n", $1, $2}'

    print_target_health "5/5  ALB Target Health"
}

as_run() {
    local target_node="${1:-}"

    section "[Scenario 13] Step 1 — Node 강제 장애 유발 (drain)"

    if [[ -z "$target_node" ]]; then
        target_node=$(as_auto_select_node)
    fi
    if [[ -z "$target_node" ]]; then
        error "drain할 Ready Node를 찾을 수 없습니다."
        exit 1
    fi

    local pre_desired pre_pod_count
    pre_desired=$(as_get_asg_desired)
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

    section "[Scenario 13] Step 2 — Pending Pod + 신규 Node 프로비저닝 모니터링"
    info "신규 Node Ready + 모든 Pod Running 대기 (최대 ${NODE_TIMEOUT}s)..."
    echo ""

    local elapsed=0 node_ready=false all_pods_ok=false
    local post_desired="$pre_desired"

    while [[ $elapsed -lt $NODE_TIMEOUT ]]; do
        sleep 5
        elapsed=$(( $(date +%s) - t0 ))

        echo -e "  [+${elapsed}s] ── Nodes ──"
        kubectl get nodes --no-headers \
            | awk '{printf "    %-50s %s\n", $1, $2}'

        local cur_desired
        cur_desired=$(as_get_asg_desired)
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

        local ready_count
        ready_count=$(kubectl get nodes --no-headers 2>/dev/null \
            | awk '$2=="Ready" {c++} END {print c+0}')

        if [[ "$ready_count" -ge 2 && "$node_ready" != "true" ]]; then
            local actually_new new_count
            actually_new=$(kubectl get nodes --no-headers 2>/dev/null \
                | awk -v tn="$target_node" '$1!=tn && $2=="Ready" {print $1}')
            new_count=$(echo "$actually_new" | grep -c . || true)
            if [[ "$new_count" -ge 2 ]]; then
                echo ""
                ok "신규 Node Ready 확인 (+${elapsed}s)"
                node_ready=true
            fi
        fi

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

    [[ "$node_ready"   != "true" ]] && warn "신규 Node 프로비저닝 타임아웃 (${NODE_TIMEOUT}s)"
    [[ "$all_pods_ok"  != "true" ]] && warn "Pod 재배치 완료 미확인 — 현재 상태 확인 필요"

    info "drain된 Node 복구 (uncordon)..."
    kubectl uncordon "$target_node"
    ok "uncordon 완료: ${target_node}"
}

as_verify() {
    section "[Scenario 13] Step 3 — Cluster Autoscaler scale-up 이벤트 확인"

    step "CA 상태 configmap"
    kubectl describe configmap cluster-autoscaler-status -n "$CA_NS" 2>/dev/null \
        | grep -A 30 "^Data" \
        | grep -E "(status:|autoscalerStatus|health:|scaleUp:|scaleDown:|cloudProviderTarget|minSize|maxSize|lastScaleUpTime|NoActivity|InProgress|lastTransition)" \
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

    section "[Scenario 13] Step 4 — Node 최종 상태"
    local node_list ready_count total_count
    node_list=$(kubectl get nodes --no-headers)
    ready_count=$(echo "$node_list" | awk '$2=="Ready" {c++} END {print c+0}')
    total_count=$(echo "$node_list" | wc -l)
    echo "$node_list" | awk '{printf "  %-50s %-25s %s\n", $1, $2, $5}' | column -t
    echo ""
    info "Ready: ${ready_count}/${total_count} Nodes"
    local new_node_status
    [[ "$ready_count" -ge 2 ]] && new_node_status="✅ Ready Node ${ready_count}개" \
                                || new_node_status="❌ Ready Node ${ready_count}개 (부족)"

    section "[Scenario 13] Step 5 — bookflow Pod 최종 배치"
    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v Completed \
        | awk '{printf "  %-45s %-8s %-15s %s\n", $1, $2, $3, $7}' \
        | sort -k4 | column -t

    local running_count pending_count
    running_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v Completed | awk '$2=="1/1" || $2~/^[2-9]\/[2-9]$/ {c++} END {print c+0}')
    pending_count=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v Completed | grep -cE "Pending|ContainerCreating" || true)
    local pod_status
    [[ "$pending_count" -eq 0 ]] && pod_status="✅ Running ${running_count}개 / Pending 0개" \
                                  || pod_status="❌ Pending ${pending_count}개 잔존"

    section "[Scenario 13] Step 6 — ASG 최종 상태"
    aws autoscaling describe-auto-scaling-groups \
        --region "$AWS_REGION" \
        --auto-scaling-group-names "$ASG_NAME" \
        --query "AutoScalingGroups[0].{Min:MinSize,Max:MaxSize,Desired:DesiredCapacity,Instances:Instances[].{ID:InstanceId,State:LifecycleState}}" \
        --output json 2>&1 | sed 's/^/  /'

    local asg_desired asg_status
    asg_desired=$(as_get_asg_desired)
    [[ "$asg_desired" -ge 3 ]] && asg_status="✅ Desired ${asg_desired} (scale-up 완료)" \
                                || asg_status="❌ Desired ${asg_desired} (scale-up 미완료)"

    section "[Scenario 13] Step 7 — ALB Target Health 확인"
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
        [[ "$unhealthy" -gt 0 ]] && all_healthy=false
    done
    local alb_status
    [[ "$all_healthy" == "true" ]] && alb_status="✅ 모든 타깃 healthy" || alb_status="❌ unhealthy 타깃 존재"

    section "[Scenario 13] Step 8 — 서비스 5xx 미발생 확인"
    step "CloudWatch ALB 5xx (최근 15분)"
    local cw_count
    cw_count=$(check_5xx_cloudwatch 15)
    echo "  HTTPCode_Target_5XX_Count: ${cw_count}건"
    [[ "$cw_count" == "0" ]] && ok "CloudWatch 5xx 0건" || warn "CloudWatch 5xx ${cw_count}건 감지"

    step "NGINX Ingress 로그 5xx (최근 15분)"
    local nginx_count
    nginx_count=$(check_5xx_nginx "15m")
    echo "  NGINX 5xx 로그: ${nginx_count}건"
    if [[ "$nginx_count" != "0" ]]; then
        warn "NGINX 5xx ${nginx_count}건 감지"
        kubectl logs -n "$INGRESS_NS" -l "$INGRESS_LABEL" --since=15m 2>/dev/null \
            | grep -E 'HTTP/[0-9.]+" 5[0-9]{2} ' | tail -5 | sed 's/^/  /' || true
    else
        ok "NGINX 5xx 0건"
    fi

    local cw_status ng_status
    [[ "$cw_count" == "0" ]]    && cw_status="✅ 0건" || cw_status="❌ ${cw_count}건"
    [[ "$nginx_count" == "0" ]] && ng_status="✅ 0건" || ng_status="❌ ${nginx_count}건"

    section "[Scenario 13] 검증 결과"
    printf "  %-30s %-28s %s\n" "항목" "기대 결과" "실제 결과"
    printf "  %-30s %-28s %s\n" "──────────────────────────────" "────────────────────────────" "──────────"
    printf "  %-30s %-28s %s\n" "Pod 재배치"        "Pending 0개, Running 전체" "$pod_status"
    printf "  %-30s %-28s %s\n" "신규 Node"         "STATUS=Ready Node 추가됨"  "$new_node_status"
    printf "  %-30s %-28s %s\n" "ASG scale-up"      "Desired 증가"              "$asg_status"
    printf "  %-30s %-28s %s\n" "ALB Target Health" "정상 Pod만 healthy"        "$alb_status"
    printf "  %-30s %-28s %s\n" "5xx (CloudWatch)"  "0건"                       "$cw_status"
    printf "  %-30s %-28s %s\n" "5xx (NGINX 로그)"  "0건"                       "$ng_status"
    echo ""
}

as_restore() {
    local target_node="${1:-}"

    section "[Scenario 13] Node Uncordon 원복"

    if [[ -z "$target_node" ]]; then
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

as_all() {
    local target_node="${1:-}"

    section "Scenario 13 — EKS 오토스케일링 전체 실행"
    echo ""
    echo "  단계: 상태확인 → Node drain → 복구 모니터링 → CA/ALB/5xx 검증"
    echo ""

    as_check

    echo ""
    [[ -z "$target_node" ]] && target_node=$(as_auto_select_node)
    warn "drain 대상: ${target_node}  (Enter 계속 / Ctrl+C 취소)"
    read -r

    as_run "$target_node"
    as_verify
    ok "Scenario 13 완료"
}

# ══════════════════════════════════════════════════════════════════
# 메인 디스패처
# ══════════════════════════════════════════════════════════════════
SCENARIO="${1:-all}"
CMD="${2:-all}"
TARGET="${3:-}"

case "$SCENARIO" in
    pod)
        case "$CMD" in
            check)  pf_check ;;
            run)    pf_run "$TARGET" ;;
            verify) pf_verify ;;
            all)    pf_all "$TARGET" ;;
            *)
                echo "사용법: $0 pod [check|run|verify|all] [pod-name]"
                exit 1 ;;
        esac
        ;;
    scale)
        case "$CMD" in
            check)   as_check ;;
            run)     as_run "$TARGET" ;;
            verify)  as_verify ;;
            restore) as_restore "$TARGET" ;;
            all)     as_all "$TARGET" ;;
            *)
                echo "사용법: $0 scale [check|run|verify|restore|all] [node-name]"
                exit 1 ;;
        esac
        ;;
    all)
        section "EKS 통합 테스트 — Scenario 12 + 13 순차 실행"
        echo ""
        echo "  1) Scenario 12: Pod 장애 시나리오"
        echo "  2) Scenario 13: 오토스케일링 시나리오"
        echo ""
        warn "두 시나리오를 순서대로 실행합니다  (Enter 계속 / Ctrl+C 취소)"
        read -r

        pf_all ""
        echo ""
        as_all ""

        section "EKS 통합 테스트 완료 (Scenario 12 + 13)"
        ;;
    *)
        echo ""
        echo "사용법: $0 [pod|scale|all] [command] [target]"
        echo ""
        echo "  pod   [check|run|verify|all]           [pod-name]   Scenario 12: Pod 장애"
        echo "  scale [check|run|verify|restore|all]   [node-name]  Scenario 13: 오토스케일링"
        echo "  all                                                  두 시나리오 순서대로 실행"
        echo ""
        echo "예시:"
        echo "  $0 pod   all                   # Scenario 12 전체 (자동 Pod 선택)"
        echo "  $0 pod   run notification-svc-xxx  # 지정 Pod 삭제"
        echo "  $0 scale all                   # Scenario 13 전체 (자동 Node 선택)"
        echo "  $0 scale restore               # drain된 Node uncordon 원복"
        echo "  $0 all                         # 두 시나리오 통합 실행"
        exit 1
        ;;
esac
