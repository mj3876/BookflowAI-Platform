#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Historical Parquet → S3 Mart → (EventBridge) → GCS → BQ   ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  업로드 대상 (6개):                                          ║
# ║    sales_fact, books_static, features,                       ║
# ║    inventory_daily, locations_static, store_location_map     ║
# ║  제외: training_dataset (BQ에서 학습 직전에 JOIN으로 생성)   ║
# ║                                                              ║
# ║  사용법:                                                     ║
# ║    bash upload_historical_mart.sh [BATCH_ID]                 ║
# ║    bash upload_historical_mart.sh e2e-002                    ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

BATCH_ID="${1:-e2e-001}"
ACCOUNT=$(account_id)
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
HISTORICAL_DIR="${REPO_ROOT}/scripts/output/historical"

# 업로드할 테이블 목록
# training_dataset 제외: BigQuery에서 학습 직전에 만들어지는 결과 테이블
TABLES=(
  sales_fact
  books_static
  features
  inventory_daily
  locations_static
  store_location_map
)

# ── Step 1. 로컬 파일 확인 ────────────────────────────────────
step "Step 1 · 로컬 Parquet 파일 확인"

info "BATCH_ID   : ${BATCH_ID}"
info "소스 디렉토리: ${HISTORICAL_DIR}"
info "대상 버킷   : s3://${MART_BUCKET}"
echo ""

MISSING=0
for TABLE in "${TABLES[@]}"; do
  LOCAL="${HISTORICAL_DIR}/${TABLE}.parquet"
  if [ -f "${LOCAL}" ]; then
    SIZE=$(python3 -c "import os; s=os.path.getsize('${LOCAL}'); print(f'{s/1024:.1f} KB')" 2>/dev/null || echo "?")
    ok "${TABLE}.parquet (${SIZE})"
  else
    warn "파일 없음: ${LOCAL}"
    MISSING=$((MISSING+1))
  fi
done

[ "${MISSING}" -gt 0 ] && err "${MISSING}개 파일 누락 · scripts/output/historical/ 확인"

# ── Step 2. S3 Mart 업로드 ────────────────────────────────────
step "Step 2 · S3 Mart 업로드 (mart/<table>/${BATCH_ID}/part-0.parquet)"

UPLOADED=0
for TABLE in "${TABLES[@]}"; do
  LOCAL="${HISTORICAL_DIR}/${TABLE}.parquet"
  S3_KEY="mart/${TABLE}/${BATCH_ID}/part-0.parquet"
  S3_URI="s3://${MART_BUCKET}/${S3_KEY}"

  aws s3 cp "${LOCAL}" "${S3_URI}" \
    --region "${REGION}" \
    --no-progress 2>/dev/null

  # 업로드 확인
  EXIST=$(aws s3 ls "${S3_URI}" --region "${REGION}" 2>/dev/null | wc -l)
  if [ "${EXIST}" -gt 0 ]; then
    ok "${TABLE} → ${S3_URI}"
    UPLOADED=$((UPLOADED+1))
  else
    warn "${TABLE} 업로드 실패"
  fi
done

info "업로드 완료: ${UPLOADED}/${#TABLES[@]}"
[ "${UPLOADED}" -lt "${#TABLES[@]}" ] && err "일부 업로드 실패"

# ── Step 3. S3 EventBridge 알림 활성화 확인 ───────────────────
step "Step 3 · S3 EventBridge 알림 상태 확인"

EB_CONF=$(aws s3api get-bucket-notification-configuration \
  --bucket "${MART_BUCKET}" \
  --region "${REGION}" \
  --query 'EventBridgeConfiguration' --output text 2>/dev/null || echo "")

if [ -n "${EB_CONF}" ] && [ "${EB_CONF}" != "None" ]; then
  ok "EventBridge 알림 활성화됨 → mart-to-gcs Lambda 자동 트리거"
else
  warn "EventBridge 알림 미설정 → 수동 활성화 실행"
  aws s3api put-bucket-notification-configuration \
    --bucket "${MART_BUCKET}" \
    --notification-configuration '{"EventBridgeConfiguration":{}}' \
    --region "${REGION}"
  ok "EventBridge 알림 활성화 완료"
fi

# ── Step 4. mart-to-gcs Lambda 트리거 대기 ───────────────────
step "Step 4 · mart-to-gcs Lambda 로그 확인 (30초 대기)"

info "EventBridge → mart-to-gcs Lambda 실행 대기 중..."
sleep 30

LOG_GROUP="/aws/lambda/${PROJECT}-mart-to-gcs"
START_MS=$(python3 -c "
import time
print(int((time.time() - 120) * 1000))
")

COPY_LOGS=$(aws logs filter-log-events \
  --log-group-name "${LOG_GROUP}" \
  --filter-pattern "[mart-to-gcs]" \
  --start-time "${START_MS}" \
  --query 'events[-10:].message' \
  --output text \
  --region "${REGION}" 2>/dev/null || echo "")

if [ -n "${COPY_LOGS}" ]; then
  ok "mart-to-gcs 실행 확인"
  echo "${COPY_LOGS}" | grep -E "s3://|gs://|done|copied|ERROR" | head -10
else
  info "로그 아직 없음 (Lambda cold start 지연) · 60초 추가 대기"
  sleep 60
  COPY_LOGS=$(aws logs filter-log-events \
    --log-group-name "${LOG_GROUP}" \
    --filter-pattern "[mart-to-gcs]" \
    --start-time "${START_MS}" \
    --query 'events[-10:].message' \
    --output text \
    --region "${REGION}" 2>/dev/null || echo "")
  [ -n "${COPY_LOGS}" ] && echo "${COPY_LOGS}" | head -10 || warn "로그 없음 · Lambda 실행 여부 콘솔 확인"
fi

# ── Step 5. S3 Mart 최종 확인 ────────────────────────────────
step "Step 5 · S3 Mart 업로드 결과 확인"

printf "\n  %-30s %10s\n" "경로" "파일 수"
printf "  %-30s %10s\n" "──────────────────────────" "────────"
for TABLE in "${TABLES[@]}"; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/mart/${TABLE}/" --recursive \
    --region "${REGION}" 2>/dev/null | wc -l)
  printf "  %-30s %10s\n" "mart/${TABLE}/" "${COUNT}"
done
echo ""

# ── 완료 체크리스트 ───────────────────────────────────────────
step "완료 및 다음 단계"
cat << EOF
  [v] 로컬 파일 확인: 6개
  [v] S3 Mart 업로드: mart/<table>/${BATCH_ID}/part-0.parquet
  [v] EventBridge 알림 활성화
  [ ] mart-to-gcs Lambda → GCS 전송 (로그 위에서 확인)
  [ ] bq-load Cloud Function → BigQuery 적재

  ★ GCS 적재 확인 (GCP 쪽):
    gsutil ls gs://<project-id>-bookflow-staging/mart/

  ★ BigQuery 적재 확인:
    bq query --nouse_legacy_sql \\
      'SELECT COUNT(*) FROM bookflow_dw.sales_fact'
    bq query --nouse_legacy_sql \\
      'SELECT COUNT(*) FROM bookflow_dw.inventory_daily'
    bq query --nouse_legacy_sql \\
      'SELECT COUNT(*) FROM bookflow_dw.locations_static'

  ※ training_dataset 은 Vertex AI 학습 직전 BigQuery 파이프라인이 자동 생성
     (sales_fact + features + books_static + inventory_daily + store_location_map JOIN)
EOF
