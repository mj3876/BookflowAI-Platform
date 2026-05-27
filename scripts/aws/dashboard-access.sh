#!/usr/bin/env bash
# dashboard-access.sh — BookFlow 대시보드 PC 접속 헬퍼
#
# 수행 순서:
#   1. .ovpn 파일 확인 (없으면 setup-vpn.sh 실행 안내)
#   2. Client VPN 연결 상태 확인 → 미연결 시 OpenVPN daemon 시작
#   3. EKS kubeconfig 업데이트 (kubectl 사용 가능)
#   4. 브라우저로 https://bookflow.myosoon.store 오픈
#
# 전제조건:
#   - AWS CLI 설치 + bookflow-deploy 프로파일 인증 완료
#   - OpenVPN 설치 (AWS VPN Client 또는 OpenVPN GUI)
#   - ~/.bookflow-vpn/bookflow-client-*.ovpn 파일 존재
#     (없으면: bash scripts/setup-vpn.sh 실행)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_OVPN="$SCRIPT_DIR/../bookflow-client-desktop-1.ovpn"

REGION="ap-northeast-1"
CLUSTER_NAME="bookflow-eks"
DASHBOARD_URL="https://bookflow.myosoon.store"
VPN_DIR="${HOME}/.bookflow-vpn"
VPN_DNS="10.0.0.2"
VPN_INTERNAL_IP="10.0.0.2"  # VPC DNS — VPN 연결 확인용 ping 대상

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
step()  { echo ""; echo -e "${CYAN}══════════════════════════════════════${NC}"; echo -e "${CYAN}  $*${NC}"; echo -e "${CYAN}══════════════════════════════════════${NC}"; }

echo ""
echo "=================================================="
echo "  BookFlow 대시보드 접속 스크립트"
echo "  $DASHBOARD_URL"
echo "=================================================="

# ── 1. .ovpn 파일 확인 ────────────────────────────────────────────────────
step "1/3 VPN 인증서 확인"

OVPN_FILE=""
if [[ -d "$VPN_DIR" ]]; then
  OVPN_FILE=$(ls "$VPN_DIR"/bookflow-client-*.ovpn 2>/dev/null | head -1 || echo "")
fi

# 레포에 소스 .ovpn 이 있으면 ~/.bookflow-vpn/ 으로 자동 복사
if [[ -z "$OVPN_FILE" && -f "$REPO_OVPN" ]]; then
  mkdir -p "$VPN_DIR"
  chmod 700 "$VPN_DIR"
  DST="$VPN_DIR/$(basename "$REPO_OVPN")"
  cp "$REPO_OVPN" "$DST"
  chmod 600 "$DST"
  OVPN_FILE="$DST"
  info "레포 .ovpn 복사: $OVPN_FILE"
fi

if [[ -z "$OVPN_FILE" ]]; then
  error "VPN 인증서(.ovpn)가 없습니다.\n\n  먼저 실행하세요:\n    bash scripts/setup-vpn.sh\n\n  생성 위치: ${VPN_DIR}/bookflow-client-<이름>.ovpn"
fi
info "인증서 확인: $OVPN_FILE"

