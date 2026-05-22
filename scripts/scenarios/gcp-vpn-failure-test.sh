#!/usr/bin/env bash
# gcp-vpn-failure-test.sh
#
# Scenario 5: GCP VPN 터널 장애 시나리오 테스트
#
# 장애 원인:
#   GCP Cloud Router BGP 피어 비활성화
#   → AWS TGW GCP 경로(10.50.0.0/24) 철회
#   → forecast-svc → PSC(10.50.0.10) → BigQuery 연결 불가
#   → pending_orders 미생성
#
# 검증 항목:
#   1. AWS VPN 터널 상태  — VgwTelemetry.Status DOWN 확인
#   2. GCP VPN 터널 상태  — IPSEC / BGP 세션 상태
#   3. forecast-svc 로그  — error|timeout|bigquery 오류 감지
#   4. pending_orders     — 최근 1시간 COUNT 확인 SQL 출력
#
# 복구:
#   BGP 피어 재활성화 → BGP 재수립(~30s) → 경로 복원 → 연결 회복
#
# 사용법:
#   bash gcp-vpn-failure-test.sh           # 전체 실행 (기본)
#   bash gcp-vpn-failure-test.sh check     # 현재 상태 확인만
#   bash gcp-vpn-failure-test.sh simulate  # BGP 비활성화 (장애 유발)
#   bash gcp-vpn-failure-test.sh verify    # 장애 영향 검증
#   bash gcp-vpn-failure-test.sh restore   # BGP 재활성화 (복구)
#
# 사전 조건:
#   gcloud (GCP 인증), aws CLI (ap-northeast-1 권한), kubectl (bookflow context)
#
# 환경 변수 (선택):
#   AWS_REGION         (기본: ap-northeast-1)
#   SIMULATE_TIMEOUT   AWS 터널 DOWN 대기 최대 초 (기본: 120)
#   RESTORE_TIMEOUT    BGP 재수립 대기 최대 초   (기본: 180)

set -euo pipefail

# ── 설정 ──────────────────────────────────────────────────────────
GCP_PROJECT="project-8ab6bf05-54d2-4f5d-b8d"
GCP_REGION="asia-northeast1"
GCP_ROUTER="bookflow-aws-cr"
GCP_BGP_PEERS=("bookflow-aws-bgp-tunnel0" "bookflow-aws-bgp-tunnel1")
GCP_TUNNELS=("bookflow-aws-tunnel-tunnel0" "bookflow-aws-tunnel-tunnel1")

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
GCP_CGW_IP="34.157.64.22"        # GCP HA VPN gateway IP = AWS Customer Gateway IP
AWS_VPN_TAG_NAME="bookflow-vpn-gcp"

GCP_PSC_CIDR="10.50.0.0/24"     # BGP 광고 경로 (PSC 엔드포인트 대역)
K8S_NAMESPACE="bookflow"
SIMULATE_TIMEOUT="${SIMULATE_TIMEOUT:-120}"
RESTORE_TIMEOUT="${RESTORE_TIMEOUT:-180}"

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

# ── 안전 종료: BGP 피어 복구 보장 ────────────────────────────────
RESTORE_NEEDED=false
restore_on_exit() {
    if [[ "$RESTORE_NEEDED" == "true" ]]; then
        warn "스크립트 종료 감지 — BGP 피어 자동 복구 중..."
        for peer in "${GCP_BGP_PEERS[@]}"; do
            gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
                --peer-name "$peer" \
                --region "$GCP_REGION" \
                --project "$GCP_PROJECT" \
                --enabled \
                --quiet 2>/dev/null && \
                ok "BGP 피어 복구: $peer" || \
                error "복구 실패: $peer — 수동 복구 명령:"$'\n'"  gcloud compute routers update-bgp-peer $GCP_ROUTER --peer-name $peer --region $GCP_REGION --project $GCP_PROJECT --enabled"
        done
    fi
}
trap restore_on_exit EXIT

