#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 06 · 5/5 ()  Glue ETL3  + raw mart 4       ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. Glue Catalog (bookflow-99-glue-catalog)          ║
# ║  2. Glue  6 S3 sync                                ║
# ║  3. raw_pos_mart · raw_sns_mart Job  +            ║
# ║  4. raw_aladin_mart · raw_event_mart Job  +       ║
# ║  5. S3 Mart                                          ║
# ║  9. Historical Parquet → S3 Mart (3 · Glue  )  ║
# ║     inventory_daily / locations_static / store_location_map  ║
# ║     (sales_fact · books_static 는 Glue ETL1 → GCS 전달 완료) ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT}"
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")

# Glue Job   
wait_glue_job() {
  local job_name="$1"
  local run_id="$2"
  local timeout=600  # 10
  local elapsed=0
  local interval=30

  while [ $elapsed -lt $timeout ]; do
    STATE=$(aws glue get-job-run \
      --job-name "${job_name}" \
      --run-id "${run_id}" \
      --query 'JobRun.JobRunState' --output text 2>/dev/null || echo "UNKNOWN")

    case "${STATE}" in
      SUCCEEDED) ok "${job_name} "; return 0 ;;
      FAILED|STOPPED|TIMEOUT|ERROR)
        aws glue get-job-run --job-name "${job_name}" --run-id "${run_id}" \
          --query 'JobRun.ErrorMessage' --output text 2>/dev/null | head -3
        err "${job_name} : ${STATE}"
        ;;
      *)
        info "${job_name}  ... (${STATE}, ${elapsed}s )"
        sleep $interval
        elapsed=$((elapsed + interval))
        ;;
    esac
  done
  err "${job_name} timeout (${timeout}s)"
}

# ── Step 1. ECS Sim  (  ) ─────────────────
step "Step 1 · ECS Sim  "

CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || \
          echo "${PROJECT}-cluster")

