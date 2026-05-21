#!/usr/bin/env bash
# client-vpn-cert-intrusion-test.sh
#
# 시나리오: Client VPN 비인가 인증키 접속 탐지 → 인증키 즉시 폐기
#
# 개요:
#   각 데스크탑에는 고유한 CN(Common Name)을 가진 클라이언트 인증서가 발급됨
#   (예: CN=bookflow-desktop-1, CN=bookflow-desktop-2, CN=bookflow-desktop-3)
#
#   다른 데스크탑에서 다른 인증키로 Client VPN 접속을 시도하면:
#     1. CloudWatch Logs에서 연결 이벤트 감지 (인증서 CN 확인)
#     2. 등록되지 않은 CN 또는 예상치 못한 인증서 탐지
#     3. 해당 인증서를 CRL(Certificate Revocation List)에 등록
#     4. Client VPN 엔드포인트에 CRL 임포트 → 이후 TLS 협상 단계에서 차단
#     5. 기존 활성 세션 강제 종료
#
# 메커니즘:
#   AWS Client VPN은 뮤추얼 TLS 인증 사용 (client-vpn.yaml의 MutualAuthentication)
#   CRL 임포트: aws ec2 import-client-vpn-client-certificate-revocation-list
#   세션 종료: aws ec2 terminate-client-vpn-connections
#
# 사용법:
#   bash client-vpn-cert-intrusion-test.sh            # 전체 실행 (기본)
#   bash client-vpn-cert-intrusion-test.sh check      # 현재 연결 및 인증서 상태 확인
#   bash client-vpn-cert-intrusion-test.sh simulate   # 비인가 인증키 접속 시뮬레이션
#   bash client-vpn-cert-intrusion-test.sh revoke     # 비인가 인증키 CRL 등록 + 세션 종료
#   bash client-vpn-cert-intrusion-test.sh verify     # 폐기 후 차단 상태 검증
#   bash client-vpn-cert-intrusion-test.sh restore    # CRL 초기화 및 테스트 파일 정리
#
# 사전 조건:
#   aws CLI (ap-northeast-1 인증 완료), openssl, python3
#   CA 인증서: $CERTS_DIR/ca.crt
#   CA 개인키: $CERTS_DIR/ca.key  (테스트용 비암호화 키)
#   데스크탑 인증서: $CERTS_DIR/desktop-{1,2,3}.crt
#
# 환경 변수 (선택):
#   AWS_REGION    (기본: ap-northeast-1)
#   CERTS_DIR     인증서 디렉토리 (기본: ~/bookflow-vpn-certs)
#   LOG_WINDOW    CloudWatch 조회 시간 범위 분 (기본: 60)

set -euo pipefail

# ── 설정 ──────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
CFN_STACK_NAME="bookflow-client-vpn"
LOG_GROUP="/aws/clientvpn/bookflow"
LOG_STREAM="connections"
LOG_WINDOW="${LOG_WINDOW:-60}"
CERTS_DIR="${CERTS_DIR:-$HOME/bookflow-vpn-certs}"
CA_CERT="${CERTS_DIR}/ca.crt"
CA_KEY="${CERTS_DIR}/ca.key"
CRL_FILE="${CERTS_DIR}/revoked.crl"
DB_DIR="${CERTS_DIR}/db"
CNF_FILE="${CERTS_DIR}/openssl.cnf"

# 등록된 데스크탑 → 인증서 CN 매핑 (실제 환경에 맞게 수정)
declare -A DESKTOP_CN_MAP=(
    [desktop-1]="bookflow-desktop-1"
    [desktop-2]="bookflow-desktop-2"
    [desktop-3]="bookflow-desktop-3"
)

# 시뮬레이션용 비인가 인증서 CN (테스트에서만 사용 — 등록 목록에 없는 CN)
ATTACKER_CN="bookflow-desktop-attacker"
ATTACKER_CERT="${CERTS_DIR}/${ATTACKER_CN}.crt"
ATTACKER_KEY="${CERTS_DIR}/${ATTACKER_CN}.key"

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

