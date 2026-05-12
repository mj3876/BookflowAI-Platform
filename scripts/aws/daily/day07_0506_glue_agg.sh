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

# ── Step 6. S3 Mart 전체 확인 ──────────────────────────────────
step "Step 6 · S3 Mart 전체 확인"

printf "\n  %-40s %6s\n" "S3 경로" "파일수"
printf "  %-40s %6s\n" "──────────────────────────────────────" "──────"
for TABLE in mart/sales_fact mart/books_static mart/features mart/inventory_daily mart/locations_static mart/store_location_map sales_daily; do
  CNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive \
    --region "${REGION}" 2>/dev/null | wc -l)
  printf "  %-40s %6s\n" "${TABLE}/" "${CNT}"
done

# ── Step 7. GCS 적재 확인 (4개 테이블) ─────────────────────────
step "Step 7 · GCS 적재 확인"

GCS_BUCKET="${GCS_BUCKET:-${BOOKFLOW_GCS_BUCKET:-}}"
if [ -z "${GCS_BUCKET}" ]; then
  warn "GCS_BUCKET 미설정 — .env.local 확인"
else
  printf "\n  %-45s %6s\n" "GCS 경로" "파일수"
  printf "  %-45s %6s\n" "─────────────────────────────────────────" "──────"
  ALL_OK=true
  for TABLE in features inventory_daily locations_static store_location_map; do
    if command -v gsutil >/dev/null 2>&1; then
      GCS_CNT=$(gsutil ls "gs://${GCS_BUCKET}/mart/${TABLE}/" 2>/dev/null | wc -l || echo 0)
    else
      # gsutil 없으면 AWS CLI로 GCS 직접 접근 불가 → 메시지만 출력
      GCS_CNT="?"
    fi
    if [ "${GCS_CNT}" = "?" ]; then
      printf "  %-45s %6s\n" "gs://${GCS_BUCKET}/mart/${TABLE}/" "(gsutil 필요)"
    elif [ "${GCS_CNT}" -gt 0 ] 2>/dev/null; then
      printf "  %-45s %6s\n" "gs://${GCS_BUCKET}/mart/${TABLE}/" "${GCS_CNT}"
      ok "gs://${GCS_BUCKET}/mart/${TABLE}/ 확인"
    else
      printf "  %-45s %6s\n" "gs://${GCS_BUCKET}/mart/${TABLE}/" "0 ⚠"
      warn "gs://${GCS_BUCKET}/mart/${TABLE}/: 파일 없음"
      ALL_OK=false
    fi
  done
  if [ "${ALL_OK}" = "true" ] && [ "${GCS_CNT}" != "?" ]; then
    ok "GCS 4개 테이블 모두 적재 확인"
  fi
fi

# ──    ──────────────────────────────────────
step "Day 07  "
cat << 'EOF'
  [ ] sales_daily_agg SUCCEEDED
  [ ] features_build SUCCEEDED → GCS dual-write → gs://{GCS_BUCKET}/mart/features/
  [ ] Step Functions ETL3 ARN 확인 (재실행 안 함 · 중복 방지)
  [ ] forecast-trigger STEP_FN_ARN 설정 (1회성 Lambda 환경변수)
  [ ] S3 Mart 전체 확인 (7개 경로)
  [ ] GCS 적재 확인:
      [ ] gs://{GCS_BUCKET}/mart/features/           (features_build dual-write)
      [ ] gs://{GCS_BUCKET}/mart/inventory_daily/    (inventory-daily-gcs Job)
      [ ] gs://{GCS_BUCKET}/mart/locations_static/   (locations-static-gcs Job)
      [ ] gs://{GCS_BUCKET}/mart/store_location_map/ (store-location-map-gcs Job)

  ★ GCS 수동 확인: gsutil ls gs://{GCS_BUCKET}/mart/
  ★ Glue Job 로그: /aws-glue/jobs/output/ (CloudWatch)

(6/7) 다음: day08_0507_cicd_glue.sh
  → glue-redeploy GHA CI/CD 설정 + Ansible CN
EOF