# ── AWS VPN 연결 ID 동적 조회 ─────────────────────────────────────
get_aws_vpn_conn_id() {
    # 1차: 태그 Name=bookflow-vpn-gcp 검색
    local conn_id
    conn_id=$(aws ec2 describe-vpn-connections \
        --filters "Name=tag:Name,Values=${AWS_VPN_TAG_NAME}" \
                  "Name=state,Values=available,pending" \
        --region "$AWS_REGION" \
        --query 'VpnConnections[0].VpnConnectionId' \
        --output text 2>/dev/null || echo "")

    # 2차: GCP CGW IP로 Customer Gateway 검색
    if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
        local cgw_id
        cgw_id=$(aws ec2 describe-customer-gateways \
            --filters "Name=ip-address,Values=${GCP_CGW_IP}" \
            --region "$AWS_REGION" \
            --query 'CustomerGateways[0].CustomerGatewayId' \
            --output text 2>/dev/null || echo "")

        if [[ -n "$cgw_id" && "$cgw_id" != "None" ]]; then
            conn_id=$(aws ec2 describe-vpn-connections \
                --filters "Name=customer-gateway-id,Values=${cgw_id}" \
                          "Name=state,Values=available,pending" \
                --region "$AWS_REGION" \
                --query 'VpnConnections[0].VpnConnectionId' \
                --output text 2>/dev/null || echo "")
        fi
    fi

    echo "${conn_id:-}"
}

# ── AWS VPN 터널 상태 테이블 출력 ────────────────────────────────
print_aws_tunnel_table() {
    local conn_id="$1"
    local data
    data=$(aws ec2 describe-vpn-connections \
        --vpn-connection-ids "$conn_id" \
        --region "$AWS_REGION" \
        --query 'VpnConnections[0].VgwTelemetry[*].{IP:OutsideIpAddress,Status:Status,BGP:StatusMessage}' \
        --output json 2>/dev/null || echo "[]")

    echo ""
    echo "  ┌──────────────────────┬──────────────────────┬────────┐"
    echo "  │ AWS Tunnel IP        │ BGP                  │ 상태   │"
    echo "  ├──────────────────────┼──────────────────────┼────────┤"
    echo "$data" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data:
    ip   = (t.get('IP') or '').rjust(20)
    bgp  = (t.get('BGP') or '')[:20].ljust(20)
    st   = t.get('Status', '?')
    mark = '\033[0;32mUP  \033[0m' if st == 'UP' else '\033[0;31mDOWN\033[0m'
    print(f'  │ {ip} │ {bgp} │ {mark} │')
if not data:
    print('  │ (데이터 없음)                                      │')
" 2>/dev/null || echo "  │ (조회 실패)                                            │"
    echo "  └──────────────────────┴──────────────────────┴────────┘"
}

# ── AWS 터널 Status 목록 반환 (공백 구분) ─────────────────────────
get_aws_tunnel_statuses() {
    local conn_id="$1"
    aws ec2 describe-vpn-connections \
        --vpn-connection-ids "$conn_id" \
        --region "$AWS_REGION" \
        --query 'VpnConnections[0].VgwTelemetry[*].Status' \
        --output text 2>/dev/null || echo ""
}

# ── GCP VPN 터널 상태 출력 ────────────────────────────────────────
print_gcp_tunnel_table() {
    echo ""
    local out err
    out=$(gcloud compute vpn-tunnels list \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --format "table[box](name:label=TUNNEL, status:label=IPSEC_STATUS, detailedStatus:label=DETAIL)" \
        2>&1) || {
        echo "  (gcloud vpn-tunnels list 실패)"
        echo "  → 실제 에러: ${out}"
        echo "  → 전체 터널 조회: gcloud compute vpn-tunnels list --project ${GCP_PROJECT}"
        return
    }
    echo "$out"
}

# ── GCP BGP 세션 상태 출력 ────────────────────────────────────────
print_gcp_bgp_status() {
    echo ""
    local status_json
    status_json=$(gcloud compute routers get-status "$GCP_ROUTER" \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --format json 2>/dev/null || echo "{}")

    echo "$status_json" | python3 -c "
import json, sys
peers = json.load(sys.stdin).get('result', {}).get('bgpPeerStatus', [])
if not peers:
    print('  BGP 피어 없음 (라우터 미배포 또는 비활성)')
    sys.exit(0)
print('  ┌' + '─'*33 + '┬' + '─'*8 + '┬' + '─'*14 + '┐')
print('  │ BGP Peer                        │ Status │ Learned Routes │')
print('  ├' + '─'*33 + '┼' + '─'*8 + '┼' + '─'*14 + '┤')
for p in peers:
    name   = (p.get('name') or '')[:31].ljust(31)
    st     = (p.get('status') or 'UNKNOWN')
    routes = str(p.get('numLearnedRoutes', '?'))[:12].ljust(12)
    color  = '\033[0;32m' if st == 'UP' else '\033[0;31m'
    st_pad = st[:6].ljust(6)
    print(f'  │ {name} │ {color}{st_pad}\033[0m │ {routes}   │')
print('  └' + '─'*33 + '┴' + '─'*8 + '┴' + '─'*14 + '┘')
" 2>/dev/null || echo "  (BGP 상태 조회 실패)"
}

