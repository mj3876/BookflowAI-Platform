#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 07 · 5/6 ()  Glue  Job + Step Functions      ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. sales_daily_agg Job  +                       ║
# ║  2. features_build Job  +                        ║
# ║  3. Step Functions ETL3                ║
# ║  4. Lambda forecast-trigger  SF ARN                ║
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

REQUIRED_TABLES=(pos_events sns_mentions aladin_books calendar_events)
for TABLE in "${REQUIRED_TABLES[@]}"; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${TABLE}/: ${COUNT}"
  else
    err "${TABLE}/  · day06   (raw mart 4)"
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

COUNT=$(aws s3 ls "s3://${MART_BUCKET}/features/" --recursive 2>/dev/null | wc -l)
ok "features/: ${COUNT}"

# ── Step 4. Step Functions ETL3   ────────────────────
step "Step 4 · Step Functions ETL3  "

SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

if [ -z "${SF_ARN}" ]; then
  warn "Step Functions ARN  · bookflow-99-step-functions  "
else
  info "ETL3 SF ARN: ${SF_ARN}"

  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "${SF_ARN}" \
    --input '{"trigger":"manual","date":"'"$(date +%Y-%m-%d)"'"}' \
    --query 'executionArn' --output text)
  info "Execution ARN: ${EXEC_ARN}"

  #   20  ( 15)
  for i in $(seq 1 30); do
    sleep 20
    SF_STATUS=$(aws stepfunctions describe-execution \
      --execution-arn "${EXEC_ARN}" \
      --query 'status' --output text 2>/dev/null || echo "UNKNOWN")
    info "ETL3 Step Functions: ${SF_STATUS} (${i})"
    case "${SF_STATUS}" in
      SUCCEEDED) ok "ETL3  "; break ;;
      FAILED|ABORTED|TIMED_OUT)
        aws stepfunctions get-execution-history \
          --execution-arn "${EXEC_ARN}" \
          --query 'events[-3:].executionFailedEventDetails' \
          --output json 2>/dev/null | head -20
        warn "ETL3 : ${SF_STATUS}"
        break ;;
    esac
  done
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

for TABLE in pos_events sns_mentions aladin_books calendar_events sales_daily features; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  info "${TABLE}/: ${COUNT}"
done

# ──    ──────────────────────────────────────
step "Day 07  "
cat << 'EOF'
  [ ] sales_daily_agg SUCCEEDED
  [ ] features_build SUCCEEDED
  [ ] S3 Mart sales_daily/ · features/  
  [ ] Step Functions ETL3 SUCCEEDED ( )
  [ ] forecast-trigger STEP_FN_ARN  

  ★ ETL3 Step Functions  6 Job  :
    raw_pos/sns/aladin/event → sales_daily_agg → features_build

(5/7)  : day08_0507_cicd_glue.sh
  → glue-redeploy GHA CI/CD  + Ansible CN 
EOF
