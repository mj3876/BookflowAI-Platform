#!/usr/bin/env bash
# gcp-sa-key-exposure-test.sh
#
# Scenario 13: SA 키 노출 → VPN 우회 BigQuery 직접 접근 검증
#
# 재현 경로:
#   AWS Secrets Manager에서 forecast-reader SA 키 추출
#   → gcloud auth activate-service-account (인터넷 경유, PSC/VPN 미사용)
#   → bookflow_dw 전체 테이블 직접 쿼리 가능 확인
#   → 키 연령/이상 접근 IP 감사
#
# 원복:
#   SA gcloud 인증 해제 + 임시 키 파일 삭제 + 원본 계정 복원
#
# 사용법:
#   bash gcp-sa-key-exposure-test.sh           # 전체 실행 (기본)
#   bash gcp-sa-key-exposure-test.sh audit     # 키 연령/사용 이력/이상 IP 감사만
#   bash gcp-sa-key-exposure-test.sh simulate  # 키 추출 + VPN 우회 BQ 접근
#   bash gcp-sa-key-exposure-test.sh restore   # 임시 파일 삭제 + 인증 원복
#
# 사전 조건:
#   gcloud (GCP 인증), aws CLI (ap-northeast-1 Secrets Manager 권한), bq CLI

set -euo pipefail

GCP_PROJECT="project-8ab6bf05-54d2-4f5d-b8d"
DATASET="bookflow_dw"
SA_EMAIL="bookflow-forecast-reader@${GCP_PROJECT}.iam.gserviceaccount.com"
SECRET_ID="bookflow/gcp/forecast-reader-sa-key"
AWS_REGION="${AWS_REGION:-ap-northeast-1}"

# EKS VPC 대역 — 이 범위 밖 IP는 비정상 접근
EKS_CIDRS="10.0.0.0/16 10.1.0.0/16 10.2.0.0/16 10.3.0.0/16 10.4.0.0/16"

TEMP_KEY_FILE=""
SA_AUTH_ACTIVATED=false
ORIG_ACCOUNT=""

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

# ── 안전 종료: SA 인증 + 임시 키 파일 자동 정리 ─────────────────
restore_on_exit() {
    if [[ "$SA_AUTH_ACTIVATED" == "true" || -n "$TEMP_KEY_FILE" ]]; then
        warn "스크립트 종료 감지 — SA 인증 자동 원복 중..."
        _revoke_sa_auth 2>/dev/null || true
    fi
}
trap restore_on_exit EXIT

# ── SA 인증 해제 + 원본 계정 복원 ────────────────────────────────
_revoke_sa_auth() {
    if [[ "$SA_AUTH_ACTIVATED" == "true" ]]; then
        gcloud auth revoke "$SA_EMAIL" --quiet 2>/dev/null && \
            ok "SA 인증 해제: $SA_EMAIL" || warn "SA 인증 해제 실패 (이미 해제됨)"
        SA_AUTH_ACTIVATED=false
    fi
    if [[ -n "$TEMP_KEY_FILE" && -f "$TEMP_KEY_FILE" ]]; then
        rm -f "$TEMP_KEY_FILE"
        ok "임시 키 파일 삭제: $TEMP_KEY_FILE"
        TEMP_KEY_FILE=""
    fi
    if [[ -n "$ORIG_ACCOUNT" ]]; then
        gcloud config set account "$ORIG_ACCOUNT" --quiet 2>/dev/null && \
            ok "원본 계정 복원: $ORIG_ACCOUNT" || \
            warn "계정 복원 실패 — 수동: gcloud config set account $ORIG_ACCOUNT"
        ORIG_ACCOUNT=""
    fi
}