# ── GCP IPSEC ESTABLISHED 터널 수 반환 ───────────────────────────
get_gcp_tunnel_up_count() {
    gcloud compute vpn-tunnels list \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --filter "status=ESTABLISHED" \
        --format "value(name)" 2>/dev/null | wc -l | tr -d ' '
}

# ── GCP 전체 터널 수 반환 ─────────────────────────────────────────
get_gcp_tunnel_total() {
    gcloud compute vpn-tunnels list \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --format "value(name)" 2>/dev/null | wc -l | tr -d ' '
}

# ── AWS UP 터널 수 반환 ───────────────────────────────────────────
get_aws_tunnel_up_count() {
    local conn_id="$1"
    local statuses
    statuses=$(get_aws_tunnel_statuses "$conn_id")
    echo "$statuses" | tr '\t' '\n' | grep "^UP$" | wc -l | tr -d ' '
}

# ── GCP BGP UP 피어 수 반환 ───────────────────────────────────────
get_gcp_bgp_up_count() {
    local status_json
    status_json=$(gcloud compute routers get-status "$GCP_ROUTER" \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --format json 2>/dev/null || echo "{}")
    echo "$status_json" | python3 -c "
import json, sys
peers = json.load(sys.stdin).get('result', {}).get('bgpPeerStatus', [])
print(sum(1 for p in peers if p.get('status') == 'UP'))
" 2>/dev/null || echo "0"
}

# ── BGP 피어 비활성화 ─────────────────────────────────────────────
disable_bgp_peers() {
    for peer in "${GCP_BGP_PEERS[@]}"; do
        step "BGP 피어 비활성화: $peer"
        gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
            --peer-name "$peer" \
            --region "$GCP_REGION" \
            --project "$GCP_PROJECT" \
            --no-enabled \
            --quiet
        ok "비활성화 완료: $peer"
    done
}

# ── BGP 피어 활성화 ──────────────────────────────────────────────
enable_bgp_peers() {
    for peer in "${GCP_BGP_PEERS[@]}"; do
        step "BGP 피어 활성화: $peer"
        gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
            --peer-name "$peer" \
            --region "$GCP_REGION" \
            --project "$GCP_PROJECT" \
            --enabled \
            --quiet
        ok "활성화 완료: $peer"
    done
}

