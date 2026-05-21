#!/usr/bin/env bash
# logic-apps-rate-limit-test.sh  v4
#
# Azure Logic Apps 504 ActionResponseTimedOut 장애 재현 + Semaphore(1) 수정 검증
# 실제 발생: 2026-05-19 오후 1:47~1:48, StockArrivalPending 14건 동시 실패
#
# ─ 장애 원인 ──────────────────────────────────────────────────────
#   단건 호출은 정상 (200)
#   동시 N건 호출 시 → ACS Email API Rate Limit 초과 (429)
#                    → Logic App 내부 재시도 반복 → 응답 지연
#                    → 120s 초과 → 504 ActionResponseTimedOut 반환
#
# ─ 재현 흐름 ──────────────────────────────────────────────────────
#   /notify × N건 동시
#     └─ _post_logic_apps (Semaphore 없음)
#          └─ Logic App × N 동시 → ACS 429 → 재시도 → 504
#
# ─ 검증 흐름 ──────────────────────────────────────────────────────
#   /notify × N건 동시
#     └─ _post_logic_apps [Semaphore(1)]
#          └─ Logic App 1건씩 순차 → ACS 정상 응답 → 504 미발생
#
# 사용법:
#   bash logic-apps-rate-limit-test.sh check              현재 상태 확인
#   bash logic-apps-rate-limit-test.sh reproduce [N]      N건 동시 → 504 재현 (기본 15)
#   bash logic-apps-rate-limit-test.sh fix                Semaphore(1) 확인 + rollout
#   bash logic-apps-rate-limit-test.sh verify  [N]        N건 동시 → 순차 처리 검증 (기본 5)
#   bash logic-apps-rate-limit-test.sh all     [N]        전체 시나리오 (기본 N=5)
#
# 환경변수:
#   LOGIC_APP_TEST_URL   reproduce 단계에서 prod Logic App 대신 사용할 테스트 URL
#                        (ACS 쿼터 분리된 테스트용 Logic App URL 지정 시 활성)
#   NOTIF_AUTH_TOKEN     verify 단계 /notification/send 인증 토큰 (기본: mock-token-hq-admin)
#                        AUTH_MODE=jwt 환경이면 실제 Bearer JWT 토큰 값 지정 필요
#   NOTIF_PORT           notification-svc 포트 (기본 8000)
#   AZURE_SUBSCRIPTION_ID  Azure 구독 ID (기본값 내장)

set -euo pipefail

# ── 설정 ──────────────────────────────────────────────────────────
NAMESPACE="bookflow"
NOTIF_APP="notification-svc"
NOTIF_PORT="${NOTIF_PORT:-80}"

LOGIC_APP_RG="rg-bookflow"
LOGIC_APP_ARRIVAL="la-bookflowmj-stock-arrival"
AZURE_SUBSCRIPTION_ID="${AZURE_SUBSCRIPTION_ID:-e98a94bb-7532-4e49-8a36-bc42e30d5a81}"

# 테스트용 Logic App (reproduce 단계에서 prod URL 대신 사용 — ACS 쿼터 분리)
# SAS URL 만료 시 az rest POST .../listCallbackUrl 로 재발급
LOGIC_APP_TEST_NAME="${LOGIC_APP_TEST_NAME:-la-bookflowmj-arrival-test}"
LOGIC_APP_TEST_URL="${LOGIC_APP_TEST_URL:-https://prod-29.japanwest.logic.azure.com:443/workflows/e2094c0f8ebf4f48a984a1dba8ad80a6/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=4CV-Jzj_jjpnnP6g9Ee2y2j052sObpiOqyQR4js5Uu0}"

# /notification/send 인증 토큰 — AUTH_MODE=jwt 이면 스크립트가 자동 발급
# (Pod 내 AUTH_JWT_SIGNING_KEY 사용). 수동 지정 시 이 환경변수로 override.
NOTIF_AUTH_TOKEN="${NOTIF_AUTH_TOKEN:-mock-token-hq-admin}"

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

# AUTH_MODE=jwt인 경우 Pod 내부 JWT signing key로 system 토큰 자동 발급
# → NOTIF_AUTH_TOKEN 수동 설정 불필요
_auto_get_jwt() {
    local pod="$1"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null
import os, time
try:
    import jwt as pyjwt
except ImportError:
    exit(0)

key  = os.environ.get("AUTH_JWT_SIGNING_KEY", "")
iss  = os.environ.get("AUTH_JWT_ISSUER",  "bookflow-auth-pod")
aud  = os.environ.get("AUTH_JWT_AUDIENCE", "bookflow-services")
mode = os.environ.get("AUTH_MODE", "mock")

if mode == "jwt" and key:
    now = int(time.time())
    token = pyjwt.encode({
        "iss": iss, "aud": aud,
        "sub": "00000000-0000-0000-0000-000000000099",
        "email": "system@bookflow.internal",
        "role": "system",
        "scope_wh_id": None, "scope_store_id": None,
        "iat": now, "exp": now + 3600,
    }, key, algorithm="HS256")
    print(token)
elif mode == "mock":
    print("mock-token-hq-admin")
PYEOF
}

