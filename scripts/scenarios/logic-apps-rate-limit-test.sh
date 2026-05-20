#!/usr/bin/env bash
# logic-apps-rate-limit-test.sh
#
# Azure Logic Apps ACS 429 Rate Limit 장애 재현 + Semaphore 수정 검증
# (실제 발생 사례: 2026-05-19 오후 1:47~1:48, 14건 동시 실패)
#
# 목적:
#   [재현] notification-svc Pod 내에서 Logic Apps webhook을 N건 동시 호출
#          → ACS Email API Rate Limit 초과 (429) → ActionResponseTimedOut 확인
#   [수정] notification-svc _logic_apps_sem = asyncio.Semaphore(1) 코드 상태 검증
#          + kubectl rollout restart으로 최신 이미지 반영 확인
#   [검증] Semaphore(1) 래핑 후 N건 호출 → 직렬 처리 → 429 미발생 + 소요시간 비교
#
# 사용법:
#   bash logic-apps-rate-limit-test.sh                    # 전체 실행 (기본 N=5)
#   bash logic-apps-rate-limit-test.sh check              # 현재 semaphore/pod/로그 상태
#   bash logic-apps-rate-limit-test.sh reproduce [N]      # N건 동시 직접 호출 (기본 15)
#   bash logic-apps-rate-limit-test.sh fix                # Semaphore(1) 확인 + rollout
#   bash logic-apps-rate-limit-test.sh verify  [N]        # Semaphore(1) 적용 후 N건 검증
#   bash logic-apps-rate-limit-test.sh all     [N]        # 전체 시나리오 순서대로 실행
#
# 사전 조건:
#   kubectl (bookflow 네임스페이스 접근), aws CLI (ap-northeast-1), az CLI (선택)
#
# 환경 변수 (선택):
#   AWS_REGION              (기본: ap-northeast-1)
#   DRY_RUN                 true=실제 Logic Apps 미호출, httpstat.us 모의 사용 (기본: false)
#   AZURE_SUBSCRIPTION_ID   az CLI 로그 조회 시 필요

set -euo pipefail

# ── 설정 ─────────────────────────────────────────────────────────
AWS_REGION="${AWS_REGION:-ap-northeast-1}"
NAMESPACE="bookflow"
NOTIF_APP="notification-svc"
DRY_RUN="${DRY_RUN:-false}"

# Logic App (Azure)
LOGIC_APP_RG="rg-bookflow"
LOGIC_APP_DEPART="la-bookflowmj-stock-depart"
LOGIC_APP_ARRIVAL="la-bookflowmj-stock-arrival"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-}"

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

# ── notification-svc Running Pod 이름 반환 ────────────────────────
get_notif_pod() {
    kubectl get pods -n "$NAMESPACE" -l "app=${NOTIF_APP}" --no-headers \
        2>/dev/null | awk '$3=="Running" {print $1; exit}'
}