# ════════════════════════════════════════════════════════════════
# audit: SA 키 연령 / 사용 이력 / 이상 IP 감사
# ════════════════════════════════════════════════════════════════
cmd_audit() {
    section "SA 키 감사 — 연령 / Secrets Manager 접근 / 이상 IP"

    step "1/4  SA 키 목록 (USER_MANAGED 키 생성일 / 만료일)"
    gcloud iam service-accounts keys list \
        --iam-account="$SA_EMAIL" \
        --project="$GCP_PROJECT" \
        --filter="keyType=USER_MANAGED" \
        --format="table[box](name.basename():label=KEY_ID, keyType, validAfterTime:label=CREATED, validBeforeTime:label=EXPIRES)" \
        2>/dev/null || warn "키 목록 조회 실패"

    echo ""
    # 키 연령 계산
    local oldest_days
    oldest_days=$(gcloud iam service-accounts keys list \
        --iam-account="$SA_EMAIL" \
        --project="$GCP_PROJECT" \
        --filter="keyType=USER_MANAGED" \
        --format="value(validAfterTime)" 2>/dev/null | head -1 | python3 -c "
import sys, datetime
line = sys.stdin.read().strip()
if not line:
    print('N/A')
    sys.exit(0)
try:
    created = datetime.datetime.fromisoformat(line.replace('Z','+00:00'))
    days = (datetime.datetime.now(datetime.timezone.utc) - created).days
    print(days)
except Exception:
    print('N/A')
" 2>/dev/null || echo "N/A")

    if [[ "$oldest_days" != "N/A" && "$oldest_days" -gt 90 ]]; then
        warn "키 연령: ${oldest_days}일 — 90일 권고 기준 초과 (만료 없음, 지금도 유효)"
    elif [[ "$oldest_days" != "N/A" ]]; then
        ok "키 연령: ${oldest_days}일"
    fi

    step "2/4  AWS Secrets Manager 최근 접근 이력"
    aws secretsmanager describe-secret \
        --secret-id "$SECRET_ID" \
        --region "$AWS_REGION" \
        --query '{SecretId:Name,LastAccessed:LastAccessedDate,LastChanged:LastChangedDate,RotationEnabled:RotationEnabled}' \
        --output table 2>/dev/null || warn "Secrets Manager 조회 실패 (AWS 인증 확인)"

    step "3/4  Cloud Audit Log — SA 최근 사용 이력 (IP 포함)"
    info "※ Data Access 로그가 비활성화된 경우 결과 없을 수 있음"
    echo ""
    gcloud logging read \
        "protoPayload.authenticationInfo.principalEmail=\"${SA_EMAIL}\"" \
        --project="$GCP_PROJECT" \
        --limit=10 \
        --format="table[box](timestamp, protoPayload.requestMetadata.callerIp:label=CALLER_IP, protoPayload.methodName:label=METHOD)" \
        2>/dev/null || warn "Cloud Logging 조회 실패"

    step "4/4  예상 외 IP 감지 (EKS VPC 10.x.x.x 범위 외)"
    gcloud logging read \
        "protoPayload.authenticationInfo.principalEmail=\"${SA_EMAIL}\"" \
        --project="$GCP_PROJECT" \
        --limit=100 \
        --format=json 2>/dev/null | python3 -c "
import json, sys, ipaddress
EKS_CIDRS = [ipaddress.ip_network(c) for c in '${EKS_CIDRS}'.split()]

def in_eks(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
        return any(ip in cidr for cidr in EKS_CIDRS)
    except Exception:
        return True

entries = json.load(sys.stdin)
suspicious = []
for e in entries:
    pp = e.get('protoPayload') or {}
    ip = pp.get('requestMetadata', {}).get('callerIp', '')
    if ip and not in_eks(ip):
        suspicious.append((e.get('timestamp',''), ip, pp.get('methodName','')))

if suspicious:
    print('  \033[0;31m[WARN] EKS VPC 범위 외 IP에서 SA 사용 감지:\033[0m')
    for ts, ip, method in suspicious[:10]:
        print(f'    {ts}  IP={ip}  {method}')
else:
    print('  \033[0;32m[OK] 모든 접근이 EKS VPC 범위 내 IP (또는 로그 없음)\033[0m')
" 2>/dev/null || warn "IP 분석 실패"
}

# ════════════════════════════════════════════════════════════════
# simulate: SA 키 추출 → VPN 우회 BQ 직접 접근
# ════════════════════════════════════════════════════════════════
cmd_simulate() {
    section "시뮬레이션 — SA 키 탈취 후 VPN 우회 BigQuery 직접 접근"
    echo ""
    echo "  재현 시나리오:"
    echo "  forecast-svc 파드 탈취 → /var/run/gcp-sa/sa-key.json 탈취"
    echo "  → 인터넷에서 gcloud auth → PSC/VPN 경유 없이 BigQuery 직접 쿼리"
    echo ""

    ORIG_ACCOUNT=$(gcloud config get-value account 2>/dev/null || echo "")
    info "현재 gcloud 계정 (원복 대상): ${ORIG_ACCOUNT:-없음}"

    step "1/4  AWS Secrets Manager에서 SA 키 추출"
    TEMP_KEY_FILE="$(python3 -c \
        'import tempfile; f=tempfile.NamedTemporaryFile(suffix="-sa-key-EXPOSED.json",delete=False,dir="/tmp"); print(f.name); f.close()')"

    local secret_val
    secret_val=$(aws secretsmanager get-secret-value \
        --secret-id "$SECRET_ID" \
        --region "$AWS_REGION" \
        --query 'SecretString' \
        --output text 2>/dev/null || echo "")

    if [[ -z "$secret_val" ]]; then
        error "Secrets Manager 키 조회 실패 — AWS 인증 또는 IAM 권한 확인"
        TEMP_KEY_FILE=""
        exit 1
    fi

    echo "$secret_val" > "$TEMP_KEY_FILE"
    chmod 600 "$TEMP_KEY_FILE"
    ok "SA 키 추출 완료 → 임시 파일: $TEMP_KEY_FILE"
    warn "이 파일 1개로 아래 모든 BigQuery 접근이 가능합니다 (PSC/VPN 불필요)."

    step "2/4  SA 인증 활성화 (인터넷 경유, VPN/PSC 미사용)"
    gcloud auth activate-service-account "$SA_EMAIL" \
        --key-file="$TEMP_KEY_FILE" \
        --quiet
    SA_AUTH_ACTIVATED=true
    ok "SA 인증 완료: $SA_EMAIL"
    info "현재 호출 IP: $(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo '확인 불가')"

    step "3/4  BigQuery 접근 가능 테이블 목록"
    echo ""
    bq ls --project_id="$GCP_PROJECT" "$DATASET" 2>/dev/null || warn "bq ls 실패"

    step "4/4  민감 테이블 접근 범위 확인 (공격자가 볼 수 있는 데이터)"
    echo ""
    local tables=("features" "sales_fact" "inventory_daily" "forecast_results" "books_static")
    printf "  %-28s  %s\n" "테이블" "행 수"
    printf "  %-28s  %s\n" "------" "----"
    for tbl in "${tables[@]}"; do
        local cnt
        cnt=$(bq query --use_legacy_sql=false --format=csv --quiet \
            "SELECT COUNT(*) FROM \`${GCP_PROJECT}.${DATASET}.${tbl}\`" \
            2>/dev/null | tail -1 || echo "조회실패")
        printf "  %-28s  %s\n" "$tbl" "$cnt"
    done

    echo ""
    info "forecast_results 최근 5건 (예측 데이터 무단 열람):"
    bq query --use_legacy_sql=false --format=pretty --max_rows=5 \
        "SELECT prediction_date, isbn13, store_id, predicted_demand, confidence_low, confidence_high
         FROM \`${GCP_PROJECT}.${DATASET}.forecast_results\`
         ORDER BY prediction_date DESC LIMIT 5" \
        2>/dev/null || info "  (데이터 없음 — stub 모드)"

    echo ""
    info "sales_fact 최근 5건 (매출 데이터 무단 열람):"
    bq query --use_legacy_sql=false --format=pretty --max_rows=5 \
        "SELECT sale_date, isbn13, store_id, qty_sold, revenue
         FROM \`${GCP_PROJECT}.${DATASET}.sales_fact\`
         ORDER BY sale_date DESC LIMIT 5" \
        2>/dev/null || info "  (데이터 없음)"

    echo ""
    warn "위 접근은 VPN/PSC 없이 인터넷에서 직접 수행됐습니다."
    warn "키가 유출된 경우 이 데이터 전체가 외부에 노출됩니다."
}

# ════════════════════════════════════════════════════════════════
# restore: 임시 키 파일 삭제 + gcloud 인증 원복
# ════════════════════════════════════════════════════════════════
cmd_restore() {
    section "원복 — SA 인증 해제 + 임시 키 파일 삭제"
    _revoke_sa_auth

    echo ""
    ok "원복 완료"
    info "현재 gcloud 활성 계정: $(gcloud config get-value account 2>/dev/null || echo '없음')"

    echo ""
    echo "  [권고 조치]"
    echo "  1. SA 키 즉시 교체 (신규 키 발급 → Secrets Manager 업데이트 → 구 키 삭제):"
    echo "     gcloud iam service-accounts keys create /tmp/new-key.json --iam-account=${SA_EMAIL}"
    echo "     aws secretsmanager put-secret-value --secret-id ${SECRET_ID} --secret-string file:///tmp/new-key.json"
    echo ""
    echo "  2. Workload Identity Federation 전환 (키리스 인증) — 근본 해결"
    echo "     SA 키 파일 방식 폐기, EKS OIDC 기반 인증으로 대체"
    echo ""
    echo "  3. forecast_reader 권한 축소:"
    echo "     bookflow_dw 전체 → forecast_results 단일 테이블 dataViewer 로 제한"
    echo ""
    echo "  4. Secrets Manager 자동 교체 활성화 (RotationEnabled: true)"
    echo "     현재: 수동 교체 — 유출 감지 전까지 키가 무기한 유효"
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    section "Scenario 13: SA 키 노출 → VPN 우회 BQ 접근 — 전체 실행"
    echo ""
    echo "  단계: 키 감사 → 탈취 시뮬레이션 → 원복"
    echo ""

    cmd_audit

    echo ""
    warn "AWS Secrets Manager에서 SA 키를 추출하고 VPN 우회 BQ 접근을 시뮬레이션합니다."
    warn "계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_simulate

    echo ""
    cmd_restore

    section "Scenario 13 완료"
    echo ""
    echo "  확인된 취약점:"
    echo "  - Secrets Manager 키 1개 → 인터넷에서 PSC/VPN 없이 BQ 전체 접근 가능"
    echo "  - bookflow_dw 전체 dataViewer: sales_fact, inventory_daily, forecast_results 모두 노출"
    echo "  - 키 만료 없음 — 유출 후 감지 전까지 무기한 유효"
    echo "  - bigquery.jobUser: 임의 SQL 실행 가능 (대규모 쿼리로 비용 폭증 가능)"
}

MODE="${1:-all}"
case "$MODE" in
    audit)    cmd_audit ;;
    simulate) cmd_simulate ;;
    restore)  cmd_restore ;;
    all)      cmd_all ;;
    *)
        echo "사용법: $0 [audit|simulate|restore|all]"
        echo ""
        echo "  audit     SA 키 연령 / 사용 이력 / 이상 IP 감사"
        echo "  simulate  SA 키 추출 + VPN 우회 BigQuery 접근 시뮬레이션"
        echo "  restore   임시 키 파일 삭제 + gcloud 인증 원복"
        echo "  all       전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