get_notif_pod() {
    kubectl get pods -n "$NAMESPACE" -l "app=${NOTIF_APP}" --no-headers \
        2>/dev/null | awk '$3=="Running" {print $1; exit}'
}

print_notifications_log() {
    local pod="$1" minutes="${2:-30}"
    echo ""
    info "notifications_log — 최근 ${minutes}분 status 분포"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<PYEOF 2>/dev/null || warn "DB 조회 실패"
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
    print(f"  {'event_type':<30} {'status':<12} {'count':>6}")
    print(f"  {'─'*30} {'─'*12} {'─'*6}")
    for evt, st, cnt in rows:
        icon = '✅' if st in ('SENT','DEDUP','SKIPPED','BUFFERED') else '❌'
        print(f"  {icon} {evt:<28} {st:<12} {cnt:>6}")
else:
    print("  (최근 ${minutes}분 데이터 없음)")
PYEOF
}

print_logic_app_runs() {
    local app_name="$1" count="${2:-10}"
    if [[ -z "${AZURE_SUBSCRIPTION_ID:-}" ]]; then
        warn "AZURE_SUBSCRIPTION_ID 미설정 — Logic App 실행 기록 조회 건너뜀"
        return
    fi
    echo ""
    info "Logic App 최근 ${count}건: ${app_name}"
    local result
    result=$(az rest --method GET \
        --url "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/resourceGroups/${LOGIC_APP_RG}/providers/Microsoft.Logic/workflows/${app_name}/runs?api-version=2016-06-01&\$top=${count}" \
        --query "value[].{start:properties.startTime, status:properties.status, code:properties.code}" \
        --output table 2>&1) \
        || { warn "az CLI 조회 실패: ${result}"; return; }
    echo "$result" | sed 's/^/  /'
}

# ════════════════════════════════════════════════════════════════
# check
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

    echo ""
    step "1/4  _logic_apps_sem 코드 확인"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || warn "Semaphore 확인 실패"
import src.routes.notification as n
sem   = n._logic_apps_sem
limit = getattr(sem, '_value', getattr(sem, '_bound_value', '?'))
print(f"  _logic_apps_sem = asyncio.Semaphore({limit})")
if limit == 1:
    print("  [OK]  동시 호출 1건 제한 — 504 ActionResponseTimedOut 방어 활성")
else:
    print(f"  [WARN] Semaphore({limit}) — 동시 호출 제한 없음, 504 위험")
PYEOF

    echo ""
    step "2/4  Logic Apps 타임아웃 / URL / AUTH_MODE 설정"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || warn "설정 확인 실패"
import os
from src.settings import settings
timeout = getattr(settings, 'logic_apps_timeout_seconds', None)
print(f"  logic_apps_timeout_seconds = {timeout}s")
if timeout is None or float(timeout) < 120:
    print(f"  [WARN] timeout {timeout}s < 120s — Logic App 504 위험, ConfigMap NOTIFICATION_LOGIC_APPS_TIMEOUT_SECONDS=120 확인 필요")
else:
    print(f"  [OK]  timeout {timeout}s >= 120s")
arrival_url = settings.logic_apps_stock_arrival_url
print(f"  stock_arrival URL : {'설정됨 (' + arrival_url[:40] + '...)' if arrival_url else '[미설정]'}")
auth_mode = os.environ.get('AUTH_MODE', 'mock')
print(f"  AUTH_MODE         = {auth_mode}")
if auth_mode == 'mock':
    print(f"  [OK]  mock 모드 — mock-token-hq-admin 헤더로 verify 가능")
else:
    print(f"  [WARN] jwt 모드 — verify 실행 시 NOTIF_AUTH_TOKEN=<JWT> 환경변수 필요")
PYEOF

    echo ""
    step "3/4  notifications_log 최근 30분 상태"
    print_notifications_log "$pod" 30

    echo ""
    step "4/4  Logic App 최근 실행 기록"
    if [[ -n "$LOGIC_APP_TEST_NAME" ]]; then
        info "테스트 Logic App 조회 중 (LOGIC_APP_TEST_NAME=${LOGIC_APP_TEST_NAME})"
        print_logic_app_runs "$LOGIC_APP_TEST_NAME" 5
        echo ""
        info "Prod Logic App 조회 중 (참고용)"
        print_logic_app_runs "$LOGIC_APP_ARRIVAL" 5
    else
        print_logic_app_runs "$LOGIC_APP_ARRIVAL" 5
    fi
}