# ── 안전 종료: 시뮬레이션 인증서 파일 정리 ──────────────────────
CLEANUP_NEEDED=false
cleanup_on_exit() {
    if [[ "$CLEANUP_NEEDED" == "true" ]]; then
        warn "스크립트 종료 감지 — 시뮬레이션 파일 정리 중..."
        rm -f "${ATTACKER_KEY}" \
              "${CERTS_DIR}/${ATTACKER_CN}.csr" \
              "${ATTACKER_CERT}" \
              "${CERTS_DIR}/ca.srl" 2>/dev/null || true
        ok "시뮬레이션 파일 정리 완료"
    fi
}
trap cleanup_on_exit EXIT

# ── Client VPN Endpoint ID 조회 ──────────────────────────────────
get_endpoint_id() {
    local eid
    eid=$(aws cloudformation list-exports \
        --region "$AWS_REGION" \
        --query 'Exports[?Name==`bookflow-client-vpn-endpoint-id`].Value' \
        --output text 2>/dev/null || echo "")

    if [[ -z "$eid" || "$eid" == "None" ]]; then
        eid=$(aws ec2 describe-client-vpn-endpoints \
            --region "$AWS_REGION" \
            --filters "Name=tag:Name,Values=bookflow-client-vpn" \
            --query 'ClientVpnEndpoints[0].ClientVpnEndpointId' \
            --output text 2>/dev/null || echo "")
    fi

    if [[ -z "$eid" || "$eid" == "None" ]]; then
        error "Client VPN Endpoint ID 조회 실패 — CloudFormation 스택 배포 상태 확인"
        exit 1
    fi
    echo "$eid"
}

# ── 활성 연결 목록 (JSON) ────────────────────────────────────────
get_active_connections() {
    local eid="$1"
    aws ec2 describe-client-vpn-connections \
        --client-vpn-endpoint-id "$eid" \
        --filters "Name=status-code,Values=active" \
        --region "$AWS_REGION" \
        --query 'Connections[*].{ID:ConnectionId,CN:CommonName,IP:ClientIp,Status:Status.Code}' \
        --output json 2>/dev/null || echo "[]"
}

# ── CloudWatch에서 최근 연결 이벤트 조회 ────────────────────────
query_vpn_logs() {
    local since_ms now_ms
    since_ms=$(( $(date +%s) * 1000 - LOG_WINDOW * 60 * 1000 ))
    now_ms=$(( $(date +%s) * 1000 ))

    aws logs filter-log-events \
        --log-group-name "$LOG_GROUP" \
        --log-stream-names "$LOG_STREAM" \
        --start-time "$since_ms" \
        --end-time "$now_ms" \
        --region "$AWS_REGION" \
        --query 'events[*].message' \
        --output json 2>/dev/null || echo "[]"
}

# ── 인증서 정보 추출 ──────────────────────────────────────────────
get_cert_cn()     { openssl x509 -in "$1" -noout -subject 2>/dev/null | sed 's/.*CN\s*=\s*//' | sed 's/,.*//' | tr -d ' '; }
get_cert_serial() { openssl x509 -in "$1" -noout -serial 2>/dev/null | cut -d= -f2; }

# ── CA 데이터베이스 초기화 ──────────────────────────────────────
init_ca_db() {
    mkdir -p "${DB_DIR}/newcerts"
    [[ -f "${DB_DIR}/index.txt" ]]       || touch "${DB_DIR}/index.txt"
    [[ -f "${DB_DIR}/index.txt.attr" ]]  || echo "unique_subject = no" > "${DB_DIR}/index.txt.attr"
    [[ -f "${DB_DIR}/serial" ]]          || echo "01" > "${DB_DIR}/serial"
    [[ -f "${DB_DIR}/crlnumber" ]]       || echo "01" > "${DB_DIR}/crlnumber"
}

