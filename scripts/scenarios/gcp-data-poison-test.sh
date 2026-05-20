#!/usr/bin/env bash
# gcp-data-poison-test.sh
#
# Scenario 12: GCS 스테이징 버킷 업로드 → BigQuery 오염 경로 검증
#
# 재현 경로:
#   테스트 Parquet 업로드 (mart/features/feature_date=9999-12-31/)
#   → Eventarc → bookflow-gcs-router Workflow 자동 트리거
#   → bookflow-bq-load Cloud Function 실행
#   → bookflow_dw.features 에 조작 행 삽입
#   → 다음 Vertex AI 파이프라인이 오염 피처를 학습에 사용
#
# 원복:
#   GCS 테스트 파일 삭제 + BQ features WHERE feature_date='9999-12-31' 행 삭제
#
# 사용법:
#   bash gcp-data-poison-test.sh           # 전체 실행 (기본)
#   bash gcp-data-poison-test.sh audit     # 버킷 IAM 쓰기 권한 확인만
#   bash gcp-data-poison-test.sh inject    # 테스트 Parquet 업로드
#   bash gcp-data-poison-test.sh verify    # BQ 오염 여부 확인
#   bash gcp-data-poison-test.sh restore   # GCS 파일 + BQ 행 삭제 (원복)
#
# 사전 조건:
#   gcloud (GCP 인증, bigquery.dataEditor 권한), python3 + pyarrow

set -euo pipefail

GCP_PROJECT="project-8ab6bf05-54d2-4f5d-b8d"
GCP_REGION="asia-northeast1"
BUCKET="project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging"
DATASET="bookflow_dw"
WORKFLOW_NAME="bookflow-gcs-router"
FUNCTION_NAME="bookflow-bq-load"

# 테스트 마커 — 유효 범위 밖 날짜로 실제 데이터와 구분
TEST_DATE="9999-12-31"
TEST_ISBN="SEC-TEST-POISON-9999"
GCS_OBJECT="mart/features/feature_date=${TEST_DATE}/part-00001-sec-test.parquet"
GCS_URI="gs://${BUCKET}/${GCS_OBJECT}"

LOCAL_TMP_PARQUET=""
UPLOADED=false

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

# ── 안전 종료: GCS 파일 + BQ 행 자동 정리 ────────────────────────
cleanup_on_exit() {
    if [[ "$UPLOADED" == "true" ]]; then
        warn "스크립트 종료 감지 — 테스트 리소스 자동 정리 중..."
        _delete_gcs_file 2>/dev/null || true
        _delete_bq_rows  2>/dev/null || true
    fi
    [[ -n "$LOCAL_TMP_PARQUET" && -f "$LOCAL_TMP_PARQUET" ]] && rm -f "$LOCAL_TMP_PARQUET"
}
trap cleanup_on_exit EXIT

# ── GCS 파일 삭제 ─────────────────────────────────────────────────
_delete_gcs_file() {
    if gcloud storage objects describe "$GCS_URI" --project="$GCP_PROJECT" &>/dev/null; then
        gcloud storage rm "$GCS_URI" --project="$GCP_PROJECT" -q
        ok "GCS 파일 삭제: $GCS_URI"
    else
        info "GCS 파일 없음 (이미 삭제됨)"
    fi
    UPLOADED=false
}

# ── BQ 테스트 행 삭제 ─────────────────────────────────────────────
_delete_bq_rows() {
    local cnt
    cnt=$(bq query --use_legacy_sql=false --format=csv --quiet \
        "SELECT COUNT(*) FROM \`${GCP_PROJECT}.${DATASET}.features\` WHERE feature_date = '${TEST_DATE}'" \
        2>/dev/null | tail -1 || echo "0")
    if [[ "${cnt:-0}" -gt 0 ]]; then
        bq query --use_legacy_sql=false --quiet \
            "DELETE FROM \`${GCP_PROJECT}.${DATASET}.features\` WHERE feature_date = '${TEST_DATE}'" \
            2>/dev/null
        ok "BQ 테스트 행 ${cnt}건 삭제 완료"
    else
        info "BQ 테스트 행 없음 — 삭제 불필요"
    fi
}