# CFN output 에서 현재 endpoint 조회 → remote 줄 자동 갱신
_ENDPOINT_ID=$(aws cloudformation describe-stacks \
  --stack-name "bookflow-60-client-vpn" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ClientVpnEndpointId'].OutputValue" \
  --output text 2>/dev/null || echo "")
if [[ -n "$_ENDPOINT_ID" ]]; then
  _EXPECTED_REMOTE="${_ENDPOINT_ID}.prod.clientvpn.${REGION}.amazonaws.com"
  _CURRENT_REMOTE=$(awk '/^remote /{print $2; exit}' "$OVPN_FILE" || echo "")
  if [[ "$_CURRENT_REMOTE" != "$_EXPECTED_REMOTE" ]]; then
    info "endpoint 갱신: ${_CURRENT_REMOTE:-없음} → ${_EXPECTED_REMOTE}"
    _TMP=$(mktemp)
    sed "s|remote cvpn-endpoint-[^ ]*|remote ${_EXPECTED_REMOTE}|" "$OVPN_FILE" > "$_TMP"
    mv "$_TMP" "$OVPN_FILE"
    chmod 600 "$OVPN_FILE"
  else
    info "endpoint 최신 상태 (${_ENDPOINT_ID})"
  fi
else
  warn "Client VPN endpoint 조회 실패 — .ovpn 기존 endpoint 유지"
fi

# ── 2. Client VPN 연결 확인 / 연결 시도 ─────────────────────────────────
step "2/3 Client VPN 연결"

# VPN 연결 감지: Windows 라우팅 테이블에 VPC CIDR(10.0.0.0) 경로 존재 여부 확인
_vpn_connected() {
  # Windows(Git Bash): route print 에 10.0.0.0 경로가 있으면 연결됨
  if route print 2>/dev/null | grep -q "10\.0\.0\.0"; then return 0; fi
  # Linux/macOS: ip route 또는 netstat
  if ip route show 10.0.0.0/16 2>/dev/null | grep -q .; then return 0; fi
  if netstat -rn 2>/dev/null | grep -q "10\.0\.0\.0"; then return 0; fi
  return 1
}

if [[ "${BOOKFLOW_SKIP_VPN_CHECK:-0}" == "1" ]]; then
  info "Client VPN 연결 확인 skip (BOOKFLOW_SKIP_VPN_CHECK=1)"
elif _vpn_connected; then
  info "Client VPN 이미 연결됨 (10.0.0.2 도달 확인)"
else
  info "VPN 미연결 — OpenVPN 연결 시도..."

  OPENVPN_BIN=""
  for _p in \
    "/c/Program Files/OpenVPN/bin/openvpn.exe" \
    "/c/Program Files (x86)/OpenVPN/bin/openvpn.exe" \
    "/c/Program Files/AWS VPN Client/openvpn.exe"; do
    [[ -f "$_p" ]] && { OPENVPN_BIN="$_p"; break; }
  done

  if [[ -z "$OPENVPN_BIN" ]]; then
    echo ""
    echo "  ┌─ 수동 연결 필요 ─────────────────────────────────────────────┐"
    echo "  │  OpenVPN 실행 파일을 찾을 수 없습니다.                       │"
    echo "  │                                                              │"
    echo "  │  옵션 A: AWS VPN Client 설치 후 .ovpn 프로파일 등록        │"
    echo "  │    https://aws.amazon.com/vpn/client-vpn-download/          │"
    echo "  │    파일: $OVPN_FILE"
    echo "  │                                                              │"
    echo "  │  옵션 B: OpenVPN GUI 설치 후 연결                           │"
    echo "  │    https://openvpn.net/community-downloads/                 │"
    echo "  │                                                              │"
    echo "  │  연결 후 이 스크립트를 다시 실행하세요.                      │"
    echo "  └──────────────────────────────────────────────────────────────┘"
    echo ""
    exit 0
  fi

  VPN_LOG="${HOME}/.bookflow-vpn/vpn-$(date +%Y-%m-%d).log"
  "$OPENVPN_BIN" \
    --config "$(cygpath -w "$OVPN_FILE")" \
    --daemon \
    --log "$(cygpath -w "$VPN_LOG")" \
    2>/dev/null || true

  info "OpenVPN daemon 시작 — 연결 대기 중 (최대 60s)..."
  WAITED=0
  VPN_OK=0
  while [[ $WAITED -lt 60 ]]; do
    sleep 5; WAITED=$((WAITED + 5))
    if _vpn_connected; then
      info "Client VPN 연결됨 (${WAITED}s)"
      VPN_OK=1
      break
    fi
    echo "  [${WAITED}s] 대기 중..."
  done

  if [[ $VPN_OK -eq 0 ]]; then
    echo ""
    warn "VPN 연결 타임아웃 (60s). 수동으로 연결 후 다시 시도하세요."
    warn "  로그: $VPN_LOG"
    echo ""
    echo "  수동 연결 후 대시보드 URL: $DASHBOARD_URL"
    echo ""
    exit 1
  fi
fi

# ── 3. EKS kubeconfig 업데이트 ───────────────────────────────────────────
step "3/3 EKS kubeconfig + 브라우저 오픈"

AWS_PROFILE="${AWS_PROFILE:-bookflow-deploy}"
if aws sts get-caller-identity --profile "$AWS_PROFILE" --region "$REGION" >/dev/null 2>&1; then
  info "EKS kubeconfig 업데이트 ($CLUSTER_NAME)..."
  aws eks update-kubeconfig \
    --name "$CLUSTER_NAME" \
    --region "$REGION" \
    --profile "$AWS_PROFILE" \
    >/dev/null 2>&1 && info "kubectl 사용 가능" || warn "kubeconfig 업데이트 실패 (kubectl 사용 불가)"
else
  warn "AWS 인증 실패 — kubectl 업데이트 skip (대시보드 브라우저 접속은 가능)"
fi

# ── 브라우저 오픈 ────────────────────────────────────────────────────────
info "브라우저 오픈: $DASHBOARD_URL"
if command -v cygpath >/dev/null 2>&1; then
  # Git Bash / Windows 환경
  start "" "$DASHBOARD_URL" 2>/dev/null || true
elif [[ "$OSTYPE" == "darwin"* ]]; then
  open "$DASHBOARD_URL"
else
  xdg-open "$DASHBOARD_URL" 2>/dev/null || true
fi

echo ""
echo "══════════════════════════════════════════════════"
echo -e "  ${GREEN}대시보드 접속 준비 완료${NC}"
echo "══════════════════════════════════════════════════"
echo ""
echo "  URL     : $DASHBOARD_URL"
echo "  Grafana : $DASHBOARD_URL/grafana"
echo ""
echo "  접속 안 되면 확인사항:"
echo "    1. VPN 연결 상태 (DNS: nslookup oauth2.googleapis.com → $VPN_DNS)"
echo "    2. EKS 파드 상태: kubectl get pods -n bookflow"
echo ""