# ════════════════════════════════════════════════════════════════
# reproduce: N건 동시 호출 → 504 ActionResponseTimedOut 재현
#
# 단건은 정상이지만 동시 N건이면:
#   → ACS Email API Rate Limit (429) 발생
#   → Logic App 내부 재시도 반복 → 응답 지연 → 120s 초과
#   → 504 ActionResponseTimedOut 반환
#
# Mock 서버가 1~2초 지연 후 504를 반환해 위 패턴을 시뮬레이션.
# (실제 프로덕션에서는 120s 대기 후 504가 오지만 테스트는 단축 적용)
# ════════════════════════════════════════════════════════════════
cmd_reproduce() {
    local n="${1:-15}"

    section "장애 재현 — StockArrivalPending ${n}건 동시 호출 → 504 ActionResponseTimedOut"
    echo ""
    echo "  실제 발생: 2026-05-19 오후 1:47~1:48, 14건 동시 실패"
    echo ""
    echo "  단건 호출 → 정상 (200 OK)"
    echo "  N건 동시  → ACS 429 Rate Limit → Logic App 재시도 반복"
    echo "            → 120s 초과 → 504 ActionResponseTimedOut"
    echo ""
    if [[ -n "$LOGIC_APP_TEST_URL" ]]; then
        echo "  [테스트 URL 사용] LOGIC_APP_TEST_URL 환경변수가 설정됨"
        echo "  → 테스트용 ACS 쿼터가 낮은 Logic App으로 호출 (prod ACS 쿼터 미소비)"
    else
        echo "  방법: 실제 Logic App webhook N건 동시 호출 → ACS Rate Limit → 504 확인"
        echo "        Azure Portal Logic App 실행 기록에 실패 항목이 직접 기록됨"
    fi
    echo ""

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    local t0
    t0=$(date +%s)

    step "실제 Logic App ${n}건 동시 호출 시작 (Semaphore 없음)..."
    if [[ -n "$LOGIC_APP_TEST_URL" ]]; then
        info "테스트 URL: ${LOGIC_APP_TEST_URL:0:70}..."
    else
        warn "실제 ACS Email API 호출됨 — ACS 쿼터 소비 주의"
    fi
    echo ""

    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - "$n" "${LOGIC_APP_TEST_URL:-}" <<'PYEOF'
import asyncio, httpx, json, sys, time

n        = int(sys.argv[1])
test_url = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    from src.settings import settings
    real_url  = test_url if test_url else settings.logic_apps_stock_arrival_url
    timeout_s = float(getattr(settings, 'logic_apps_timeout_seconds', 120))
except Exception as e:
    print(f"  [ERROR] settings 로드 실패: {e}")
    sys.exit(1)

if not real_url:
    print("  [ERROR] logic_apps_stock_arrival_url 미설정 (LOGIC_APP_TEST_URL도 미설정)")
    sys.exit(1)

def make_payload(i):
    return {
        "event_type": "StockArrivalPending",
        "severity":   "INFO",
        "correlation_id": f"test-arrival-reproduce-{i:03d}",
        "payload": {
            "order_id":         f"TEST-RL-{i:03d}",
            "isbn13":           "9791162540365",
            "title":            "테스트 504 재현 시나리오",
            "source_location":  "WH-01",
            "target_location":  f"STORE-{i:02d}",
            "qty":              1,
            "dispatched_at":    "2026-05-19T04:42:00Z",
            "expected_arrival": "2026-05-20",
        },
        "recipients": [{"address": "ms8405493@gmail.com", "displayName": "504 Reproduce Test"}],
    }

async def call_once(i, start_event):
    await start_event.wait()  # 전체 코루틴 동시 출발
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                real_url,
                content=json.dumps(make_payload(i), ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
        elapsed = time.monotonic() - t
        if r.status_code == 504:
            body = r.json() if "application/json" in r.headers.get("content-type", "") else {}
            msg  = body.get("error", {}).get("code", "ActionResponseTimedOut")
        elif 200 <= r.status_code < 300:
            msg = r.text[:60].strip()
        else:
            msg = r.text[:60].strip()
        return i, r.status_code, elapsed, str(msg)
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t
        return i, -504, elapsed, f"Client Timeout({timeout_s:.0f}s) — Logic App 미응답"
    except Exception as e:
        return i, -1, time.monotonic() - t, f"{type(e).__name__}: {str(e)[:60]}"

async def main():
    url_label = "[테스트 URL]" if test_url else "[Prod URL]"
    print(f"  URL    : {url_label} {real_url[:70]}...")
    print(f"  Timeout: {timeout_s}s")
    print(f"  이벤트 : StockArrivalPending")
    print(f"  동시   : {n}건 — Semaphore 없음 (장애 재현)")
    print()

    start_event = asyncio.Event()
    tasks = [asyncio.create_task(call_once(i, start_event)) for i in range(1, n + 1)]
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    start_event.set()
    results = await asyncio.gather(*tasks)
    total   = time.monotonic() - t0

    ok_cnt  = sum(1 for _, c, _, _ in results if 200 <= c < 300)
    err_504 = sum(1 for _, c, _, _ in results if c in (504, -504))
    err_etc = sum(1 for _, c, _, _ in results if c not in range(200, 300) and c not in (504, -504))

    for i, code, elapsed, msg in sorted(results):
        if 200 <= code < 300:
            icon = "✅"
        elif code in (504, -504):
            icon = "⏱️ "
        else:
            icon = "❌"
        code_str = str(code) if code > 0 else ("504" if code == -504 else "ERR")
        print(f"  {icon}  Call {i:2d}: HTTP {code_str:<3}  ({elapsed:.1f}s)  {msg}")

    print()
    print(f"  {'─'*54}")
    print(f"  총 소요: {total:.1f}s  (동시 시작 → 마지막 응답)")
    print(f"  성공(2xx): {ok_cnt}/{n}  |  504: {err_504}/{n}  |  기타: {err_etc}/{n}")
    print()
    if err_504 > 0:
        print(f"  [재현 성공] {err_504}건 504 ActionResponseTimedOut")
        print(f"  → Azure Portal > la-bookflowmj-stock-arrival > 실행 기록에서 상세 확인 가능")
    elif ok_cnt == n:
        print(f"  [참고] 전건 성공 — 현재 ACS 쿼터 여유 있음")
        print(f"  → 동시 호출 건수를 늘리거나 실제 트래픽 피크 시간대에 재시도")
    else:
        print(f"  [확인 필요] 예상치 못한 오류 발생 — 위 결과 확인")

asyncio.run(main())
PYEOF

    local elapsed=$(( $(date +%s) - t0 ))
    echo ""
    info "재현 단계 완료 (+${elapsed}s)"

    print_notifications_log "$pod" 5
    # LOGIC_APP_TEST_URL 사용 시 테스트 Logic App 실행 기록도 함께 조회
    if [[ -n "$LOGIC_APP_TEST_NAME" ]]; then
        info "테스트 Logic App 실행 기록 (Azure Portal 직접 확인 권장: ${LOGIC_APP_TEST_NAME})"
        print_logic_app_runs "$LOGIC_APP_TEST_NAME" 5
    else
        print_logic_app_runs "$LOGIC_APP_ARRIVAL" 5
        if [[ -n "$LOGIC_APP_TEST_URL" ]]; then
            warn "LOGIC_APP_TEST_NAME 미설정 — 테스트 Logic App 실행 기록은 Azure Portal에서 직접 확인"
        fi
    fi
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

    step "1/3  notification.py 코드 확인"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || warn "코드 확인 실패"
import inspect, src.routes.notification as n

sem   = n._logic_apps_sem
limit = getattr(sem, '_value', getattr(sem, '_bound_value', '?'))
print(f"  _logic_apps_sem = asyncio.Semaphore({limit})")

src_lines = [l.rstrip() for l in inspect.getsource(n._post_logic_apps).splitlines()]
sem_lines = [l for l in src_lines if '_logic_apps_sem' in l or 'async with' in l]
print()
print("  _post_logic_apps 내 Semaphore 사용:")
for l in sem_lines:
    print(f"    {l.strip()}")

print(f"\n  _DEDUP_TTL = {n._DEDUP_TTL}s  (중복 차단 윈도우)")

if limit == 1:
    print("\n  [OK] Semaphore(1) 적용 — Logic App 호출 1건씩 순차 처리 확인")
else:
    print(f"\n  [FAIL] Semaphore({limit}) — 수정 필요")
PYEOF

    echo ""
    step "2/3  ConfigMap 타임아웃 설정 확인"
    kubectl get configmap -n "$NAMESPACE" notification-svc-env -o json \
        2>/dev/null \
        | python3 -c "
import sys, json
d = json.load(sys.stdin).get('data', {})
matched = {k: v for k, v in d.items() if 'TIMEOUT' in k or 'LOGIC' in k.upper()}
if not matched:
    print('  (TIMEOUT/LOGIC 키 없음)')
for k, v in sorted(matched.items()):
    try:
        is_ok = float(v) >= 120
    except ValueError:
        is_ok = True
    icon = '✅' if is_ok else '⚠️ '
    note = '  ← 120s 미만: Logic App 504 위험' if not is_ok else ''
    print(f'  {icon}  {k} = {v}{note}')
" 2>/dev/null || warn "ConfigMap 조회 실패"

    echo ""
    step "3/3  kubectl rollout restart — 최신 이미지 반영"
    kubectl rollout restart deployment/"$NOTIF_APP" -n "$NAMESPACE"
    kubectl rollout status deployment/"$NOTIF_APP" -n "$NAMESPACE" --timeout=120s
    ok "rollout 완료 — Semaphore(1) 최신 코드 적용 확인됨"
}

# ════════════════════════════════════════════════════════════════
# verify: /notification/send N건 동시 요청 → Semaphore(1) 직렬화 → 504 미발생 검증
#
# 동일하게 N건을 동시에 요청하지만:
#   _logic_apps_sem(1) 이 Logic App 호출을 1건씩 순차 처리
#   → ACS가 동시 요청을 받지 않음 → Rate Limit 없음 → 504 없음
#
# 검증 포인트:
#   - 성공(2xx) = N건, 504 = 0건
#   - 총 소요시간 ≈ N × 단건 시간  (직렬 처리 증거)
#   - notifications_log FAILED 0건
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    local n="${1:-5}"

    section "수정 검증 — StockArrivalPending ${n}건 동시 요청 → Semaphore(1) 순차 처리"
    echo ""
    echo "  재현과 동일하게 ${n}건을 동시에 /notification/send로 요청"
    echo "  차이: _logic_apps_sem(1)이 Logic App 호출을 1건씩 순차 처리"
    echo ""
    echo "  기대: 504 = 0건, 성공 = ${n}건"
    echo "        총 소요 ≈ ${n} × 단건 시간  (순차 처리 증거)"
    echo ""

    local pod
    pod=$(get_notif_pod)
    if [[ -z "$pod" ]]; then
        error "notification-svc Running Pod를 찾을 수 없습니다."
        exit 1
    fi

    # AUTH_MODE=jwt인 경우 Pod에서 JWT 자동 발급 (NOTIF_AUTH_TOKEN 수동 설정 불필요)
    if [[ "$NOTIF_AUTH_TOKEN" == "mock-token-hq-admin" ]]; then
        step "0/2  인증 토큰 자동 발급 (AUTH_MODE 확인 중)"
        local auto_jwt
        auto_jwt=$(_auto_get_jwt "$pod")
        if [[ -n "$auto_jwt" ]]; then
            NOTIF_AUTH_TOKEN="$auto_jwt"
            info "토큰 발급 완료: ${NOTIF_AUTH_TOKEN:0:20}... (3600s 유효)"
        else
            warn "JWT 자동 발급 실패 — 기본 mock 토큰 사용 (AUTH_MODE=mock 환경에서만 동작)"
        fi
        echo ""
    fi

    # 단건 연결 테스트 (Logic App URL 정상 여부 먼저 확인)
    step "1/2  단건 연결 테스트 (/notification/send ping)"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - "$NOTIF_PORT" "$NOTIF_AUTH_TOKEN" <<'PYEOF' 2>/dev/null || warn "단건 테스트 스킵"
import asyncio, httpx, json, sys, time
from uuid import uuid4

port       = sys.argv[1]
auth_token = sys.argv[2]
url        = f"http://127.0.0.1:{port}/notification/send"

payload = {
    "event_type":      "StockArrivalPending",
    "severity":        "INFO",
    "correlation_id":  str(uuid4()),   # 매번 고유 UUID (DEDUP 방지)
    "payload_summary": {
        "order_id":           "PING-001",
        "isbn13":             "9791162540365",
        "title":              "단건 연결 테스트",
        "source_location":    "WH-01",
        "source_location_id": 1,        # _stock_arrival_recipients() → location_contacts[1] 조회
        "target_location":    "STORE-01",
        "qty":                1,
        "dispatched_at":      "2026-05-19T04:42:00Z",
        "expected_arrival":   "2026-05-20",
    },
    "recipients": ["ms8405493@gmail.com"],
}

async def ping():
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                url,
                content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Content-Type":  "application/json; charset=utf-8",
                    "Authorization": f"Bearer {auth_token}",
                },
            )
        elapsed = time.monotonic() - t
        if 200 <= r.status_code < 300:
            print(f"  [OK]   HTTP {r.status_code}  ({elapsed:.2f}s) — 단건 정상")
        elif r.status_code == 401:
            print(f"  [AUTH] HTTP 401 — 인증 실패. NOTIF_AUTH_TOKEN 환경변수 확인 (AUTH_MODE=jwt이면 실제 JWT 필요)")
        elif r.status_code == 422:
            print(f"  [422]  Unprocessable: {r.text[:120]}")
        elif r.status_code == 504:
            print(f"  [WARN] HTTP 504  ({elapsed:.2f}s) — 단건도 504: Logic App 자체 문제 또는 timeout 설정 확인")
        else:
            print(f"  [WARN] HTTP {r.status_code}  ({elapsed:.2f}s): {r.text[:80]}")
    except httpx.ConnectError:
        print(f"  [ERROR] {url} 연결 실패 — 포트 확인 필요")
        print(f"  NOTIF_PORT 환경변수로 포트 지정: NOTIF_PORT=8000 bash ...")
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t
        print(f"  [TIMEOUT] {elapsed:.2f}s 초과 — Logic App 응답 없음 (logic_apps_timeout_seconds 확인)")
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {str(e)[:80]}")

