#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 05 · 5/2 ()  ETL1+2   + Glue         ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. ETL1(ECS Sim → Kinesis → Firehose → S3)     ║
# ║  2. ETL2(Lambda → S3 Raw)                    ║
# ║  3. Glue ETL3  S3                          ║
# ║  4.                     ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT}"

# ── Step 1. Firehose → S3 pos-events   ─────────────
step "Step 1 · Kinesis Firehose → S3 pos-events "

TODAY_PREFIX="pos-events/year=$(date +%Y)/month=$(date +%-m)/day=$(date +%-d)/"
POS_COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${TODAY_PREFIX}" 2>/dev/null | wc -l)

if [ "${POS_COUNT}" -gt 0 ]; then
  ok "pos-events  : ${POS_COUNT}"
  aws s3 ls "s3://${RAW_BUCKET}/${TODAY_PREFIX}" | tail -3
else
  warn "pos-events   · ECS Sim   "
  info "Firehose buffering interval = 60 · ECS Sim 2   "
fi

# ── Step 2. S3 Raw    ────────────────────────────
step "Step 2 · S3 Raw  "

for PREFIX in pos-events aladin events sns; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  SIZE=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive --human-readable 2>/dev/null | \
         awk '{sum+=$3} END {print sum " " $4}' || echo "0")
  info "${PREFIX}/: ${COUNT}"
done

# ── Step 3.     ────────────────────────────
step "Step 3 ·    "

# sns    
LATEST_SNS=$(aws s3 ls "s3://${RAW_BUCKET}/sns/" --recursive 2>/dev/null | \
  sort | tail -1 | awk '{print $4}')

if [ -n "${LATEST_SNS}" ]; then
  info "sns  (): ${LATEST_SNS}"
  aws s3 cp "s3://${RAW_BUCKET}/${LATEST_SNS}" /tmp/sns_sample.json.gz --quiet 2>/dev/null && \
    zcat /tmp/sns_sample.json.gz 2>/dev/null | head -3 | python3 -m json.tool 2>/dev/null | head -20 || \
    warn "sns   "
fi

# aladin   
LATEST_ALADIN=$(aws s3 ls "s3://${RAW_BUCKET}/aladin/" --recursive 2>/dev/null | \
  sort | tail -1 | awk '{print $4}')

if [ -n "${LATEST_ALADIN}" ]; then
  info "aladin : ${LATEST_ALADIN}"
  aws s3 cp "s3://${RAW_BUCKET}/${LATEST_ALADIN}" /tmp/aladin_sample.json.gz --quiet 2>/dev/null && \
    zcat /tmp/aladin_sample.json.gz 2>/dev/null | head -1 | python3 -m json.tool 2>/dev/null || \
    warn "aladin   "
fi

# ── Step 4. Glue  S3  ──────────────────────────
step "Step 4 · Glue  S3  (bookflow-99-glue-catalog  )"

# glue-scripts   
if aws s3 ls "s3://${GLUE_BUCKET}" > /dev/null 2>&1; then
  info "Glue scripts  : s3://${GLUE_BUCKET}"
  GLUE_JOBS_DIR="${REPO_ROOT}/glue-jobs"
  if [ -d "${GLUE_JOBS_DIR}" ]; then
    aws s3 sync "${GLUE_JOBS_DIR}/" "s3://${GLUE_BUCKET}/scripts/" \
      --region "${REGION}" \
      --exclude "*.pyc" --exclude "__pycache__/*" \
      --output json | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    print(f'  ✓ sync ')
except:
    pass
" 2>/dev/null
    ok "Glue  6 S3  "
    aws s3 ls "s3://${GLUE_BUCKET}/scripts/" | sort
  else
    warn "glue-jobs/  "
  fi
else
  warn "Glue scripts   · 5/5 day06 glue-catalog   sync"
fi

# ── Step 5. EventBridge     ──────────────────
step "Step 5 · EventBridge    (  )"

aws events list-rules \
  --name-prefix "${PROJECT}" \
  --query 'Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}' \
  --output table 2>/dev/null

# sns-gen (10) + aladin·event ()  ENABLED 
DISABLED=$(aws events list-rules \
  --name-prefix "${PROJECT}" \
  --query 'Rules[?State==`DISABLED`].Name' \
  --output text 2>/dev/null)

if [ -z "${DISABLED}" ]; then
  ok " EventBridge  ENABLED"
else
  warn "DISABLED : ${DISABLED}"
fi

# ── Step 6.   -  ECS DesiredCount 0  ─────────
step "Step 6 · ()  ECS Sim "

cat << 'EOF'
    ECS Sim  ( ):

    CLUSTER=$(aws cloudformation describe-stacks \
      --stack-name bookflow-30-ecs-cluster \
      --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" \
      --output text)

    aws ecs update-service --cluster $CLUSTER --service online-sim --desired-count 0
    aws ecs update-service --cluster $CLUSTER --service offline-sim --desired-count 0

   :
    aws ecs update-service --cluster $CLUSTER --service online-sim --desired-count 1
    aws ecs update-service --cluster $CLUSTER --service offline-sim --desired-count 1

  Lambda    (SNS  · ·  )
EOF

# ──    ──────────────────────────────────────
step "Day 05  "
cat << 'EOF'
  [ ] Firehose → S3 pos-events  
  [ ] S3 Raw 4   
  [ ] sns/aladin  JSON  
  [ ] Glue  6 S3  (  )
  [ ] EventBridge   ENABLED

  ■ ETL1 (ECS Sim → Kinesis → Firehose → S3) ✓
  ■ ETL2 (Lambda × 7 → S3 Raw) ✓

(5/5)  : day06_0505_glue_raw.sh
  → Glue Catalog + raw mart 4  +  Job 
EOF