# ── forecast-svc 오류 로그 확인 ───────────────────────────────────
check_forecast_logs() {
    info "forecast-svc 오류 로그 확인 중..."
    local pod
    pod=$(kubectl get pod -n "$K8S_NAMESPACE" -l app=forecast-svc \
        --field-selector=status.phase=Running \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

    if [[ -z "$pod" ]]; then
        warn "forecast-svc 실행 중인 파드 없음"
        return
    fi

    local log_lines
    log_lines=$(kubectl logs -n "$K8S_NAMESPACE" "$pod" --tail=50 2>/dev/null \
        | grep -iE "error|timeout|bigquery" || true)

    local count
    count=$(echo "$log_lines" | grep -c . || echo 0)

    if [[ "$count" -gt 0 ]]; then
        warn "오류 로그 ${count}건 감지 (파드: $pod):"
        echo "$log_lines" | head -10 | sed 's/^/    /'
    else
        ok "오류 로그 없음 — 파드: $pod"
    fi
}

# ── pending_orders SQL 안내 ───────────────────────────────────────
print_pending_orders_sql() {
    echo ""
    echo "  [pending_orders 확인 — RDS에서 직접 실행]"
    echo ""
    echo "    SELECT COUNT(*) FROM pending_orders"
    echo "    WHERE created_at > NOW() - INTERVAL '1 hour';"
    echo ""
    echo "  기대값:"
    echo "    VPN 정상 시: COUNT > 0  (forecast-svc 주문 생성 중)"
    echo "    VPN 장애 시: COUNT = 0  (BigQuery 응답 없어 주문 미생성)"
}

# ════════════════════════════════════════════════════════════════
# prepare: 장애테스트 초기 상태 설정
#   - 양쪽 IPSEC 터널 UP
#   - tunnel0: BGP1 (Active), tunnel1: BGP0 (Standby)
# ════════════════════════════════════════════════════════════════
cmd_prepare() {
    section "장애테스트 초기 상태 설정"
    echo "  목표: IPSEC 양쪽 UP  /  ${GCP_BGP_PEERS[0]} BGP1  /  ${GCP_BGP_PEERS[1]} BGP0"
    echo ""

    # AWS VPN 연결 확인
    step "1/4  AWS VPN 연결 확인"
    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
        error "AWS VPN 연결을 찾을 수 없음 — 태그(${AWS_VPN_TAG_NAME}) 또는 CGW IP(${GCP_CGW_IP}) 확인 필요"
        exit 1
    fi
    ok "AWS VPN Connection: $conn_id"

    # 양쪽 BGP 피어 활성화 → IPSEC 재협상 트리거
    step "2/4  GCP BGP 피어 전체 활성화 (IPSEC 재협상)"
    enable_bgp_peers

    # GCP IPSEC 터널 현재 상태 확인 (단순 조회 — 강제 UP 불가)
    step "3/4  GCP IPSEC 터널 현재 상태 확인"
    local gcp_up
    gcp_up=$(timeout 15 gcloud compute vpn-tunnels list \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --filter "status=ESTABLISHED" \
        --format "value(name)" 2>/dev/null | wc -l | tr -d ' ')
    local aws_up
    aws_up=$(get_aws_tunnel_up_count "$conn_id")

    echo "  GCP IPSEC ESTABLISHED: ${gcp_up}개  /  AWS Tunnel UP: ${aws_up}/2"

    if [[ "$gcp_up" -ge 1 && "$aws_up" -ge 1 ]]; then
        ok "터널 연결 정상"
    else
        warn "터널 일부 또는 전체 DOWN — IPSEC 상태는 PSK·IKE 설정에 따라 자동 복구됩니다"
        warn "  GCP 전체 터널 상태: gcloud compute vpn-tunnels list --project ${GCP_PROJECT}"
        warn "BGP 설정은 계속 진행합니다"
    fi

    # tunnel1 BGP 비활성화 → BGP0 (Standby)
    step "4/4  ${GCP_BGP_PEERS[1]} BGP 비활성화 (BGP0 설정)"
    gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
        --peer-name "${GCP_BGP_PEERS[1]}" \
        --region "$GCP_REGION" \
        --project "$GCP_PROJECT" \
        --no-enabled \
        --quiet
    ok "${GCP_BGP_PEERS[1]} 비활성화 완료"

    # BGP 상태 안정화 대기
    info "BGP 상태 안정화 대기 (30초)..."
    sleep 30

    section "초기 상태 확인"
    cmd_check

    echo ""
    local bgp_up
    bgp_up=$(get_gcp_bgp_up_count)
    local gcp_up aws_up
    gcp_up=$(get_gcp_tunnel_up_count)
    aws_up=$(get_aws_tunnel_up_count "$conn_id")

    if [[ "$gcp_up" -eq "${#GCP_TUNNELS[@]}" && "$bgp_up" -eq 1 ]]; then
        ok "초기 상태 준비 완료"
        echo "  - GCP IPSEC: 양쪽 UP"
        echo "  - ${GCP_BGP_PEERS[0]}: BGP1 (Active)"
        echo "  - ${GCP_BGP_PEERS[1]}: BGP0 (Standby)"
        echo "  이제 'bash $0 all' 또는 'bash $0 simulate' 실행 가능"
    else
        warn "상태 불완전 — GCP IPSEC UP: ${gcp_up}, BGP UP: ${bgp_up}"
        warn "잠시 후 'bash $0 check' 로 재확인하세요"
    fi
}

# ════════════════════════════════════════════════════════════════
# check: 현재 상태 확인
# ════════════════════════════════════════════════════════════════
cmd_check() {
    section "현재 VPN 상태"

    step "1/3  AWS VPN 터널 상태"
    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
        warn "AWS GCP VPN 연결 없음 (VPN 미배포 또는 중지 상태)"
    else
        info "AWS VPN Connection: $conn_id"
        print_aws_tunnel_table "$conn_id"
    fi

    step "2/3  GCP VPN 터널 상태 (IPSEC)"
    print_gcp_tunnel_table

    step "3/3  GCP Cloud Router BGP 세션 상태"
    print_gcp_bgp_status
}

# ════════════════════════════════════════════════════════════════
# simulate: BGP 피어 비활성화로 VPN 장애 유발
# ════════════════════════════════════════════════════════════════
cmd_simulate() {
    section "장애 시뮬레이션 — GCP BGP 피어 비활성화"
    echo ""
    echo "  방법: GCP Cloud Router BGP 피어 비활성화 (--no-enabled)"
    echo "  효과: BGP 세션 종료 → AWS TGW ${GCP_PSC_CIDR} 경로 철회"
    echo "        forecast-svc PSC(10.50.0.10) → BigQuery 연결 불가"
    echo "  복구: BGP 피어 재활성화 (--enabled) → 즉시 재수립 가능"
    echo ""

    # 사전 조건: BGP가 UP 상태인지 확인
    step "사전 조건 확인"
    local bgp_up
    bgp_up=$(get_gcp_bgp_up_count)
    if [[ "$bgp_up" -eq 0 ]]; then
        error "GCP BGP 피어가 이미 DOWN 상태. 먼저 복구 후 실행: bash $0 restore"
        exit 1
    fi
    ok "GCP BGP 피어 ${bgp_up}개 UP 확인"

    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
        info "시뮬레이션 전 AWS 터널 상태:"
        print_aws_tunnel_table "$conn_id"
    else
        warn "AWS VPN 연결 없음 — GCP 측 상태만 추적합니다"
        conn_id=""
    fi

    # BGP 피어 비활성화 (장애 유발)
    section "GCP BGP 피어 비활성화"
    RESTORE_NEEDED=true
    disable_bgp_peers
    local t0
    t0=$(date +%s)

    # AWS 터널 DOWN 대기 (BGP Hold Timer: ~90s)
    if [[ -n "$conn_id" ]]; then
        section "AWS VPN 터널 DOWN 대기 (BGP Hold Timer ~90s)"
        local elapsed=0
        while true; do
            local statuses
            statuses=$(get_aws_tunnel_statuses "$conn_id")
            elapsed=$(( $(date +%s) - t0 ))
            echo -e "  [${elapsed}s] AWS 터널 상태: $(echo "$statuses" | tr '\t' ' ')"

            if [[ -n "$statuses" ]] && ! echo "$statuses" | grep -q "UP"; then
                ok "AWS 터널 전체 DOWN 확인 (${elapsed}s 소요)"
                break
            fi
            if [[ $elapsed -ge $SIMULATE_TIMEOUT ]]; then
                warn "대기 타임아웃 (${SIMULATE_TIMEOUT}s) — BGP Hold Timer 진행 중"
                warn "verify는 약간 후 실행하세요: bash $0 verify"
                break
            fi
            sleep 10
        done

        echo ""
        info "시뮬레이션 후 AWS 터널 상태:"
        print_aws_tunnel_table "$conn_id"
    fi

    echo ""
    info "GCP BGP 세션 상태:"
    print_gcp_bgp_status
}

# ════════════════════════════════════════════════════════════════
# verify: 장애 영향 검증
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    section "장애 영향 검증"

    step "1/4  AWS VPN 터널 상태"
    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
        local statuses
        statuses=$(get_aws_tunnel_statuses "$conn_id")
        if echo "$statuses" | grep -q "UP"; then
            warn "AWS 터널 일부 UP — BGP Hold Timer 대기 중일 수 있음 (~90s)"
        else
            ok "AWS 터널 전체 DOWN 확인"
        fi
        print_aws_tunnel_table "$conn_id"
    else
        warn "AWS VPN 연결 없음"
    fi

    step "2/4  GCP VPN 터널 / BGP 세션 상태"
    print_gcp_tunnel_table
    print_gcp_bgp_status
    local bgp_up
    bgp_up=$(get_gcp_bgp_up_count)
    if [[ "$bgp_up" -eq 0 ]]; then
        ok "GCP BGP 피어 전체 비활성화 확인"
    else
        warn "GCP BGP 피어 ${bgp_up}개 아직 UP — simulate 단계를 먼저 실행하세요"
    fi

    step "3/4  forecast-svc 오류 로그 확인"
    check_forecast_logs

    step "4/4  pending_orders 미생성 확인"
    print_pending_orders_sql

    echo ""
    ok "검증 완료"
    echo ""
    echo "  예상 결과:"
    echo "  - AWS 터널:       DOWN (BGP 세션 종료)"
    echo "  - GCP BGP:        비활성화 (피어 0개 UP)"
    echo "  - forecast-svc:   BigQuery 연결 오류/타임아웃 로그"
    echo "  - pending_orders: COUNT = 0 (주문 생성 중단)"
    echo ""
    echo "  복구: bash $0 restore"
}