for SVC in online-sim offline-sim; do
  RUNNING=$(aws ecs describe-services \
    --cluster "${CLUSTER}" --services "${SVC}" \
    --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")
  if [ "${RUNNING}" = "0" ]; then
    warn "${SVC} running=0 · ..."
    aws ecs update-service --cluster "${CLUSTER}" --service "${SVC}" \
      --desired-count 1 --region "${REGION}" --output json > /dev/null
    ok "${SVC} DesiredCount=1"
  else
    ok "${SVC}: ${RUNNING} tasks running"
  fi
done

# ── Step 2. Glue Catalog   ───────────────────────────
step "Step 2 · Glue Catalog  "

bookflow task glue
ok "bookflow-99-glue-catalog + step-functions  "

# Glue DB  
DB_NAME=$(stack_output "bookflow-99-glue-catalog" "GlueDatabaseName" 2>/dev/null || \
          echo "bookflow_mart")
info "Glue DB: ${DB_NAME}"

# ── Step 3. Glue  S3 Sync ────────────────────────────
step "Step 3 · Glue  S3 Sync"

GLUE_JOBS_DIR="${REPO_ROOT}/glue-jobs"
aws s3 sync "${GLUE_JOBS_DIR}/" "s3://${GLUE_BUCKET}/scripts/" \
  --region "${REGION}" \
  --exclude "*.pyc" --exclude "__pycache__/*"
ok "6  → s3://${GLUE_BUCKET}/scripts/"
aws s3 ls "s3://${GLUE_BUCKET}/scripts/"

# ── Step 4. raw_pos_mart Job  ────────────────────────────
step "Step 4 · raw_pos_mart Job "

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-raw-pos-mart" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "raw_pos_mart RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-raw-pos-mart" "${RUN_ID}"

# ── Step 5. raw_sns_mart Job  ────────────────────────────
step "Step 5 · raw_sns_mart Job "

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-raw-sns-mart" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "raw_sns_mart RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-raw-sns-mart" "${RUN_ID}"

# ── Step 6. raw_aladin_mart Job  ─────────────────────────
step "Step 6 · raw_aladin_mart Job "

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-raw-aladin-mart" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "raw_aladin_mart RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-raw-aladin-mart" "${RUN_ID}"

# ── Step 7. raw_event_mart Job  ──────────────────────────
step "Step 7 · raw_event_mart Job "

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-raw-event-mart" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "raw_event_mart RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-raw-event-mart" "${RUN_ID}"

# ── Step 8. S3 Mart   ────────────────────────────────
step "Step 8 · S3 Mart  "

# GCS 전송 경로: Glue features_build Job이 직접 GCS dual-write 수행
# mart-to-gcs Lambda는 제거됨 — EventBridge S3 트리거 방식 사용 안 함
for TABLE in mart/sales_fact mart/books_static; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${TABLE}/: ${COUNT} Parquet (features_build 입력용)"
  else
    warn "${TABLE}/: 없음 · Glue Job 실패 확인"
  fi
done
# features_build 입력 재료 (내부 ETL용 · GCS 직접 전송 없음)
for TABLE in sns_mentions aladin_books calendar_events sales_daily; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${TABLE}/: ${COUNT} Parquet (features_build 입력용)"
  else
    warn "${TABLE}/: 없음"
  fi
done

# ── Step 9. Historical Parquet → S3 Mart (3 ) ───────
step "Step 9 · Historical Parquet → S3 Mart (e2e-001, 3 )"

# sales_fact, books_static 는 Glue ETL1(raw_pos_mart, raw_aladin_mart)이 S3 Mart에 기록.
# GCS 전달은 Storage Transfer(활성화 후) 또는 gsutil cp 수동 업로드로 처리.
#
# Glue Job 이 없어 historical 파일만이 유일한 소스인 3개 테이블만 S3에 업로드
BATCH_ID="e2e-001"
HISTORICAL_DIR="${REPO_ROOT}/scripts/output/historical"
DAY06_TABLES=(inventory_daily locations_static store_location_map)

for TABLE in "${DAY06_TABLES[@]}"; do
  LOCAL="${HISTORICAL_DIR}/${TABLE}.parquet"
  S3_URI="s3://${MART_BUCKET}/mart/${TABLE}/${BATCH_ID}/part-0.parquet"
  if [ -f "${LOCAL}" ]; then
    aws s3 cp "${LOCAL}" "${S3_URI}" \
      --region "${REGION}" --no-progress 2>/dev/null
    ok "${TABLE} → ${S3_URI}"
  else
    warn "${LOCAL} 없음 · 스킵 (scripts/output/historical/ 확인)"
  fi
done

# ── Step 10. Static 3개 테이블 Glue Job → GCS dual-write ────────
step "Step 10 · inventory_daily · locations_static · store_location_map → GCS"

GCS_BUCKET="${GCS_BUCKET:-${BOOKFLOW_GCS_BUCKET:-}}"
if [ -z "${GCS_BUCKET}" ]; then
  warn "GCS_BUCKET 미설정 — .env.local 확인 · GCS dual-write 건너뜀"
else
  for TABLE_JOB in "inventory-daily-gcs" "locations-static-gcs" "store-location-map-gcs"; do
    JOB_NAME="${PROJECT}-${TABLE_JOB}"
    # S3에 소스 데이터가 있는지 확인
    TABLE_NAME="${TABLE_JOB//-/_}"
    TABLE_NAME="${TABLE_NAME/_gcs/}"
    CNT=$(aws s3 ls "s3://${MART_BUCKET}/mart/${TABLE_NAME}/" --recursive 2>/dev/null | wc -l)
    if [ "${CNT}" -eq 0 ]; then
      warn "${TABLE_NAME}/: S3 데이터 없음 · Step 9 historical 업로드 확인"
      continue
    fi
    RUN_ID=$(aws glue start-job-run \
      --job-name "${JOB_NAME}" \
      --region "${REGION}" \
      --query 'JobRunId' --output text)
    info "${JOB_NAME} RunId: ${RUN_ID}"
    wait_glue_job "${JOB_NAME}" "${RUN_ID}"
  done

  # GCS 전달 확인 (gsutil 설치된 경우에만)
  if command -v gsutil >/dev/null 2>&1; then
    step "Step 10b · GCS 적재 확인"
    for TABLE in inventory_daily locations_static store_location_map; do
      GCS_CNT=$(gsutil ls "gs://${GCS_BUCKET}/mart/${TABLE}/" 2>/dev/null | wc -l || echo 0)
      if [ "${GCS_CNT}" -gt 0 ]; then
        ok "gs://${GCS_BUCKET}/mart/${TABLE}/: ${GCS_CNT} 파일"
      else
        warn "gs://${GCS_BUCKET}/mart/${TABLE}/: 파일 없음"
      fi
    done
  else
    info "gsutil 미설치 · GCS 확인은 day07 Step 7에서 수행"
  fi
fi

# ──    ──────────────────────────────────────
step "Day 06  "
cat << 'EOF'
  [ ] bookflow-99-glue-catalog   (SUCCEED)
  [ ] bookflow-99-step-functions
  [ ] Glue 스크립트 S3 sync
  [ ] raw_pos_mart SUCCEEDED
  [ ] raw_sns_mart SUCCEEDED
  [ ] raw_aladin_mart SUCCEEDED
  [ ] raw_event_mart SUCCEEDED
  [ ] S3 Mart mart/sales_fact/ · mart/books_static/ · sns_mentions/
  [ ] Historical → mart/inventory_daily/e2e-001/
  [ ] Historical → mart/locations_static/e2e-001/
  [ ] Historical → mart/store_location_map/e2e-001/
  [ ] inventory-daily-gcs SUCCEEDED → gs://{GCS_BUCKET}/mart/inventory_daily/
  [ ] locations-static-gcs SUCCEEDED → gs://{GCS_BUCKET}/mart/locations_static/
  [ ] store-location-map-gcs SUCCEEDED → gs://{GCS_BUCKET}/mart/store_location_map/

  ★ Job 확인: aws glue get-job-runs --job-name bookflow-inventory-daily-gcs
  ★ Logs: /aws-glue/jobs/output/

(6/7) 다음: day07_0506_glue_agg.sh
  → sales_daily_agg + features_build Job 실행 + features GCS 전달 확인
EOF
