#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 11 · 5/12 ()    +                   ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. ECS Sim   (  )                       ║
# ║  2.    Glue ETL3                      ║
# ║  3.    +                                  ║
# ║  4. Lambda    +                               ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")

# ── Step 1. ECS Sim  ────────────────────────────────────
step "Step 1 · ECS Sim  (  )"

CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || \
          echo "${PROJECT}-cluster")

for SVC in online-sim offline-sim; do
  RUNNING=$(aws ecs describe-services \
    --cluster "${CLUSTER}" --services "${SVC}" \
    --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")
  if [ "${RUNNING}" = "0" ]; then
    aws ecs update-service --cluster "${CLUSTER}" --service "${SVC}" \
      --desired-count 1 --region "${REGION}" --output json > /dev/null
    ok "${SVC} "
  else
    ok "${SVC}: ${RUNNING} tasks  "
  fi
done

# ── Step 2.     ────────────────────────────
step "Step 2 ·    "

info "S3 Raw   :"
for PREFIX in pos-events aladin events sns; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  SIZE_APPROX=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | \
    awk '{sum+=$3} END {printf "%.1f MB", sum/1024/1024}')
  info "  ${PREFIX}/: ${COUNT} (${SIZE_APPROX})"
done

# ── Step 3. ETL3    (  ) ───
step "Step 3 · ETL3 Step Functions  "

SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

if [ -n "${SF_ARN}" ]; then
  info "ETL3   ..."
  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "${SF_ARN}" \
    --input "{\"trigger\":\"integration-day11\",\"date\":\"$(date +%Y-%m-%d)\"}" \
    --query 'executionArn' --output text)
  info "Execution: ${EXEC_ARN}"
  info "Console : https://ap-northeast-1.console.aws.amazon.com/states/home"

  #   (  ·  )
  info "Step Functions  ~15  ·  ..."
else
  #  Job  
  info "Step Functions  ·  Job ..."
  for JOB in raw-pos-mart raw-sns-mart raw-aladin-mart raw-event-mart sales-daily-agg features-build; do
    RUN_ID=$(aws glue start-job-run \
      --job-name "${PROJECT}-${JOB}" \
      --region "${REGION}" \
      --query 'JobRunId' --output text 2>/dev/null || echo "FAILED")
    info "${JOB} : ${RUN_ID}"
  done
fi

# ── Step 4. Lambda    ────────────────────────────
step "Step 4 · Lambda    ( 3)"

START_3D=$(python3 -c "
from datetime import datetime,timedelta,timezone
print((datetime.now(timezone.utc)-timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ'))
")

echo ""
info "Lambda  :"
for FN in aladin-sync event-sync sns-gen spike-detect pos-ingestor; do
  LOG_GROUP="/aws/lambda/${PROJECT}-${FN}"
  info "── ${FN} ──"
  aws logs filter-log-events \
    --log-group-name "${LOG_GROUP}" \
    --filter-pattern "ERROR Traceback" \
    --start-time $(python3 -c "
from datetime import datetime,timedelta,timezone
print(int((datetime.now(timezone.utc)-timedelta(days=3)).timestamp()*1000))
") \
    --query 'events[-3:].message' \
    --output text 2>/dev/null | head -5 || info "    (  )"
done

# ── Step 5.     (  ) ───────────────
step "Step 5 ·     (  )"

cat << 'EOF'
     Lambda  :

  #  Lambda  
  cd BookFlowAI-Platform/infra/aws/99-serverless/lambdas/spike-detect
  zip /tmp/spike-detect.zip index.py
  aws lambda update-function-code \
    --function-name bookflow-spike-detect \
    --zip-file fileb:///tmp/spike-detect.zip

  #  SAM  (  )
  cd BookFlowAI-Platform
  python scripts/aws/bookflow.py task lambdas

  # Glue   
  aws s3 cp glue-jobs/raw_pos_mart.py s3://bookflow-glue-scripts-ACCOUNT/scripts/
  aws glue start-job-run --job-name bookflow-raw-pos-mart
EOF

# ── Step 6.     ─────────────────────
step "Step 6 ·    "

PASS=0
FAIL=0

# Lambda Active 
for FN in aladin-sync event-sync sns-gen spike-detect pos-ingestor; do
  STATE=$(aws lambda get-function-configuration \
    --function-name "${PROJECT}-${FN}" \
    --query 'State' --output text 2>/dev/null || echo "NOT_FOUND")
  if [ "${STATE}" = "Active" ]; then
    PASS=$((PASS+1))
  else
    warn "${FN}: ${STATE}"
    FAIL=$((FAIL+1))
  fi
done

# S3 Raw   
for PREFIX in aladin events sns pos-events; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    PASS=$((PASS+1))
  else
    warn "S3 Raw ${PREFIX}/  "
    FAIL=$((FAIL+1))
  fi
done

# S3 Mart   
for TABLE in pos_events sns_mentions sales_daily; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    PASS=$((PASS+1))
  else
    warn "S3 Mart ${TABLE}/ "
    FAIL=$((FAIL+1))
  fi
done

echo ""
echo "    : PASS=${PASS} FAIL=${FAIL}"
[ "${FAIL}" -eq 0 ] && ok "   " || warn "${FAIL}   "

# ──    ──────────────────────────────────────
step "Day 11  "
cat << 'EOF'
  [ ] ECS Sim  
  [ ]    (Raw:     )
  [ ] ETL3 Step Functions  (  )
  [ ] Lambda    + 
  [ ]   PASS

(5/13)  : day12_0513_data_verify.sh
  →     +   
EOF