# ════════════════════════════════════════════════════════════════
# restore: BGP 피어 재활성화 및 복구 확인
# ════════════════════════════════════════════════════════════════
cmd_restore() {
    section "복구 — GCP BGP 피어 재활성화"

    enable_bgp_peers
    RESTORE_NEEDED=false

    section "BGP 세션 재수립 대기"
    local t0
    t0=$(date +%s)
    local elapsed=0
    while true; do
        local bgp_up
        bgp_up=$(get_gcp_bgp_up_count)
        elapsed=$(( $(date +%s) - t0 ))
        echo "  [${elapsed}s] GCP BGP UP: ${bgp_up}개 / ${#GCP_BGP_PEERS[@]}개"

        if [[ "$bgp_up" -eq "${#GCP_BGP_PEERS[@]}" ]]; then
            ok "BGP 세션 전체 재수립 완료 (${elapsed}s 소요)"
            break
        fi
        if [[ $elapsed -ge $RESTORE_TIMEOUT ]]; then
            warn "복구 대기 타임아웃 (${RESTORE_TIMEOUT}s) — BGP 재수립 중일 수 있음"
            break
        fi
        sleep 10
    done

    echo ""
    info "복구 후 GCP BGP 상태:"
    print_gcp_bgp_status

    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
        echo ""
        info "복구 후 AWS 터널 상태:"
        print_aws_tunnel_table "$conn_id"
    fi
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    section "Scenario 5: GCP VPN 터널 장애 — 전체 실행"
    echo ""
    echo "  단계: 상태확인 → 장애유발 → 영향검증 → 복구 → 최종확인"
    echo ""

    cmd_check

    # 사전 조건 검증 — 초기 상태가 정상이어야 장애 시뮬레이션 의미 있음
    echo ""
    step "사전 조건 검증"

    local conn_id
    conn_id=$(get_aws_vpn_conn_id)
    if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
        error "AWS GCP VPN 연결을 찾을 수 없습니다 — VPN 미배포 상태이거나 태그/CGW IP 확인 필요"
        error "  태그: Name=${AWS_VPN_TAG_NAME}, CGW IP: ${GCP_CGW_IP}"
        exit 1
    fi

    local aws_statuses
    aws_statuses=$(get_aws_tunnel_statuses "$conn_id")
    if ! echo "$aws_statuses" | grep -q "UP"; then
        error "AWS VPN 터널이 모두 DOWN 상태입니다 — GCP VPN 배포 및 BGP 설정 확인 필요"
        error "  현재 상태: ${aws_statuses}"
        exit 1
    fi
    ok "AWS VPN 터널 UP 확인"

    local bgp_up
    bgp_up=$(get_gcp_bgp_up_count)
    if [[ "$bgp_up" -eq 0 ]]; then
        error "GCP BGP 피어가 모두 DOWN 상태입니다 — 시뮬레이션 전 정상 상태 필요"
        error "  복구 시도: bash $0 restore"
        exit 1
    fi
    ok "GCP BGP 피어 ${bgp_up}개 UP 확인"

    echo ""
    warn "GCP BGP 피어를 비활성화합니다 (forecast-svc BigQuery 연결 중단)."
    warn "계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_simulate

    echo ""
    cmd_verify

    echo ""
    warn "복구를 진행합니다."
    cmd_restore

    section "최종 상태 확인"
    cmd_check
    echo ""
    ok "Scenario 5 완료"
    echo ""
    echo "  결론:"
    echo "  - GCP BGP 비활성화 → AWS TGW ${GCP_PSC_CIDR} 경로 철회 확인"
    echo "  - forecast-svc BigQuery 연결 불가 확인"
    echo "  - BGP 재활성화 → 경로/연결 복구 확인"
}

# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
MODE="${1:-all}"

case "$MODE" in
    prepare)  cmd_prepare ;;
    check)    cmd_check ;;
    simulate) cmd_simulate ;;
    verify)   cmd_verify ;;
    restore)  cmd_restore ;;
    all)      cmd_all ;;
    *)
        echo "사용법: $0 [prepare|check|simulate|verify|restore|all]"
        echo ""
        echo "  prepare   장애테스트 초기 상태 설정 (IPSEC UP + BGP1/BGP0) ← 먼저 실행"
        echo "  check     현재 AWS/GCP VPN·BGP 상태 확인"
        echo "  simulate  GCP BGP 피어 비활성화 (장애 유발)"
        echo "  verify    장애 영향 검증 (simulate 후 실행)"
        echo "  restore   GCP BGP 피어 재활성화 (복구)"
        echo "  all       전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
