#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 07 · 5/6 ()  Glue  Job + Step Functions      ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. sales_daily_agg Job  +                       ║
# ║  2. features_build Job  +                        ║
# ║  3. Step Functions ETL3 ARN 확인 (재실행 제외 · 중복 방지)    ║
# ║  4. Lambda forecast-trigger  SF ARN                ║
# ║  (features 는 Step 3 features_build → EventBridge → GCS 자동 처리) ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")

# Glue Job   
wait_glue_job() {
  local job_name="$1"
  local run_id="$2"
  local timeout=900
  local elapsed=0

  while [ $elapsed -lt $timeout ]; do
    STATE=$(aws glue get-job-run --job-name "${job_name}" --run-id "${run_id}" \
      --query 'JobRun.JobRunState' --output text 2>/dev/null || echo "UNKNOWN")
    case "${STATE}" in
      SUCCEEDED) ok "${job_name} "; return 0 ;;
      FAILED|STOPPED|TIMEOUT|ERROR)
        aws glue get-job-run --job-name "${job_name}" --run-id "${run_id}" \
          --query 'JobRun.ErrorMessage' --output text 2>/dev/null | head -3
        err "${job_name} : ${STATE}"
        ;;
      *) info "${job_name}   (${STATE}, ${elapsed}s)"; sleep 30; elapsed=$((elapsed+30)) ;;
    esac
  done
  err "${job_name} timeout"
}

# ── Step 1.   (Mart raw  ) ──────────────
step "Step 1 ·   (Mart raw )"

# mart/sales_fact: sales_daily_agg 입력 · historical 업로드로 보완 가능 (warn)
# sns_mentions, aladin_books, calendar_events: features_build 내부 입력
#   → Glue 미실행 시 historical features.parquet 으로 대체 (Step 7) 하므로 warn 처리
for TABLE in mart/sales_fact mart/books_static sns_mentions aladin_books calendar_events; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${TABLE}/: ${COUNT}"
  else
    warn "${TABLE}/ 없음 · Glue 미실행 또는 historical parquet 로 대체 예정"
  fi
done

# ── Step 2. sales_daily_agg Job  ─────────────────────────
step "Step 2 · sales_daily_agg Job "

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-sales-daily-agg" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "sales_daily_agg RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-sales-daily-agg" "${RUN_ID}"

COUNT=$(aws s3 ls "s3://${MART_BUCKET}/sales_daily/" --recursive 2>/dev/null | wc -l)
ok "sales_daily/: ${COUNT}"

# ── Step 3. features_build Job  ──────────────────────────
step "Step 3 · features_build Job  (  · 4 DPU)"

RUN_ID=$(aws glue start-job-run \
  --job-name "${PROJECT}-features-build" \
  --region "${REGION}" \
  --query 'JobRunId' --output text)
info "features_build RunId: ${RUN_ID}"
wait_glue_job "${PROJECT}-features-build" "${RUN_ID}"

COUNT=$(aws s3 ls "s3://${MART_BUCKET}/mart/features/" --recursive 2>/dev/null | wc -l)
ok "mart/features/: ${COUNT}"

# ── Step 4. Step Functions ETL3 ARN 확인만 (실행 제외) ───────────
step "Step 4 · Step Functions ETL3 ARN 확인"

# day06 Steps 4-7 + day07 Steps 2-3 에서 Glue Job 6개를 이미 개별 실행 완료.
# Step Functions 재실행 시 동일 Job 6개가 다시 돌아 S3/BQ 데이터 중복 발생.
# → ETL3는 단독 실행 용도 (day06~07 없이 한번에 돌릴 때만 사용).
SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

if [ -n "${SF_ARN}" ]; then
  ok "ETL3 State Machine ARN 확인: ${SF_ARN:0:60}..."
  info "※ day06+day07 개별 Job 실행 완료 → Step Functions 재실행 생략 (중복 방지)"