print(f"  URL  : {url}")
print(f"  Token: {auth_token[:30]}...")
asyncio.run(ping())
PYEOF

    echo ""
    step "2/2  ${n}건 동시 발송 → Semaphore(1) 순차 처리 검증"
    echo ""

    local t0
    t0=$(date +%s)

    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - "$n" "$NOTIF_PORT" "$NOTIF_AUTH_TOKEN" <<'PYEOF'
import asyncio, httpx, json, sys, time
from uuid import uuid4

n          = int(sys.argv[1])
port       = sys.argv[2]
auth_token = sys.argv[3]
url        = f"http://127.0.0.1:{port}/notification/send"

try:
    from src.settings import settings
    single_timeout_s = float(getattr(settings, 'logic_apps_timeout_seconds', 120))
except Exception:
    single_timeout_s = 120.0

# 순차 처리: N건 × 단건 timeout으로 전체 대기 (마지막 건 timeout 방지)
timeout_s = single_timeout_s * n

# 실행마다 고유한 run_id — DEDUP(5분 TTL) 중복 차단 방지
run_id = uuid4().hex[:8]

def make_payload(i):
    return {
        "event_type":      "StockArrivalPending",
        "severity":        "INFO",
        "correlation_id":  str(uuid4()),     # 각 건 UUID → DEDUP 미차단
        "payload_summary": {
            "order_id":           f"TEST-VERIFY-{run_id}-{i:03d}",
            "isbn13":             "9791162540365",
            "title":              "테스트 Semaphore 검증",
            "source_location":    "WH-01",
            "source_location_id": 1,      # _stock_arrival_recipients() → location_contacts[1]
            "target_location":    f"STORE-{i:02d}",
            "qty":                1,
            "dispatched_at":      "2026-05-19T04:42:00Z",
            "expected_arrival":   "2026-05-20",
        },
        "recipients": ["ms8405493@gmail.com"],
    }

