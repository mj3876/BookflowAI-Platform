#!/usr/bin/env bash
# setup-vpn.sh — BookFlow Client VPN 개인 인증서 발급 + .ovpn 생성
#
# 사용법: bash scripts/setup-vpn.sh
#
# 전제조건:
#   - aws CLI 설치 + ap-northeast-1 접근 권한 (SSO 또는 Access Key)
#   - openssl 설치 (macOS 기본 포함 / Linux 기본 포함 / Windows: Git Bash 또는 WSL)

set -euo pipefail

REGION="ap-northeast-1"
SECRET_NAME="bookflow/vpn/ca"
VPN_ENDPOINT="${VPN_ENDPOINT:-}"
OUTPUT_DIR="${HOME}/.bookflow-vpn"

# ── 색상 출력 ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 전제조건 확인 ──────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  BookFlow Client VPN 설정 스크립트"
echo "=================================================="
echo ""

info "전제조건 확인 중..."

command -v openssl >/dev/null 2>&1 || error "openssl이 설치되지 않았습니다.\n  macOS: brew install openssl\n  Ubuntu: sudo apt install openssl"
command -v aws    >/dev/null 2>&1 || error "AWS CLI가 설치되지 않았습니다.\n  https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html"

# python3 또는 python 감지 (Windows Store stub은 실행해도 실패하므로 실제 동작 여부 확인)
PYTHON=""
set +e
python3 -c "import json,sys" >/dev/null 2>&1 && PYTHON=python3
[[ -z "$PYTHON" ]] && python -c "import json,sys" >/dev/null 2>&1 && PYTHON=python
set -e
[[ -z "$PYTHON" ]] && error "Python이 설치되지 않았습니다."

aws sts get-caller-identity --region "$REGION" >/dev/null 2>&1 \
  || error "AWS 인증 실패. 'aws configure' 또는 SSO 로그인을 먼저 완료하세요."

info "AWS 인증 확인 완료"

if [[ -z "$VPN_ENDPOINT" ]]; then
    ENDPOINT_ID=$(aws cloudformation list-exports \
        --region "$REGION" \
        --query "Exports[?Name=='bookflow-client-vpn-endpoint-id'].Value | [0]" \
        --output text 2>/dev/null || true)
    if [[ -z "$ENDPOINT_ID" || "$ENDPOINT_ID" == "None" ]]; then
        ENDPOINT_ID=$(aws ec2 describe-client-vpn-endpoints \
            --region "$REGION" \
            --filters "Name=tag:Name,Values=bookflow-client-vpn" \
            --query "sort_by(ClientVpnEndpoints[?Status.Code=='available'], &CreationTime)[-1].ClientVpnEndpointId" \
            --output text)
    fi
    [[ -n "$ENDPOINT_ID" && "$ENDPOINT_ID" != "None" ]] \
      || error "Client VPN endpoint 조회 실패. 먼저 AWS Client VPN을 배포하세요."
    VPN_ENDPOINT="${ENDPOINT_ID}.prod.clientvpn.${REGION}.amazonaws.com"
fi
info "Client VPN endpoint: ${VPN_ENDPOINT}"

# ── 이름 입력 ──────────────────────────────────────────────────────────
echo ""
read -rp "이름을 입력하세요 (예: minji, yeongheon): " NAME
NAME="${NAME// /-}"  # 공백 → 하이픈
NAME="${NAME,,}"     # 소문자

if [[ -z "$NAME" ]]; then
    error "이름이 비어있습니다."
fi

CN="bookflow-${NAME}"
OVPN_FILE="${OUTPUT_DIR}/bookflow-client-${NAME}.ovpn"

if [[ -f "$OVPN_FILE" ]]; then
    warn "이미 존재합니다: $OVPN_FILE"
    read -rp "덮어쓰시겠습니까? (y/N): " OVERWRITE
    [[ "${OVERWRITE,,}" == "y" ]] || { info "취소됨."; exit 0; }
fi

mkdir -p "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"

# ── CA 가져오기 (Secrets Manager) ──────────────────────────────────────
echo ""
info "CA 인증서를 Secrets Manager에서 가져오는 중..."

SECRET_JSON=$(aws secretsmanager get-secret-value \
    --secret-id "$SECRET_NAME" \
    --region "$REGION" \
    --query SecretString \
    --output text) \
  || error "CA 시크릿 조회 실패. IAM 권한을 확인하세요: secretsmanager:GetSecretValue on ${SECRET_NAME}"

