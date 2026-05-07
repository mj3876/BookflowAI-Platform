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

for TABLE in pos_events sns_mentions aladin_books calendar_events; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${TABLE}/: ${COUNT} Parquet "
  else
    warn "${TABLE}/:   · Job  "
  fi
done

# ──    ──────────────────────────────────────
step "Day 06  "
cat << 'EOF'
  [ ] bookflow-99-glue-catalog   (SUCCEED)
  [ ] bookflow-99-step-functions  
  [ ] Glue  6 S3 
  [ ] raw_pos_mart SUCCEEDED
  [ ] raw_sns_mart SUCCEEDED
  [ ] raw_aladin_mart SUCCEEDED
  [ ] raw_event_mart SUCCEEDED
  [ ] S3 Mart pos_events/ · sns_mentions/ · aladin_books/ · calendar_events/  

  ★ Job  : aws glue get-job-runs --job-name bookflow-raw-pos-mart
  ★ Logs: /aws-glue/jobs/output/

(5/6)  : day07_0506_glue_agg.sh
  → sales_daily_agg + features_build Job 
EOF