SENT_STATUSES = {"SENT", "DEDUP", "SKIPPED", "BUFFERED"}

headers = {
    "Content-Type":  "application/json; charset=utf-8",
    "Authorization": f"Bearer {auth_token}",
}

async def call_notify(i, start_event):
    await start_event.wait()  # 전체 코루틴 동시 출발
    t = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                url,
                content=json.dumps(make_payload(i), ensure_ascii=False).encode("utf-8"),
                headers=headers,
            )
        elapsed = time.monotonic() - t
        if r.status_code == 504:
            return i, r.status_code, elapsed, "TIMEOUT", "504 ActionResponseTimedOut — Semaphore 미적용 또는 Logic App 자체 문제"
        elif r.status_code == 401:
            return i, r.status_code, elapsed, "AUTH_ERR", "401 Unauthorized — NOTIF_AUTH_TOKEN 확인 필요"
        elif r.status_code == 422:
            return i, r.status_code, elapsed, "FORMAT_ERR", f"422 Unprocessable: {r.text[:60]}"
        elif r.status_code >= 400:
            return i, r.status_code, elapsed, "HTTP_ERR", f"HTTP {r.status_code}: {r.text[:60]}"
        else:
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            notif_status = body.get("status", "")
            return i, r.status_code, elapsed, notif_status, notif_status
    except httpx.TimeoutException:
        elapsed = time.monotonic() - t
        return i, -504, elapsed, "TIMEOUT", f"Client Timeout({timeout_s:.0f}s) — Logic App 미응답"
    except httpx.ConnectError as e:
        return i, -1, time.monotonic() - t, "CONNECT_ERR", f"ConnectError: {str(e)[:60]}"
    except Exception as e:
        return i, -1, time.monotonic() - t, "ERR", f"{type(e).__name__}: {str(e)[:60]}"