CA_CRT=$(echo "$SECRET_JSON" | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['ca_crt'])")
CA_KEY=$(echo "$SECRET_JSON" | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['ca_key'])")

CA_CRT_FILE="${OUTPUT_DIR}/ca.crt"
CA_KEY_FILE="${OUTPUT_DIR}/ca.key"
CA_SRL_FILE="${OUTPUT_DIR}/ca.srl"

echo "$CA_CRT" > "$CA_CRT_FILE"
echo "$CA_KEY" > "$CA_KEY_FILE"
chmod 600 "$CA_KEY_FILE"

# ca.srl 초기화 (없으면 생성)
if [[ ! -f "$CA_SRL_FILE" ]]; then
    openssl x509 -in "$CA_CRT_FILE" -noout -serial 2>/dev/null \
      | sed 's/serial=//' > "$CA_SRL_FILE"
fi

info "CA 로드 완료"

# ── 인증서 발급 ────────────────────────────────────────────────────────
echo ""
info "[1/4] RSA 2048 키 생성..."
KEY_FILE="${OUTPUT_DIR}/${NAME}.key"
CSR_FILE="${OUTPUT_DIR}/${NAME}.csr"
CRT_FILE="${OUTPUT_DIR}/${NAME}.crt"

openssl genrsa -out "$KEY_FILE" 2048 2>/dev/null
chmod 600 "$KEY_FILE"

info "[2/4] CSR 생성..."
REQ_CNF="${OUTPUT_DIR}/${NAME}-req.cnf"
cat > "$REQ_CNF" << EOF
[req]
distinguished_name = dn
prompt = no

[dn]
CN = ${CN}
EOF
openssl req -new -key "$KEY_FILE" -out "$CSR_FILE" -config "$REQ_CNF" 2>/dev/null
rm -f "$REQ_CNF"

info "[3/4] CA 서명 (유효기간 10년)..."
EXT_CNF="${OUTPUT_DIR}/client.ext"
cat > "$EXT_CNF" << 'EOF'
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = clientAuth
EOF
openssl x509 -req \
    -in "$CSR_FILE" \
    -CA "$CA_CRT_FILE" \
    -CAkey "$CA_KEY_FILE" \
    -CAserial "$CA_SRL_FILE" \
    -out "$CRT_FILE" \
    -days 3650 \
    -extfile "$EXT_CNF" \
    2>/dev/null
rm -f "$CSR_FILE" "$EXT_CNF"

info "[4/4] .ovpn 파일 생성..."
{
cat << EOF
client
dev tun
proto udp
remote ${VPN_ENDPOINT} 443
remote-random-hostname
resolv-retry infinite
nobind
remote-cert-tls server
cipher AES-256-GCM
verb 3
<ca>
$(cat "$CA_CRT_FILE")
</ca>
reneg-sec 0
verify-x509-name server.bookflow-client-vpn.local name
<cert>
$(openssl x509 -in "$CRT_FILE")
</cert>
<key>
$(cat "$KEY_FILE")
</key>
EOF
} > "$OVPN_FILE"
chmod 600 "$OVPN_FILE"

# CA key 삭제 (로컬에 남기지 않음)
rm -f "$CA_KEY_FILE"

# ── 완료 ──────────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "  ${GREEN}발급 완료!${NC}"
echo "=================================================="
echo ""
info "생성된 파일: ${OVPN_FILE}"
echo ""
echo "  ┌─ 다음 단계 ──────────────────────────────────────────────────┐"
echo "  │                                                              │"
echo "  │  1. AWS VPN Client 설치                                     │"
echo "  │     https://aws.amazon.com/vpn/client-vpn-download/         │"
echo "  │                                                              │"
echo "  │  2. VPN Client 실행 → File > Manage Profiles                │"
echo "  │     → Add Profile → .ovpn 파일 선택                         │"
echo "  │     파일 위치: ${OVPN_FILE}"
echo "  │                                                              │"
echo "  │  3. Connect 클릭 → 접속 완료                                │"
echo "  │     접속 후 내부망: 10.0.0.0/16                              │"
echo "  │                                                              │"
echo "  └──────────────────────────────────────────────────────────────┘"
echo ""
warn "${NAME}.key, ${NAME}.crt 파일은 ${OUTPUT_DIR}/ 에 보관됩니다."
warn "이 디렉터리를 외부에 공유하지 마세요."
echo ""