# ── 테스트 Parquet 생성 (features 스키마, 명확한 이상 마커값) ────
create_test_parquet() {
    LOCAL_TMP_PARQUET="$(python3 -c \
        'import tempfile; f=tempfile.NamedTemporaryFile(suffix=".parquet",delete=False); print(f.name); f.close()')"

    python3 - "$LOCAL_TMP_PARQUET" <<'PYEOF'
import sys
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("ERROR: pyarrow 없음 → pip install pyarrow", file=sys.stderr)
    sys.exit(1)

# features 테이블 스키마 (feature_date는 Hive 파티션 경로에서 자동 주입)
schema = pa.schema([
    pa.field('isbn13',                   pa.string()),
    pa.field('is_holiday',               pa.bool_()),
    pa.field('holiday_name',             pa.string()),
    pa.field('season',                   pa.string()),
    pa.field('day_of_week',              pa.int32()),
    pa.field('is_weekend',               pa.bool_()),
    pa.field('month',                    pa.int32()),
    pa.field('event_nearby_days',        pa.int32()),
    pa.field('sns_mentions_1d',          pa.int32()),
    pa.field('sns_mentions_7d',          pa.int32()),
    pa.field('book_age_days',            pa.int32()),
    pa.field('is_bestseller_flag',       pa.bool_()),
    pa.field('on_hand_total',            pa.int32()),
    pa.field('days_since_last_stockout', pa.int32()),
])

# 유효 범위를 벗어난 수치로 오염 여부 즉시 식별 가능하게 설정
tbl = pa.table({
    'isbn13':                   ['SEC-TEST-POISON-9999'],
    'is_holiday':               [True],
    'holiday_name':             ['SECURITY_TEST_DO_NOT_USE'],
    'season':                   ['SECURITY_TEST'],
    'day_of_week':              [9],       # 1-7 범위 초과
    'is_weekend':               [True],
    'month':                    [13],      # 1-12 범위 초과
    'event_nearby_days':        [-1],
    'sns_mentions_1d':          [999999],  # 극단값 마커
    'sns_mentions_7d':          [999999],
    'book_age_days':            [-1],
    'is_bestseller_flag':       [True],
    'on_hand_total':            [-1],
    'days_since_last_stockout': [-1],
}, schema=schema)

pq.write_table(tbl, sys.argv[1])
print(f"[OK] Parquet 생성 완료: {sys.argv[1]}  ({tbl.num_rows}행)")
PYEOF
}

# ── 최근 Workflow 실행 이력 ───────────────────────────────────────
show_recent_workflow_execs() {
    gcloud workflows executions list "$WORKFLOW_NAME" \
        --location="$GCP_REGION" \
        --project="$GCP_PROJECT" \
        --limit=5 \
        --format="table[box](name.basename():label=EXEC_ID, state, startTime, endTime)" \
        2>/dev/null || echo "  (Workflow 조회 실패)"
}