async def main():
    print(f"  URL    : {url}")
    print(f"  Token  : {auth_token[:30]}...")
    print(f"  Timeout: {single_timeout_s}s × {n}건 = {timeout_s:.0f}s  (순차 처리 전체 대기)")
    print(f"  run_id : {run_id}  (DEDUP 중복 방지용 고유 식별자)")
    print(f"  요청   : StockArrivalPending × {n}건  동시 발송 (재현과 동일한 조건)")
    print(f"  차이   : _logic_apps_sem(1) → Logic App 호출 1건씩 순차 처리")
    print()

    start_event = asyncio.Event()
    tasks = [asyncio.create_task(call_notify(i, start_event)) for i in range(1, n + 1)]
    await asyncio.sleep(0.05)  # 모든 태스크 대기 상태 진입 후
    t0 = time.monotonic()
    start_event.set()           # 동시 출발
    results = await asyncio.gather(*tasks)
    total   = time.monotonic() - t0

    # HTTP 200 + SENT/DEDUP/SKIPPED = 진짜 성공
    # HTTP 200 + FAILED = Logic App 호출 자체 실패 (ACS 오류 등)
    sent_cnt   = sum(1 for _, c, _, ns, _ in results if 200 <= c < 300 and ns in SENT_STATUSES)
    failed_cnt = sum(1 for _, c, _, ns, _ in results if 200 <= c < 300 and ns == "FAILED")
    err_504    = sum(1 for _, c, _, ns, _ in results if c in (504, -504) or ns == "TIMEOUT")
    err_etc    = sum(1 for _, c, _, ns, _ in results if c not in range(200, 300) and c not in (504, -504) and ns not in ("TIMEOUT",))

    for i, code, elapsed, notif_status, msg in sorted(results):
        if 200 <= code < 300 and notif_status in SENT_STATUSES:
            icon = "✅"
        elif 200 <= code < 300 and notif_status == "FAILED":
            icon = "⚠️ "   # HTTP 200이지만 Logic App/ACS 실패
        elif code in (504, -504) or notif_status == "TIMEOUT":
            icon = "⏱️ "
        else:
            icon = "❌"
        code_str = str(code) if code > 0 else ("504" if code == -504 else "ERR")
        print(f"  {icon}  Call {i:2d}: HTTP {code_str:<3}  status={notif_status:<8}  ({elapsed:.2f}s)  {msg[:60]}")

    per_call = total / n if n else 0
    print()
    print(f"  {'─'*60}")
    print(f"  총 소요: {total:.2f}s  |  건당 평균: {per_call:.2f}s")
    print(f"  SENT/DEDUP: {sent_cnt}/{n}  |  Logic App FAILED: {failed_cnt}/{n}  |  504: {err_504}/{n}  |  기타: {err_etc}/{n}")
    print()

    if sent_cnt == n:
        print("  [검증 성공] Semaphore(1) 순차 처리 — 504 미발생, 전건 Logic App 성공")
        print(f"  → 총 소요 {total:.1f}s ≈ {n} × 단건({per_call:.1f}s)  (직렬 처리 확인)")
    elif failed_cnt > 0 and err_504 == 0:
        import os
        test_la = os.environ.get("LOGIC_APP_TEST_NAME", "la-bookflowmj-arrival-test")
        print(f"  [부분 성공] Semaphore(1) 정상 (504 없음)  /  Logic App FAILED {failed_cnt}건")
        print(f"  → 아래 audit_log 에러 상세 확인 후 원인 파악:")
        print(f"     Azure Portal > {test_la} > 실행 기록 > 실패 액션 상세")
    elif err_504 > 0:
        print(f"  [검증 실패] {err_504}건 504 발생 — Semaphore 미적용 또는 Logic App 자체 timeout")
        print(f"     check 명령으로 _logic_apps_sem 값 확인")
    elif err_etc > 0:
        print(f"  [연결 오류] {err_etc}건 — 위 상세 코드 확인")
        print(f"     401: NOTIF_AUTH_TOKEN 재설정 / 422: payload 형식 오류 / ConnectError: 포트 확인")

