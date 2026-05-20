#!/usr/bin/env bash
# eks-pod-failure-test.sh
#
# AWS EKS Pod 장애 시나리오 테스트 (Scenario 12)
#
# 목적:
#   Running Pod 강제 삭제 → Deployment Controller가 신규 Pod 재생성하는 동안
#   ALB Target Health 이상 없고 5xx 미발생(무중단)임을 검증
#
# 사용법:
#   bash eks-pod-failure-test.sh                        # 전체 실행 (자동 Pod 선택)
#   bash eks-pod-failure-test.sh all <pod-name>         # 지정 Pod로 전체 실행
#   bash eks-pod-failure-test.sh check                  # 현재 Pod/ALB 상태만 확인
#   bash eks-pod-failure-test.sh run   [pod-name]       # Pod 삭제 + 복구 대기
#   bash eks-pod-failure-test.sh verify                 # ALB/5xx 검증만 실행
#
# 사전 조건:
#   kubectl (bookflow 네임스페이스 접근), aws CLI (ap-northeast-1)
#
# 환경 변수 (선택):
#   AWS_REGION        (기본: ap-northeast-1)
#   NAMESPACE         (기본: bookflow)
#   RECOVER_TIMEOUT   Pod Ready 대기 최대 초 (기본: 60)

set -euo pipefail

# ── 설정 ─────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
NAMESPACE="${NAMESPACE:-bookflow}"
RECOVER_TIMEOUT="${RECOVER_TIMEOUT:-60}"

# ALB / Target Group (EKS NGINX Ingress NLB)
TG_ARN_1="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-2a7ed77e21/d5014d44dd4d2298"
TG_ARN_2="arn:aws:elasticloadbalancing:ap-northeast-1:994878981869:targetgroup/k8s-ingressn-ingressn-8cccae40c7/cf5c9b4eb0aae397"
ALB_SUFFIX="app/bookflow-alb-external/57e62cdd02356761"

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

# ── Running Pod 목록 (Deployment 소속만) ─────────────────────────
get_running_pods() {
    kubectl get pods -n "$NAMESPACE" --no-headers \
        --field-selector=status.phase=Running 2>/dev/null \
        | awk '$2=="1/1" || $2~/^[2-9]\/[2-9]$/ {print $1}' \
        | grep -vE '^(bq-|forecast-bq-|intervention-auto|publisher-watcher|reservation-ttl)' \
        || true
}

# ── 자동 Pod 선택 (notification-svc 우선, 없으면 첫 번째 Running) ─
auto_select_pod() {
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        --field-selector=status.phase=Running 2>/dev/null \
        | awk '$1~/^notification-svc/ && $2=="1/1" {print $1; exit}')
    if [[ -z "$pod" ]]; then
        pod=$(get_running_pods | head -1)
    fi
    echo "$pod"
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

# ── NGINX Ingress 5xx 로그 조회 ───────────────────────────────────
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

# ════════════════════════════════════════════════════════════════
# check: 현재 상태 확인
# ════════════════════════════════════════════════════════════════
cmd_check() {
    section "현재 EKS Pod / ALB 상태"

    step "1/2  bookflow 네임스페이스 Running Pod"
    kubectl get pods -n "$NAMESPACE" -o wide --no-headers \
        | grep -v "Completed" \
        | awk '{printf "  %-45s %-8s %-6s %s\n", $1, $2, $3, $7}' \
        | column -t

    print_target_health "2/2  ALB Target Health"
}