# ════════════════════════════════════════════════════════════════
# audit: 버킷 IAM 쓰기 권한 확인
# ════════════════════════════════════════════════════════════════
cmd_audit() {
    section "버킷 IAM 쓰기 권한 감사"

    info "대상 버킷: gs://${BUCKET}"
    echo ""

    step "전체 IAM 정책"
    gcloud storage buckets get-iam-policy "gs://${BUCKET}" \
        --project="$GCP_PROJECT" \
        --format="table[box](bindings.role, bindings.members.join(','))" \
        2>/dev/null || warn "IAM 조회 실패"

    echo ""
    step "쓰기 가능 Principal 추출 (objectCreator 이상)"
    gcloud storage buckets get-iam-policy "gs://${BUCKET}" \
        --project="$GCP_PROJECT" \
        --format=json 2>/dev/null | python3 -c "
import json, sys
WRITE_ROLES = {
    'roles/storage.objectAdmin', 'roles/storage.objectCreator',
    'roles/storage.admin', 'roles/storage.legacyBucketWriter',
    'roles/storage.legacyObjectOwner', 'roles/editor', 'roles/owner',
}
data = json.load(sys.stdin)
found = [(b['role'], b.get('members', [])) for b in data.get('bindings', []) if b['role'] in WRITE_ROLES]
if found:
    print('  [WARN] 쓰기 권한 보유 Principal:')
    for role, members in found:
        for m in members:
            print(f'    {role}  →  {m}')
    print()
    print('  → 이 중 하나라도 탈취/오용되면 bq-load 파이프라인이 트리거됩니다.')
else:
    print('  [OK] 명시적 쓰기 역할 없음')
    print('  (프로젝트 레벨 roles/editor 상속 여부는 별도 확인 필요)')
" 2>/dev/null || warn "IAM 파싱 실패"
}

# ════════════════════════════════════════════════════════════════
# inject: 테스트 Parquet 업로드 (오염 유발)
# ════════════════════════════════════════════════════════════════
cmd_inject() {
    section "테스트 Parquet 업로드 — 오염 경로 유발"
    echo ""
    echo "  업로드 경로: $GCS_URI"
    echo "  트리거 예상: Eventarc → bookflow-gcs-router → bq-load → features 삽입"
    echo ""

    if gcloud storage objects describe "$GCS_URI" --project="$GCP_PROJECT" &>/dev/null; then
        warn "이전 테스트 파일이 잔류해 있습니다. 먼저 원복 후 실행하세요:"
        warn "  bash $0 restore"
        exit 1
    fi

    step "1/3  features 스키마 Parquet 생성 (마커값: isbn13=$TEST_ISBN, month=13)"
    create_test_parquet
    ok "Parquet 생성: $LOCAL_TMP_PARQUET"

    step "2/3  GCS 업로드"
    gcloud storage cp "$LOCAL_TMP_PARQUET" "$GCS_URI" --project="$GCP_PROJECT"
    UPLOADED=true
    ok "업로드 완료: $GCS_URI  ($(date '+%H:%M:%S'))"
    rm -f "$LOCAL_TMP_PARQUET"; LOCAL_TMP_PARQUET=""

    step "3/3  Eventarc 트리거 대기 (30s)"
    sleep 30
    echo ""
    info "현재 Workflow 실행 이력:"
    show_recent_workflow_execs
    echo ""
    warn "bq-load 완료까지 추가 30-60s 소요 — 1분 후 verify 실행 권장:"
    warn "  sleep 60 && bash $0 verify"
}