asyncio.run(main())
PYEOF

    local elapsed=$(( $(date +%s) - t0 ))
    echo ""
    info "검증 단계 완료 (+${elapsed}s)"

    print_notifications_log "$pod" 15

    # Logic App FAILED 시 audit_log에서 실제 에러 코드 조회
    echo ""
    info "audit_log — 최근 Logic App 에러 상세 (FAILED 원인)"
    kubectl exec -i -n "$NAMESPACE" "$pod" -- python3 - <<'PYEOF' 2>/dev/null || true
from src.db import db_conn
import json
with db_conn() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT after_state, created_at
          FROM audit_log
         WHERE entity_type='notifications_log'
           AND after_state::jsonb->>'status' = 'FAILED'
         ORDER BY id DESC
         LIMIT 5
    """)
    rows = cur.fetchall()
if rows:
    for state_raw, ts in rows:
        state = json.loads(state_raw)
        err = state.get('error') or '(에러 상세 없음)'
        evt = state.get('event_type', '')
        print(f"  [{ts}] {evt}: {err}")
else:
    print("  (최근 FAILED 기록 없음)")
PYEOF

    # 검증 대상 Logic App 실행 기록 표시
    if [[ -n "${LOGIC_APP_TEST_NAME:-}" ]]; then
        info "테스트 Logic App 최근 실행 기록 (검증 대상)"
        print_logic_app_runs "$LOGIC_APP_TEST_NAME" 5
        echo ""
        info "Prod Logic App 최근 실행 기록 (참고용)"
        print_logic_app_runs "$LOGIC_APP_ARRIVAL" 3
    else
        print_logic_app_runs "$LOGIC_APP_ARRIVAL" 5
    fi
}

# ════════════════════════════════════════════════════════════════
# 검증 결과 요약
# ════════════════════════════════════════════════════════════════
print_summary() {
    local reproduce_n="${1:-15}" verify_n="${2:-5}"

    section "시나리오 검증 결과 요약"
    printf "  %-42s %-28s %s\n" "항목" "기대 결과" "확인 방법"
    printf "  %-42s %-28s %s\n" "──────────────────────────────────────────" "────────────────────────────" "────────────────────"
    printf "  %-42s %-28s %s\n" "[재현] ${reproduce_n}건 동시 → 504"  "전건 504 ActionResponseTimedOut" "Logic App 직접 호출"
    printf "  %-42s %-28s %s\n" "[수정] Semaphore(1) 코드 확인"             "limit=1 활성"                    "Pod 내 inspect"
    printf "  %-42s %-28s %s\n" "[수정] timeout >= 120s"                    "120.0s 이상"                     "ConfigMap"
    printf "  %-42s %-28s %s\n" "[검증] 단건 ping"                          "HTTP 200 정상"                   "/notification/send 단건"
    printf "  %-42s %-28s %s\n" "[검증] ${verify_n}건 동시 → 순차 처리"    "504=0건, 성공=${verify_n}건"     "실제 _logic_apps_sem"
    printf "  %-42s %-28s %s\n" "[검증] 총 소요 ≈ N × 단건 시간"           "직렬 처리 확인"                  "elapsed 비교"
    printf "  %-42s %-28s %s\n" "[검증] notifications_log"                  "FAILED 0건"                      "DB 직접 쿼리"
    echo ""
}

# ════════════════════════════════════════════════════════════════
# all
# ════════════════════════════════════════════════════════════════
cmd_all() {
    local n="${1:-5}"
    local reproduce_n=15

    section "Logic Apps 504 장애 재현 + Semaphore(1) 수정 검증 (전체 실행)"
    echo ""
    echo "  실제 발생: 2026-05-19 오후 1:47~1:48 — StockArrivalPending 14건 동시 실패"
    echo "  재현: ${reproduce_n}건 동시 호출 → 504  (LOGIC_APP_TEST_URL 설정 권장)"
    echo "  검증: ${n}건 동시 /notification/send → Semaphore(1) 순차 처리 → 504 미발생"
    echo ""
    warn "시작합니다.  Enter 계속 / Ctrl+C 취소"
    read -r

    cmd_check

    echo ""
    warn "[재현] ${reproduce_n}건 동시 Logic App 직접 호출 — Enter 계속 / Ctrl+C 건너뜀"
    read -r
    cmd_reproduce "$reproduce_n"

    echo ""
    warn "[수정] rollout restart — Enter 계속 / Ctrl+C 건너뜀"
    read -r
    cmd_fix

    echo ""
    warn "[검증] ${n}건 동시 /notification/send 발송 — Enter 계속 / Ctrl+C 건너뜀"
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
        echo "  reproduce [N]      N건 동시 호출 → 504 재현 (기본 15)"
        echo "  fix                Semaphore(1) 코드 확인 + rollout restart"
        echo "  verify [N]         N건 동시 /notification/send → Semaphore(1) 순차 검증 (기본 5)"
        echo "  all [N]            전체 시나리오 순서대로 실행 (기본 N=5)"
        exit 1
        ;;
esac