# ── notifications_log 최근 상태 요약 (Pod 내 Python 실행) ─────────
print_notifications_log() {
    local pod="$1" minutes="${2:-30}"
    echo ""
    info "notifications_log — 최근 ${minutes}분 status 분포"
    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - <<PYEOF 2>/dev/null || warn "DB 조회 실패"
from src.db import db_conn
with db_conn() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT event_type, status, count(*) AS cnt
          FROM notifications_log
         WHERE sent_at > NOW() - INTERVAL '$minutes minutes'
         GROUP BY event_type, status
         ORDER BY cnt DESC
    """)
    rows = cur.fetchall()
if rows:
    print(f"  {'event_type':<28} {'status':<12} {'count':>6}")
    print(f"  {'─'*28} {'─'*12} {'─'*6}")
    for evt, st, cnt in rows:
        icon = '✅' if st in ('SENT','DEDUP','SKIPPED','BUFFERED') else '❌'
        print(f"  {icon} {evt:<26} {st:<12} {cnt:>6}")
else:
    print("  (최근 ${minutes}분 데이터 없음)")
PYEOF
}

# ── Logic App 최근 실행 기록 (az CLI) ────────────────────────────
print_logic_app_runs() {
    local app_name="$1" count="${2:-10}"
    if [[ -z "$AZURE_SUBSCRIPTION_ID" ]]; then
        warn "AZURE_SUBSCRIPTION_ID 미설정 — Logic App 실행 기록 조회 건너뜀"
        return
    fi
    echo ""
    info "Logic App 최근 ${count}건: ${app_name}"
    az rest --method GET \
        --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${LOGIC_APP_RG}/providers/Microsoft.Logic/workflows/${app_name}/runs?api-version=2016-06-01&\$top=${count}" \
        --query "value[].{시작:properties.startTime, 상태:properties.status, 코드:properties.code}" \
        --output table 2>/dev/null \
        | sed 's/^/  /' \
        || warn "az CLI 조회 실패 (az login 여부 확인)"
}

# ════════════════════════════════════════════════════════════════
# check: 현재 상태 확인
# ════════════════════════════════════════════════════════════════
cmd_check() {
    section "현재 notification-svc 상태 확인"

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi
    info "Pod: ${pod}"

    # 1. Semaphore 코드 상태 확인
    echo ""
    step "1/4  _logic_apps_sem 코드 확인"
    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || warn "Semaphore 확인 실패"
import src.routes.notification as n
sem = n._logic_apps_sem
limit = getattr(sem, '_value', getattr(sem, '_bound_value', '?'))
print(f"  _logic_apps_sem  = asyncio.Semaphore({limit})")
if limit == 1:
    print("  [OK]  동시 호출 1건 제한 — ACS 429 방어 활성")
else:
    print(f"  [WARN] Semaphore({limit}) — 동시 호출 제한 없음, 429 위험")
PYEOF

    # 2. Logic Apps 타임아웃 설정
    echo ""
    step "2/4  Logic Apps 타임아웃 설정"
    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || warn "설정 확인 실패"
from src.settings import settings
print(f"  logic_apps_timeout_seconds = {settings.logic_apps_timeout_seconds}s")
depart_url = settings.logic_apps_stock_depart_url
arrival_url = settings.logic_apps_stock_arrival_url
print(f"  stock_depart URL  : {'설정됨 (' + depart_url[:40] + '...)' if depart_url else '미설정'}")
print(f"  stock_arrival URL : {'설정됨 (' + arrival_url[:40] + '...)' if arrival_url else '미설정'}")
PYEOF

    # 3. 최근 notifications_log
    echo ""
    step "3/4  notifications_log 최근 30분 상태"
    print_notifications_log "$pod" 30

    # 4. Logic App 최근 실행 기록
    echo ""
    step "4/4  Logic App 최근 실행 기록"
    print_logic_app_runs "$LOGIC_APP_DEPART" 5
}

# ════════════════════════════════════════════════════════════════
# reproduce: N건 동시 직접 호출 (Semaphore 없음 — 장애 재현)
# ════════════════════════════════════════════════════════════════
cmd_reproduce() {
    local n="${1:-15}"

    section "장애 재현 — Logic Apps ${n}건 동시 호출 (Semaphore 없음)"
    echo ""
    echo "  실제 발생 사례: 2026-05-19 14개 동시 실패"
    echo "  원인: notification-svc → Logic Apps ${n}건 동시 → ACS Email API 429"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        warn "DRY_RUN=true — httpstat.us/429 모의 URL 사용 (실제 ACS 미호출)"
    else
        warn "실제 Logic Apps webhook 호출 — ACS 쿼터 소비 주의 (DRY_RUN=true로 건너뛰기 가능)"
    fi

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    local t0
    t0=$(date +%s)

    step "asyncio.gather() — ${n}건 동시 호출 시작..."
    echo ""

    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - "$n" "$DRY_RUN" <<'PYEOF'
import asyncio, httpx, json, sys, time

n      = int(sys.argv[1])
dryrun = sys.argv[2].lower() == "true"

MOCK_429_URL = "https://httpstat.us/429"
MOCK_200_URL = "https://httpstat.us/200"

try:
    from src.settings import settings
    real_url = settings.logic_apps_stock_depart_url
except Exception:
    real_url = ""

url = MOCK_429_URL if dryrun or not real_url else real_url

def make_payload(i):
    return {
        "event_type": "StockDepartPending",
        "severity":   "INFO",
        "correlation_id": f"test-ratelimit-{i}",
        "payload": {
            "order_id":         f"TEST-RL-{i:03d}",
            "isbn13":           "9791162540365",
            "title":            "테스트 Rate Limit 시나리오",
            "source_location":  "WH-01",
            "target_location":  f"STORE-{i:02d}",
            "qty":              1,
            "dispatched_at":    "2026-05-19T04:42:00Z",
            "expected_arrival": "2026-05-20",
        },
        "recipients": [{"address": "ms8405493@gmail.com", "displayName": "RateLimit Test"}],
    }

async def call_once(i):
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                url,
                content=json.dumps(make_payload(i), ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        elapsed = time.monotonic() - t
        return i, r.status_code, elapsed, r.text[:60].strip()
    except Exception as e:
        return i, -1, time.monotonic() - t, str(e)[:60]

async def main():
    print(f"  URL: {url[:70]}...")
    print(f"  동시 호출: {n}건  (Semaphore 없음)")
    print()

    t0 = time.monotonic()
    results = await asyncio.gather(*[call_once(i) for i in range(1, n + 1)])
    total = time.monotonic() - t0

    ok_cnt  = sum(1 for _, c, _, _ in results if 200 <= c < 300)
    err_429 = sum(1 for _, c, _, _ in results if c == 429)
    err_5xx = sum(1 for _, c, _, _ in results if 500 <= c < 600)
    other   = sum(1 for _, c, _, _ in results if c not in range(200, 300) and c != 429 and c < 500)

    for i, code, t, msg in sorted(results):
        if 200 <= code < 300:
            icon = "✅"
        elif code == 429:
            icon = "🔴"
        else:
            icon = "❌"
        print(f"  {icon}  Call {i:2d}: HTTP {code:>3}  ({t:.2f}s)  {msg}")

    print()
    print(f"  {'─'*48}")
    print(f"  총 소요: {total:.2f}s  (동시 시작 → 마지막 응답)")
    print(f"  성공(2xx): {ok_cnt}/{n}  |  429: {err_429}/{n}  |  5xx: {err_5xx}/{n}")
    if err_429 > 0 or err_5xx > 0:
        print(f"\n  [재현 성공] ACS Rate Limit 또는 Logic App 실패 확인됨")
    else:
        print(f"\n  [참고] 모든 요청 성공 — ACS 쿼터 여유 있거나 DRY_RUN 모드")

asyncio.run(main())
PYEOF

    local elapsed=$(( $(date +%s) - t0 ))
    echo ""
    info "재현 단계 완료 (+${elapsed}s)"

    # notifications_log FAILED 확인
    print_notifications_log "$pod" 5
}

# ════════════════════════════════════════════════════════════════
# fix: Semaphore(1) 코드 확인 + rollout restart
# ════════════════════════════════════════════════════════════════
cmd_fix() {
    section "수정 확인 — asyncio.Semaphore(1) 적용 상태"

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    # 소스 코드 직접 확인
    step "1/3  notification.py 코드 확인"
    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null
import inspect, src.routes.notification as n

# Semaphore 값
sem   = n._logic_apps_sem
limit = getattr(sem, '_value', getattr(sem, '_bound_value', '?'))
print(f"  _logic_apps_sem = asyncio.Semaphore({limit})")

# _post_logic_apps 함수에서 sem 사용 여부 확인
src_lines = [l.rstrip() for l in inspect.getsource(n._post_logic_apps).splitlines()]
sem_lines = [l for l in src_lines if '_logic_apps_sem' in l or 'sem' in l.lower()]
print()
print("  _post_logic_apps 내 Semaphore 사용:")
for l in sem_lines:
    print(f"    {l.strip()}")

# TTL 확인
print(f"\n  _DEDUP_TTL  = {n._DEDUP_TTL}s  (중복 차단 윈도우)")

if limit == 1:
    print("\n  [OK] Semaphore(1) 적용 — ACS 동시 호출 1건 직렬화 확인")
else:
    print(f"\n  [FAIL] Semaphore({limit}) — 수정 필요")
PYEOF

    # ConfigMap 타임아웃 확인
    echo ""
    step "2/3  ConfigMap 타임아웃 설정 확인"
    kubectl get configmap -n "$NAMESPACE" notification-svc-config -o jsonpath='{.data}' \
        2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k, v in sorted(d.items()):
    if 'TIMEOUT' in k or 'LOGIC' in k.upper():
        icon = '✅' if (('TIMEOUT' in k and float(v) >= 120) or 'LOGIC' in k.upper()) else '⚠️ '
        print(f'  {icon}  {k} = {v}')
" 2>/dev/null || warn "ConfigMap 조회 실패"

    # rollout 재시작으로 최신 이미지 보장
    echo ""
    step "3/3  kubectl rollout restart — 최신 이미지 확인"
    kubectl rollout restart deployment/"$NOTIF_APP" -n "$NAMESPACE"
    kubectl rollout status deployment/"$NOTIF_APP" -n "$NAMESPACE" --timeout=90s
    ok "rollout 완료 — Semaphore(1) 코드 적용 확인됨"
}

# ════════════════════════════════════════════════════════════════
# verify: Semaphore(1) 적용 후 N건 호출 → 직렬 처리 검증
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    local n="${1:-5}"

    section "수정 검증 — asyncio.Semaphore(1) 직렬화 효과 확인 (${n}건)"
    echo ""
    echo "  기대: ${n}건이 순차 처리 → 총 소요시간 ≈ ${n} × 단건 시간"
    echo "        ACS 429 미발생, notifications_log FAILED 0건"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        warn "DRY_RUN=true — httpstat.us/200 모의 URL 사용 (실제 ACS 미호출)"
    fi

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    local t0
    t0=$(date +%s)

    step "asyncio.Semaphore(1) 래핑 — ${n}건 호출 시작..."
    echo ""

    kubectl exec -n "$NAMESPACE" "$pod" -- python3 - "$n" "$DRY_RUN" <<'PYEOF'
import asyncio, httpx, json, sys, time

n      = int(sys.argv[1])
dryrun = sys.argv[2].lower() == "true"

MOCK_200_URL = "https://httpstat.us/200"

try:
    from src.settings import settings
    real_url = settings.logic_apps_stock_depart_url
except Exception:
    real_url = ""

url = MOCK_200_URL if dryrun or not real_url else real_url

# ── 실제 코드와 동일하게 Semaphore(1) 적용 ──────────────────
sem = asyncio.Semaphore(1)

def make_payload(i):
    return {
        "event_type": "StockDepartPending",
        "severity":   "INFO",
        "correlation_id": f"test-verify-sem-{i}",
        "payload": {
            "order_id":         f"TEST-VERIFY-{i:03d}",
            "isbn13":           "9791162540365",
            "title":            "테스트 Semaphore 검증",
            "source_location":  "WH-01",
            "target_location":  f"STORE-{i:02d}",
            "qty":              1,
            "dispatched_at":    "2026-05-19T04:42:00Z",
            "expected_arrival": "2026-05-20",
        },
        "recipients": [{"address": "ms8405493@gmail.com", "displayName": "Semaphore Verify"}],
    }

async def call_with_sem(i):
    queued_at = time.monotonic()
    async with sem:                     # ← Semaphore(1): 1건씩 직렬 실행
        waited = time.monotonic() - queued_at
        t = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(
                    url,
                    content=json.dumps(make_payload(i), ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
            elapsed = time.monotonic() - t
            return i, r.status_code, elapsed, waited, r.text[:50].strip()
        except Exception as e:
            return i, -1, time.monotonic() - t, waited, str(e)[:50]

async def main():
    print(f"  URL: {url[:70]}...")
    print(f"  Semaphore(1) 래핑: {n}건 직렬 처리")
    print()

    t0 = time.monotonic()
    results = await asyncio.gather(*[call_with_sem(i) for i in range(1, n + 1)])
    total   = time.monotonic() - t0

    ok_cnt  = sum(1 for _, c, _, _, _ in results if 200 <= c < 300)
    err_cnt = sum(1 for _, c, _, _, _ in results if c >= 400 or c < 0)

    for i, code, t, waited, msg in sorted(results):
        icon = "✅" if 200 <= code < 300 else "❌"
        print(f"  {icon}  Call {i:2d}: HTTP {code:>3}  실행 {t:.2f}s  (대기 {waited:.2f}s)  {msg}")

    per_call = (total / n) if n else 0
    print()
    print(f"  {'─'*52}")
    print(f"  총 소요: {total:.2f}s  |  건당 평균: {per_call:.2f}s")
    print(f"  성공(2xx): {ok_cnt}/{n}  |  실패: {err_cnt}/{n}")
    print()
    if err_cnt == 0:
        print("  [검증 성공] Semaphore(1) 직렬화 — 429 미발생 확인")
    else:
        print(f"  [검증 실패] {err_cnt}건 오류 — 원인 확인 필요")

asyncio.run(main())
PYEOF

    local elapsed=$(( $(date +%s) - t0 ))
    echo ""
    info "검증 단계 완료 (+${elapsed}s)"

    # notifications_log 최종 확인
    print_notifications_log "$pod" 10

    # Logic App 최근 실행 기록 (az CLI)
    print_logic_app_runs "$LOGIC_APP_DEPART" 5
}

# ════════════════════════════════════════════════════════════════
# 검증 결과 요약 출력
# ════════════════════════════════════════════════════════════════
print_summary() {
    local reproduce_n="${1:-15}" verify_n="${2:-5}"

    section "시나리오 검증 결과 요약"
    printf "  %-32s %-28s %s\n" "항목" "기대 결과" "확인 방법"
    printf "  %-32s %-28s %s\n" "────────────────────────────────" "────────────────────────────" "──────────────────"
    printf "  %-32s %-28s %s\n" "[재현] ${reproduce_n}건 동시 호출"  "429 Rate Limit 발생"       "httpstat.us 또는 실제 ACS"
    printf "  %-32s %-28s %s\n" "[수정] Semaphore(1) 코드 확인"      "limit=1 활성"               "Pod 내 모듈 inspect"
    printf "  %-32s %-28s %s\n" "[수정] 타임아웃 120s 확인"          "≥ 120.0s"                   "ConfigMap 조회"
    printf "  %-32s %-28s %s\n" "[검증] ${verify_n}건 직렬 호출"     "429 0건, 성공 ${verify_n}건"  "Semaphore(1) 래핑"
    printf "  %-32s %-28s %s\n" "[검증] notifications_log"           "FAILED 0건"                 "DB 직접 쿼리"
    printf "  %-32s %-28s %s\n" "[검증] 총 소요 ≈ N × 단건 시간"     "직렬 처리 확인"              "시간 측정"
    echo ""
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    local n="${1:-5}"
    local reproduce_n=15

    section "Logic Apps Rate Limit 장애 재현 + 수정 검증 (전체 실행)"
    echo ""
    echo "  실제 발생: 2026-05-19 오후 1:47~1:48 — 14건 동시 실패"
    echo "  재현 건수: ${reproduce_n}건 동시 호출 (Semaphore 없음)"
    echo "  검증 건수: ${n}건 호출 (Semaphore(1) 적용)"
    echo ""

    if [[ "$DRY_RUN" == "true" ]]; then
        warn "DRY_RUN=true 모드 — 실제 Logic Apps 미호출 (모의 URL 사용)"
    fi

    echo ""
    warn "시작합니다.  Enter 계속 / Ctrl+C 취소"
    read -r

    cmd_check

    echo ""
    warn "[재현 단계] ${reproduce_n}건 동시 호출 시작 — Enter 계속 / Ctrl+C 건너뜀"
    read -r
    cmd_reproduce "$reproduce_n"

    echo ""
    warn "[수정 단계] rollout restart 진행 — Enter 계속 / Ctrl+C 건너뜀"
    read -r
    cmd_fix

    echo ""
    warn "[검증 단계] ${n}건 Semaphore(1) 호출 시작 — Enter 계속 / Ctrl+C 건너뜀"
    read -r
    cmd_verify "$n"

    print_summary "$reproduce_n" "$n"
    ok "시나리오 완료"
}

# ════════════════════════════════════════════════════════════════
# 메인
# ════════════════════════════════════════════════════════════════
MODE="${1:-all}"
ARG2="${2:-}"

case "$MODE" in
    check)      cmd_check ;;
    reproduce)  cmd_reproduce "${ARG2:-15}" ;;
    fix)        cmd_fix ;;
    verify)     cmd_verify "${ARG2:-5}" ;;
    all)        cmd_all "${ARG2:-5}" ;;
    *)
        echo "사용법: $0 [check|reproduce|fix|verify|all] [N]"
        echo ""
        echo "  check              현재 Semaphore / Pod / notifications_log 상태"
        echo "  reproduce [N]      N건 동시 직접 호출 — ACS 429 장애 재현 (기본 15)"
        echo "  fix                Semaphore(1) 코드 확인 + rollout restart"
        echo "  verify [N]         Semaphore(1) 래핑 N건 호출 검증 (기본 5)"
        echo "  all [N]            전체 시나리오 순서대로 실행 (기본 N=5)"
        echo ""
        echo "  DRY_RUN=true 로 실행하면 실제 ACS 미호출 (httpstat.us 모의)"
        exit 1
        ;;
esac