# ════════════════════════════════════════════════════════════════
# verify: BQ 오염 여부 확인
# ════════════════════════════════════════════════════════════════
cmd_verify() {
    section "BQ 오염 여부 검증"

    step "1/3  최근 Workflow 실행 이력"
    show_recent_workflow_execs

    step "2/3  bq-load Cloud Function 최근 로그"
    gcloud functions logs read "$FUNCTION_NAME" \
        --region="$GCP_REGION" \
        --project="$GCP_PROJECT" \
        --limit=10 \
        2>/dev/null | grep -v "^$" || warn "로그 조회 실패"

    step "3/3  BQ features 테스트 행 확인 (feature_date='${TEST_DATE}')"
    echo ""
    local cnt
    cnt=$(bq query --use_legacy_sql=false --format=csv --quiet \
        "SELECT COUNT(*) FROM \`${GCP_PROJECT}.${DATASET}.features\` WHERE feature_date = '${TEST_DATE}'" \
        2>/dev/null | tail -1 || echo "0")

    if [[ "${cnt:-0}" -gt 0 ]]; then
        error "[POISONED] features 테이블에 테스트 행 ${cnt}건 삽입 확인!"
        echo ""
        bq query --use_legacy_sql=false --format=pretty --max_rows=5 \
            "SELECT feature_date, isbn13, month, day_of_week, sns_mentions_1d, on_hand_total
             FROM \`${GCP_PROJECT}.${DATASET}.features\`
             WHERE feature_date = '${TEST_DATE}'" 2>/dev/null || true
        echo ""
        warn "실제 공격 시: 정상 날짜 + 조작 수치 → 다음 Vertex AI 학습 시 오염 피처 반영"
        warn "복구: bash $0 restore"
    else
        info "BQ 테스트 행 없음 — bq-load 처리 중이거나 Workflow 미트리거"
        info "1분 후 재시도 또는 bq-load 로그 확인"
    fi
}

# ════════════════════════════════════════════════════════════════
# restore: GCS 파일 + BQ 테스트 행 삭제
# ════════════════════════════════════════════════════════════════
cmd_restore() {
    section "원복 — GCS 파일 + BQ 테스트 행 삭제"

    step "1/2  GCS 테스트 파일 삭제"
    _delete_gcs_file

    step "2/2  BQ features 테스트 행 삭제"
    _delete_bq_rows

    echo ""
    local cnt
    cnt=$(bq query --use_legacy_sql=false --format=csv --quiet \
        "SELECT COUNT(*) FROM \`${GCP_PROJECT}.${DATASET}.features\` WHERE feature_date = '${TEST_DATE}'" \
        2>/dev/null | tail -1 || echo "?")
    ok "원복 완료 — features WHERE feature_date='${TEST_DATE}': ${cnt}행"
}

# ════════════════════════════════════════════════════════════════
# 전체 실행
# ════════════════════════════════════════════════════════════════
cmd_all() {
    section "Scenario 12: GCS 데이터 오염 경로 — 전체 실행"
    echo ""
    echo "  단계: IAM 감사 → Parquet 주입 → BQ 오염 검증 → 원복"
    echo ""

    cmd_audit

    echo ""
    warn "스테이징 버킷에 테스트 Parquet을 업로드합니다."
    warn "Eventarc → bq-load → BQ 쓰기가 트리거됩니다. 계속하려면 Enter, 취소는 Ctrl+C"
    read -r

    cmd_inject

    info "bq-load 처리 대기 (60s)..."
    sleep 60

    cmd_verify

    echo ""
    cmd_restore

    section "Scenario 12 완료"
    echo ""
    echo "  확인된 공격 경로:"
    echo "  - 버킷 쓰기 권한 1개 → Eventarc 자동 트리거 → bq-load 무조건 실행"
    echo "  - bookflow-bq-load SA: bookflow_dw 전체에 dataEditor → 임의 행 삽입 가능"
    echo ""
    echo "  완화 방안:"
    echo "  - bq-load SA 권한을 테이블 단위로 축소 (features, sales_fact 등 각각)"
    echo "  - bq-load에 Parquet 업로더 SA 검증 로직 추가"
    echo "  - 테스트/개발용 전용 버킷 분리 (Eventarc 미연결)"
}

MODE="${1:-all}"
case "$MODE" in
    audit)   cmd_audit ;;
    inject)  cmd_inject ;;
    verify)  cmd_verify ;;
    restore) cmd_restore ;;
    all)     cmd_all ;;
    *)
        echo "사용법: $0 [audit|inject|verify|restore|all]"
        echo ""
        echo "  audit    버킷 IAM 쓰기 권한 확인"
        echo "  inject   테스트 Parquet 업로드 (오염 유발)"
        echo "  verify   BQ 오염 행 확인"
        echo "  restore  GCS 파일 + BQ 행 삭제 (원복)"
        echo "  all      전체 시나리오 순서대로 실행 (기본)"
        exit 1
        ;;
esac