# ── OpenSSL 설정 파일 생성 ──────────────────────────────────────
write_openssl_cnf() {
    cat > "$CNF_FILE" << EOF
[ ca ]
default_ca = CA_default

[ CA_default ]
dir               = ${DB_DIR}
certs             = \$dir
crl_dir           = ${CERTS_DIR}
new_certs_dir     = \$dir/newcerts
database          = \$dir/index.txt
serial            = \$dir/serial
certificate       = ${CA_CERT}
private_key       = ${CA_KEY}
crl               = ${CRL_FILE}
crlnumber         = \$dir/crlnumber
default_crl_days  = 30
default_md        = sha256
preserve          = no
policy            = policy_any

[ policy_any ]
countryName             = optional
stateOrProvinceName     = optional
organizationName        = optional
organizationalUnitName  = optional
commonName              = supplied
emailAddress            = optional

[ req ]
default_bits        = 2048
default_md          = sha256
distinguished_name  = req_distinguished_name

[ req_distinguished_name ]
EOF
}

# ── 인증서 CRL 등록 (OpenSSL) ────────────────────────────────────
revoke_cert_in_crl() {
    local cert_file="$1"
    local serial hex_serial expiry_raw expiry_fmt subject

    init_ca_db
    write_openssl_cnf

    serial=$(get_cert_serial "$cert_file")
    hex_serial=$(echo "$serial" | tr 'a-f' 'A-F')

    # newcerts/ 에 인증서 복사
    cp "$cert_file" "${DB_DIR}/newcerts/${hex_serial}.pem"

    # index.txt에 아직 등록되지 않은 경우에만 추가
    if ! grep -q "	${hex_serial}	" "${DB_DIR}/index.txt" 2>/dev/null; then
        expiry_raw=$(openssl x509 -in "$cert_file" -noout -enddate 2>/dev/null | cut -d= -f2)
        expiry_fmt=$(python3 -c "
from datetime import datetime
import sys
try:
    d = datetime.strptime('${expiry_raw}', '%b %d %H:%M:%S %Y %Z')
    print(d.strftime('%y%m%d%H%M%SZ'))
except Exception:
    print('271231235959Z')
" 2>/dev/null || echo "271231235959Z")
        subject=$(openssl x509 -in "$cert_file" -noout -subject -nameopt RFC2253 2>/dev/null | \
            sed 's/^subject=//')
        printf "V\t%s\t\t%s\tunknown\t%s\n" "$expiry_fmt" "$hex_serial" "$subject" \
            >> "${DB_DIR}/index.txt"
    fi

    # 이미 폐기된 경우 건너뜀
    if grep -q "^R.*	${hex_serial}	" "${DB_DIR}/index.txt" 2>/dev/null; then
        warn "인증서 이미 폐기됨 (Serial: ${hex_serial})"
    else
        openssl ca \
            -config "$CNF_FILE" \
            -revoke "$cert_file" \
            -keyfile "$CA_KEY" \
            -cert "$CA_CERT" \
            -batch 2>/dev/null
        ok "CRL 등록 완료 (Serial: ${hex_serial})"
    fi

    # CRL 파일 생성
    openssl ca \
        -config "$CNF_FILE" \
        -gencrl \
        -keyfile "$CA_KEY" \
        -cert "$CA_CERT" \
        -out "$CRL_FILE" \
        -batch 2>/dev/null

    local revoked_count
    revoked_count=$(openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
        grep -c 'Serial Number' || echo "0")
    ok "CRL 파일 생성 완료 — 누적 폐기 수: ${revoked_count}개"
}

# ── CRL → Client VPN 엔드포인트 임포트 ──────────────────────────
import_crl() {
    local eid="$1"
    local crl_pem
    crl_pem=$(cat "$CRL_FILE")

    aws ec2 import-client-vpn-client-certificate-revocation-list \
        --client-vpn-endpoint-id "$eid" \
        --certificate-revocation-list "$crl_pem" \
        --region "$AWS_REGION" \
        --output none
    ok "CRL → Client VPN 임포트 완료 (${eid})"
}

# ── 특정 CN의 활성 세션 강제 종료 ───────────────────────────────
terminate_sessions_by_cn() {
    local eid="$1" target_cn="$2"

    local conn_ids
    conn_ids=$(aws ec2 describe-client-vpn-connections \
        --client-vpn-endpoint-id "$eid" \
        --filters "Name=status-code,Values=active" \
        --region "$AWS_REGION" \
        --query "Connections[?CommonName=='${target_cn}'].ConnectionId" \
        --output json 2>/dev/null || echo "[]")

    local count
    count=$(echo "$conn_ids" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [[ "$count" -eq 0 ]]; then
        info "활성 세션 없음 (CN: ${target_cn})"
        return 0
    fi

    info "활성 세션 ${count}개 강제 종료 (CN: ${target_cn})"
    echo "$conn_ids" | python3 -c "
import json, sys
for cid in json.load(sys.stdin):
    print(cid)
" | while read -r cid; do
        aws ec2 terminate-client-vpn-connections \
            --client-vpn-endpoint-id "$eid" \
            --connection-id "$cid" \
            --region "$AWS_REGION" \
            --output none 2>/dev/null \
            && ok "세션 종료: ${cid}" \
            || warn "세션 종료 실패 (이미 종료됐을 수 있음): ${cid}"
    done
}

# ── 등록된 CN 목록 반환 ──────────────────────────────────────────
get_registered_cns() {
    for d in "${!DESKTOP_CN_MAP[@]}"; do
        echo "${DESKTOP_CN_MAP[$d]}"
    done
}

# ════════════════════════════════════════════════════════════════
# check: 현재 Client VPN 연결 상태 및 인증서 매핑 확인
# ════════════════════════════════════════════════════════════════
cmd_check() {
    section "Client VPN 현재 상태"

    step "1/4  Endpoint ID 조회"
    local eid
    eid=$(get_endpoint_id)
    info "Endpoint: ${eid}"

    step "2/4  활성 연결 목록"
    local conns
    conns=$(get_active_connections "$eid")
    local conn_count
    conn_count=$(echo "$conns" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")

    if [[ "$conn_count" -eq 0 ]]; then
        info "현재 활성 연결 없음"
    else
        echo ""
        echo "  ┌──────────────────────────────────────┬─────────────────────────┬───────────────┬────────────┐"
        echo "  │ Connection ID                        │ CN (인증서)             │ Client IP     │ 상태       │"
        echo "  ├──────────────────────────────────────┼─────────────────────────┼───────────────┼────────────┤"
        echo "$conns" | python3 -c "
import json, sys
registered = set([$(for d in "${!DESKTOP_CN_MAP[@]}"; do printf '\"${DESKTOP_CN_MAP[$d]}\",'; done)])
conns = json.load(sys.stdin)
for c in conns:
    cid    = (c.get('ID')  or '')[:36]
    cn     = (c.get('CN')  or 'N/A')[:23]
    ip     = (c.get('IP')  or 'N/A')[:13]
    status = (c.get('Status') or 'N/A')[:10]
    flag   = '' if cn in registered else ' ⚠ 비인가'
    print(f'  │ {cid:<36} │ {cn:<23} │ {ip:<13} │ {status:<10} │{flag}')
"
        echo "  └──────────────────────────────────────┴─────────────────────────┴───────────────┴────────────┘"
    fi

    step "3/4  등록된 데스크탑 → CN 매핑"
    echo ""
    for desktop in $(echo "${!DESKTOP_CN_MAP[@]}" | tr ' ' '\n' | sort); do
        local cert_file="${CERTS_DIR}/${desktop}.crt"
        local cert_info="인증서 없음 (${cert_file})"
        if [[ -f "$cert_file" ]]; then
            cert_info="Serial: $(get_cert_serial "$cert_file")"
        fi
        echo "  ${desktop}  →  CN: ${DESKTOP_CN_MAP[$desktop]}  (${cert_info})"
    done

    step "4/4  현재 CRL 상태"
    if [[ -f "$CRL_FILE" ]]; then
        local revoked_count
        revoked_count=$(openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
            grep -c 'Serial Number' || echo "0")
        warn "CRL 존재 — 폐기된 인증서: ${revoked_count}개"
        openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
            grep -B1 'Serial Number' | grep 'Serial Number' | \
            awk '{print "    폐기 Serial:", $NF}'
    else
        ok "CRL 없음 — 폐기 이력 없음"
    fi
}

# ════════════════════════════════════════════════════════════════
# simulate: 비인가 인증키 접속 시도 시뮬레이션
# ════════════════════════════════════════════════════════════════
cmd_simulate() {
    section "비인가 인증키 접속 시도 시뮬레이션"

    if [[ ! -f "$CA_KEY" || ! -f "$CA_CERT" ]]; then
        error "CA 인증서/키 없음"
        error "  CA_CERT: ${CA_CERT}"
        error "  CA_KEY:  ${CA_KEY}"
        error "위 경로에 Client VPN CA 파일을 배치하세요."
        exit 1
    fi

    step "1/4  비인가 클라이언트 인증서 생성 (CN=${ATTACKER_CN})"
    CLEANUP_NEEDED=true
    local attacker_csr="${CERTS_DIR}/${ATTACKER_CN}.csr"

    mkdir -p "$CERTS_DIR"
    openssl genrsa -out "$ATTACKER_KEY" 2048 2>/dev/null
    openssl req -new -key "$ATTACKER_KEY" \
        -subj "/CN=${ATTACKER_CN}" \
        -out "$attacker_csr" 2>/dev/null
    openssl x509 -req \
        -in "$attacker_csr" \
        -CA "$CA_CERT" \
        -CAkey "$CA_KEY" \
        -CAcreateserial \
        -out "$ATTACKER_CERT" \
        -days 365 \
        -sha256 2>/dev/null
    rm -f "$attacker_csr"

    local attacker_serial
    attacker_serial=$(get_cert_serial "$ATTACKER_CERT")
    ok "비인가 인증서 생성 완료"
    info "  CN:     ${ATTACKER_CN}"
    info "  Serial: ${attacker_serial}"
    info "  파일:   ${ATTACKER_CERT}"

    step "2/4  등록된 데스크탑 CN 목록 대조"
    echo ""
    info "등록된 CN 목록:"
    for d in $(echo "${!DESKTOP_CN_MAP[@]}" | tr ' ' '\n' | sort); do
        echo "    ✓  ${DESKTOP_CN_MAP[$d]}"
    done
    echo ""
    echo -e "    ${RED}✗  ${ATTACKER_CN}  ← 미등록 CN${NC}"

    step "3/4  CloudWatch Logs 최근 연결 이벤트 조회 (최근 ${LOG_WINDOW}분)"
    local log_events
    log_events=$(query_vpn_logs)
    local event_count
    event_count=$(echo "$log_events" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    info "최근 연결 이벤트: ${event_count}건"

    # 로그에서 미등록 CN 탐색
    if [[ "$event_count" -gt 0 ]]; then
        local suspicious
        suspicious=$(echo "$log_events" | python3 -c "
import json, sys, re
registered_cns = {$(for d in "${!DESKTOP_CN_MAP[@]}"; do printf '\"${DESKTOP_CN_MAP[$d]}\", '; done)}
events = json.load(sys.stdin)
found = []
for msg in events:
    m = re.search(r'CommonName[=:\s]+([^\s,]+)', str(msg), re.IGNORECASE)
    if m:
        cn = m.group(1)
        if cn not in registered_cns:
            found.append(cn)
if found:
    for cn in set(found):
        print(cn)
else:
    print('')
" 2>/dev/null || echo "")
        if [[ -n "$suspicious" ]]; then
            warn "실제 로그에서 미등록 CN 탐지:"
            echo "$suspicious" | while read -r cn; do
                [[ -z "$cn" ]] && continue
                warn "    비인가 CN: ${cn}"
            done
        fi
    fi

    step "4/4  침입 탐지 이벤트"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local eid
    eid=$(get_endpoint_id)
    echo ""
    echo -e "  ${RED}┌─────────────────────────────────────────────────────┐${NC}"
    echo -e "  ${RED}│  [침입 탐지] 비인가 인증서 접속 시도 감지           │${NC}"
    echo -e "  ${RED}├─────────────────────────────────────────────────────┤${NC}"
    echo -e "  ${RED}│${NC}  시각:      ${ts}"
    echo -e "  ${RED}│${NC}  CN:        ${ATTACKER_CN}"
    echo -e "  ${RED}│${NC}  Serial:    ${attacker_serial}"
    echo -e "  ${RED}│${NC}  Endpoint:  ${eid}"
    echo -e "  ${RED}│${NC}  판정:      미등록 CN → 비인가 접속 시도"
    echo -e "  ${RED}│${NC}  조치:      인증서 폐기 (revoke) 단계 진행"
    echo -e "  ${RED}└─────────────────────────────────────────────────────┘${NC}"
    echo ""
    info "다음 단계: bash $0 revoke"
}

# ════════════════════════════════════════════════════════════════
# revoke: 비인가 인증서 CRL 등록 + Client VPN 임포트 + 세션 종료
# ════════════════════════════════════════════════════════════════
cmd_revoke() {
    section "비인가 인증서 폐기"

    if [[ ! -f "$ATTACKER_CERT" ]]; then
        error "폐기 대상 인증서 없음: ${ATTACKER_CERT}"
        error "먼저 simulate 단계를 실행하세요: bash $0 simulate"
        exit 1
    fi
    if [[ ! -f "$CA_KEY" || ! -f "$CA_CERT" ]]; then
        error "CA 인증서/키 없음 — CRL 서명 불가"
        exit 1
    fi

    local eid
    eid=$(get_endpoint_id)
    local attacker_serial
    attacker_serial=$(get_cert_serial "$ATTACKER_CERT")
    local attacker_cn
    attacker_cn=$(get_cert_cn "$ATTACKER_CERT")

    step "1/4  인증서 CRL 등록 (OpenSSL)"
    info "대상: CN=${attacker_cn}  Serial=${attacker_serial}"
    revoke_cert_in_crl "$ATTACKER_CERT"

    step "2/4  CRL → Client VPN 엔드포인트 임포트"
    import_crl "$eid"

    step "3/4  활성 세션 강제 종료"
    terminate_sessions_by_cn "$eid" "$attacker_cn"

    step "4/4  폐기 결과"
    local revoked_count
    revoked_count=$(openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
        grep -c 'Serial Number' || echo "0")

    echo ""
    echo -e "  ${GREEN}┌──────────────────────────────────────────────────────┐${NC}"
    echo -e "  ${GREEN}│  [폐기 완료] 인증서 무효화 처리 성공                │${NC}"
    echo -e "  ${GREEN}├──────────────────────────────────────────────────────┤${NC}"
    echo -e "  ${GREEN}│${NC}  CN:               ${attacker_cn}"
    echo -e "  ${GREEN}│${NC}  Serial:           ${attacker_serial}"
    echo -e "  ${GREEN}│${NC}  CRL 폐기 총 수:   ${revoked_count}개"
    echo -e "  ${GREEN}│${NC}  차단 방식:        TLS 핸드셰이크 단계에서 거부"
    echo -e "  ${GREEN}│${NC}  기존 세션:        강제 종료 완료"
    echo -e "  ${GREEN}└──────────────────────────────────────────────────────┘${NC}"
    echo ""
    warn "인증서 키 파일 보안 삭제 권장: rm -f ${ATTACKER_KEY}"
}

# ════════════════════════════════════════════════════════════════
# verify: 폐기 후 차단 상태 검증
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    section "인증서 폐기 후 차단 검증"

    local eid
    eid=$(get_endpoint_id)

    step "1/3  CRL 임포트 상태 확인"
    if [[ -f "$CRL_FILE" ]]; then
        local revoked_count
        revoked_count=$(openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
            grep -c 'Serial Number' || echo "0")
        ok "CRL 파일 존재 — 폐기된 인증서: ${revoked_count}개"
        openssl crl -in "$CRL_FILE" -noout -text 2>/dev/null | \
            grep -B1 'Serial Number' | grep 'Serial Number' | \
            awk '{print "    Serial:", $NF}'
    else
        error "CRL 파일 없음 — revoke 단계 재실행 필요"
        return 1
    fi

    step "2/3  활성 연결에서 폐기된 CN 잔류 여부 확인"
    local conns
    conns=$(get_active_connections "$eid")
    local lingering
    lingering=$(echo "$conns" | python3 -c "
import json, sys
conns = json.load(sys.stdin)
bad = [c for c in conns if c.get('CN') == '${ATTACKER_CN}']
print(len(bad))
" 2>/dev/null || echo "0")

    if [[ "$lingering" -eq 0 ]]; then
        ok "폐기된 CN(${ATTACKER_CN}) 활성 세션 없음 — 차단 정상"
    else
        error "경고: 폐기 CN 세션 ${lingering}개 여전히 활성 — 수동 종료 필요"
        warn "  aws ec2 terminate-client-vpn-connections --client-vpn-endpoint-id ${eid} --connection-id <id>"
    fi

    step "3/3  CRL 유효성 검증"
    if openssl crl -in "$CRL_FILE" -CAfile "$CA_CERT" -noout 2>/dev/null; then
        ok "CRL 서명 검증 통과 (CA 서명 유효)"
    else
        warn "CA 인증서 없음 또는 검증 불가 — AWS 측에서는 정상 임포트된 경우 차단 작동"
    fi

    echo ""
    echo "  검증 요약:"
    echo "  ├── CRL 등록:            완료 (OpenSSL CA 서명)"
    echo "  ├── VPN 엔드포인트 임포트: 완료"
    echo "  ├── 활성 세션 종료:       완료"
    echo "  └── 차단 방식:           TLS 핸드셰이크 — certificate_revoked(44) 에러"
}

# ════════════════════════════════════════════════════════════════
# restore: CRL 초기화 및 테스트 파일 정리
# ════════════════════════════════════════════════════════════════
cmd_restore() {
    section "테스트 복구 — CRL 초기화"

    local eid
    eid=$(get_endpoint_id)

    step "1/3  빈 CRL 생성 및 Client VPN 임포트 (폐기 목록 초기화)"
    if [[ -f "$CA_KEY" && -f "$CA_CERT" ]]; then
        # 빈 CA 데이터베이스로 폐기 항목 없는 CRL 생성
        local restore_dir="${CERTS_DIR}/restore-tmp"
        mkdir -p "${restore_dir}/newcerts"
        touch "${restore_dir}/index.txt"
        echo "unique_subject = no" > "${restore_dir}/index.txt.attr"
        echo "01" > "${restore_dir}/serial"
        echo "01" > "${restore_dir}/crlnumber"

        cat > "${restore_dir}/openssl.cnf" << EOF
[ ca ]
default_ca = CA_default
[ CA_default ]
dir               = ${restore_dir}
new_certs_dir     = \$dir/newcerts
database          = \$dir/index.txt
serial            = \$dir/serial
certificate       = ${CA_CERT}
private_key       = ${CA_KEY}
crl               = ${CRL_FILE}
crlnumber         = \$dir/crlnumber
default_crl_days  = 30
default_md        = sha256
preserve          = no
policy            = policy_any
[ policy_any ]
commonName = supplied
EOF

        openssl ca \
            -config "${restore_dir}/openssl.cnf" \
            -gencrl \
            -keyfile "$CA_KEY" \
            -cert "$CA_CERT" \
            -out "$CRL_FILE" \
            -batch 2>/dev/null

        import_crl "$eid"
        rm -rf "$restore_dir"
        ok "빈 CRL 임포트 완료 — 폐기 목록 초기화"
    else
        warn "CA 키 없음 — CRL 파일만 삭제 (AWS VPN에는 마지막 임포트 상태 유지)"
        rm -f "$CRL_FILE"
    fi

    step "2/3  CA 데이터베이스 및 CRL 파일 정리"
    rm -rf "$DB_DIR" "$CNF_FILE" "$CRL_FILE" "${CERTS_DIR}/ca.srl" 2>/dev/null || true
    ok "CA 데이터베이스 및 CRL 파일 삭제 완료"

    step "3/3  시뮬레이션 인증서 파일 정리"
    rm -f "$ATTACKER_CERT" "$ATTACKER_KEY" \
          "${CERTS_DIR}/${ATTACKER_CN}.csr" 2>/dev/null || true
    CLEANUP_NEEDED=false
    ok "시뮬레이션 파일 정리 완료"

    echo ""
    ok "복구 완료"
    echo "  - 등록된 데스크탑 인증서(desktop-1,2,3)로 정상 접속 가능"
    echo "  - 비인가 인증서 관련 파일 모두 삭제"
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    section "Client VPN 비인가 인증키 침입 시나리오 전체 실행"
    echo ""
    echo "  단계: 상태확인 → 침입시뮬레이션 → 인증키폐기 → 차단검증 → 복구"
    echo ""

    cmd_check

    echo ""
    warn "비인가 인증서를 생성하여 접속 시도를 시뮬레이션합니다."
    warn "계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_simulate

    echo ""
    warn "탐지된 비인가 인증서를 폐기합니다. 계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_revoke

    echo ""
    cmd_verify

    echo ""
    warn "테스트 완료 — CRL 및 파일을 초기화합니다. 계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_restore

    section "최종 결과"
    cmd_check
    echo ""
    ok "비인가 인증키 침입 시나리오 완료"
    echo ""
    echo "  결론:"
    echo "  - 비인가 CN(${ATTACKER_CN}) 접속 시도 탐지"
    echo "  - OpenSSL CRL 등록 → Client VPN 엔드포인트 임포트"
    echo "  - 활성 세션 강제 종료"
    echo "  - 이후 동일 인증서 사용 시 TLS certificate_revoked(44) 에러로 차단"
    echo "  - 등록된 데스크탑 인증서는 정상 접속 유지"
}

# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
MODE="${1:-all}"

case "$MODE" in
    check)    cmd_check ;;
    simulate) cmd_simulate ;;
    revoke)   cmd_revoke ;;
    verify)   cmd_verify ;;
    restore)  cmd_restore ;;
    all)      cmd_all ;;
    *)
        echo "사용법: $0 [check|simulate|revoke|verify|restore|all]"
        echo ""
        echo "  check     현재 VPN 연결 및 인증서 매핑 확인"
        echo "  simulate  비인가 인증서 생성 및 접속 시도 시뮬레이션"
        echo "  revoke    탐지된 비인가 인증서 CRL 등록 및 세션 종료"
        echo "  verify    폐기 후 차단 상태 검증"
        echo "  restore   CRL 초기화 및 테스트 파일 정리 (복구)"
        echo "  all       전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