else
  warn "bookflow-99-step-functions 스택 미배포"
fi

# ── Step 5. forecast-trigger Lambda SF ARN   ─────────
step "Step 5 · forecast-trigger Lambda SF ARN "

FT_ENV=$(aws lambda get-function-configuration \
  --function-name "${PROJECT}-forecast-trigger" \
  --query 'Environment.Variables.STEP_FN_ARN' \
  --output text 2>/dev/null || echo "")

if [ -n "${FT_ENV}" ] && [ "${FT_ENV}" != "None" ]; then
  ok "forecast-trigger STEP_FN_ARN: ${FT_ENV:0:40}..."
else
  warn "forecast-trigger STEP_FN_ARN  · Lambda  "
  if [ -n "${SF_ARN}" ]; then
    info "STEP_FN_ARN  ..."
    CURRENT_ENV=$(aws lambda get-function-configuration \
      --function-name "${PROJECT}-forecast-trigger" \
      --query 'Environment.Variables' --output json 2>/dev/null || echo "{}")
    aws lambda update-function-configuration \
      --function-name "${PROJECT}-forecast-trigger" \
      --environment "Variables={AWS_REGION=${REGION},PROJECT_NAME=${PROJECT},STEP_FN_ARN=${SF_ARN}}" \
      --region "${REGION}" --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  ✓ forecast-trigger SF ARN  ')
"
  fi
fi

# ── Step 6. S3 Mart   ────────────────────────────────
step "Step 6 · S3 Mart   "

for TABLE in mart/sales_fact mart/books_static mart/features mart/inventory_daily mart/locations_static mart/store_location_map sales_daily; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  info "${TABLE}/: ${COUNT}"
done

# ── Step 7. S3 Mart 최종 확인 ────────────────────────────────
step "Step 7 · S3 Mart 최종 확인 (6개 테이블)"

# features 는 Step 3 features_build 완료 시 GCS dual-write → gs://{GCS_BUCKET}/mart/features/ 자동 전달
# historical features.parquet 재업로드 시 BigQuery WRITE_APPEND 중복 발생 → 업로드 제외
printf "\n  %-35s %6s\n" "경로" "파일수"
printf "  %-35s %6s\n" "──────────────────────────────" "──────"
for TABLE in sales_fact books_static features inventory_daily locations_static store_location_map; do
  S3_PATH="mart/${TABLE}"
  CNT=$(aws s3 ls "s3://${MART_BUCKET}/${S3_PATH}/" --recursive \
    --region "${REGION}" 2>/dev/null | wc -l)
  printf "  %-35s %6s\n" "${S3_PATH}/" "${CNT}"
done

# ──    ──────────────────────────────────────
step "Day 07  "
cat << 'EOF'
  [ ] sales_daily_agg SUCCEEDED
  [ ] features_build SUCCEEDED → GCS dual-write → gs://{GCS_BUCKET}/mart/features/
  [ ] Step Functions ETL3 ARN 확인 (재실행 안 함 · 중복 방지)
  [ ] forecast-trigger STEP_FN_ARN 설정 (1회성 Lambda 환경변수)
  [ ] S3 Mart 6개 테이블 파일 수 확인

  ★ GCS 전달 경로 (features_build GCS dual-write):
    day06: mart/sales_fact/, mart/books_static/  (Glue raw_pos/aladin_mart → GCS 미전달, BQ 직접 적재 불필요)
    day06: mart/inventory_daily/, mart/locations_static/, mart/store_location_map/ (historical → GCS 수동 업로드)
    day07: mart/features/  (Glue features_build → GCS dual-write → Eventarc → GCP Workflows → bq-load)
  ★ training_dataset 은 BigQuery Vertex AI 파이프라인이 학습 직전 JOIN 으로 생성

(5/7)  : day08_0507_cicd_glue.sh
  → glue-redeploy GHA CI/CD  + Ansible CN
EOF