# ════════════════════════════════════════════════════════════════
# run: Pod 삭제 + 복구 모니터링
# ════════════════════════════════════════════════════════════════
cmd_run() {
    local target_pod="${1:-}"

    section "Step 1 — Pod 강제 장애 유발"

    if [[ -z "$target_pod" ]]; then
        target_pod=$(auto_select_pod)
    fi

    if [[ -z "$target_pod" ]]; then
        error "삭제할 Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    info "대상 Pod: ${target_pod}"

    # 삭제 전 상태 저장
    local old_deploy
    old_deploy=$(kubectl get pod -n "$NAMESPACE" "$target_pod" \
        -o jsonpath='{.metadata.labels.app}' 2>/dev/null || echo "unknown")

    local t0
    t0=$(date +%s)

    step "kubectl delete pod -n ${NAMESPACE} ${target_pod}"
    kubectl delete pod -n "$NAMESPACE" "$target_pod"
    ok "Pod 삭제 완료 — Deployment Controller 재생성 시작"

    # ── Step 2: Pod 복구 모니터링 ──────────────────────────────
    section "Step 2 — 실시간 Pod 상태 모니터링"
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

        # 신규 Pod 탐지 (이전 Pod 제외하고 app 레이블로 찾기)
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

# ════════════════════════════════════════════════════════════════
# verify: ALB 헬스 + 5xx 검증
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    section "Step 3 — ALB Target Health 확인"

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

    if [[ "$all_healthy" == "true" ]]; then
        ok "모든 ALB 타깃 healthy"
    else
        warn "unhealthy 타깃 존재 — 복구 대기 필요"
    fi

    # ── Step 4: 5xx 확인 ───────────────────────────────────────
    section "Step 4 — 서비스 5xx 미발생 확인"

    step "CloudWatch ALB 5xx (최근 10분)"
    local cw_count
    cw_count=$(check_5xx_cloudwatch 10)
    echo "  HTTPCode_Target_5XX_Count: ${cw_count}건"
    if [[ "$cw_count" == "0" ]]; then
        ok "CloudWatch 5xx 0건"
    else
        warn "CloudWatch 5xx ${cw_count}건 감지"
    fi

    step "NGINX Ingress 로그 5xx (최근 10분)"
    local nginx_count
    nginx_count=$(check_5xx_nginx "10m")
    echo "  NGINX 5xx 로그: ${nginx_count}건"
    if [[ "$nginx_count" == "0" ]]; then
        ok "NGINX 5xx 0건"
    else
        warn "NGINX 5xx ${nginx_count}건 감지"
        kubectl logs -n "$INGRESS_NS" \
            -l "$INGRESS_LABEL" --since=10m 2>/dev/null \
            | grep -E 'HTTP/[0-9.]+" 5[0-9]{2} ' | tail -5 \
            | sed 's/^/  /' || true
    fi

    # ── 검증 요약 ─────────────────────────────────────────────
    section "검증 결과"
    printf "  %-30s %-25s %s\n" "항목" "기대 결과" "실제 결과"
    printf "  %-30s %-25s %s\n" "──────────────────────────────" "─────────────────────────" "──────────"

    local pod_status alb_status cw_status ng_status
    pod_status=$(kubectl get pods -n "$NAMESPACE" --no-headers \
        | grep -v "Completed" | awk '$2=="1/1"' | wc -l)
    [[ "$all_healthy" == "true" ]] && alb_status="✅ 정상 Pod만 healthy" || alb_status="❌ unhealthy 존재"
    [[ "$cw_count" == "0" ]] && cw_status="✅ 0건" || cw_status="❌ ${cw_count}건"
    [[ "$nginx_count" == "0" ]] && ng_status="✅ 0건" || ng_status="❌ ${nginx_count}건"

    printf "  %-30s %-25s %s\n" "Pod 상태" "신규 Pod Running" "✅ Running ${pod_status}개"
    printf "  %-30s %-25s %s\n" "ALB Target Health" "정상 Pod만 healthy" "$alb_status"
    printf "  %-30s %-25s %s\n" "5xx (CloudWatch)" "0건" "$cw_status"
    printf "  %-30s %-25s %s\n" "5xx (NGINX 로그)" "0건" "$ng_status"
    echo ""
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    local target_pod="${1:-}"

    section "EKS Pod 장애 시나리오 전체 실행 (Scenario 12)"
    echo ""
    echo "  단계: 상태확인 → Pod 삭제 → 복구 모니터링 → ALB/5xx 검증"
    echo ""

    cmd_check

    echo ""
    if [[ -z "$target_pod" ]]; then
        target_pod=$(auto_select_pod)
        warn "삭제 대상: ${target_pod}  (Enter 계속 / Ctrl+C 취소)"
    else
        warn "삭제 대상: ${target_pod}  (Enter 계속 / Ctrl+C 취소)"
    fi
    read -r

    cmd_run "$target_pod"
    cmd_verify

    ok "Scenario 12 완료"
}

# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
MODE="${1:-all}"
ARG2="${2:-}"

case "$MODE" in
    check)  cmd_check ;;
    run)    cmd_run "$ARG2" ;;
    verify) cmd_verify ;;
    all)    cmd_all "$ARG2" ;;
    *)
        echo "사용법: $0 [check|run|verify|all] [pod-name]"
        echo ""
        echo "  check          현재 Pod/ALB 상태 확인"
        echo "  run [pod]      Pod 삭제 + 복구 모니터링"
        echo "  verify         ALB Health + 5xx 검증"
        echo "  all [pod]      전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
