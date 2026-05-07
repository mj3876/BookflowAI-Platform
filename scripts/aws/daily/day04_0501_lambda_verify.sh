#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 04 · 5/1 ()   Lambda                     ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. 7 Lambda  ACTIVE                           ║
# ║  2. EventBridge  5                                 ║
# ║  3. S3 Raw     (aladin · events · sns)     ║
# ║  4. pos-ingestor Kinesis    + RDS         ║
# ║  5. CloudWatch Logs                                  ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
STREAM_NAME=$(stack_output "bookflow-20-kinesis" "StreamName" 2>/dev/null || \
              echo "${PROJECT}-pos-events")

# ── Step 1.  Lambda   ────────────────────────────
step "Step 1 ·  Lambda  (7)"

LAMBDAS=(aladin-sync event-sync sns-gen spike-detect forecast-trigger secret-forwarder pos-ingestor)
ALL_OK=true

for FN in "${LAMBDAS[@]}"; do
  FULL="${PROJECT}-${FN}"
  STATE=$(aws lambda get-function-configuration \
    --function-name "${FULL}" \
    --query 'State' --output text 2>/dev/null || echo "NOT_FOUND")
  if [ "${STATE}" = "Active" ]; then
    ok "${FULL}: Active"
  else
    warn "${FULL}: ${STATE}"
    ALL_OK=false
  fi
done

$ALL_OK && ok " Lambda Active" || warn " Lambda  ·   "

# ── Step 2. EventBridge    ───────────────────────
step "Step 2 · EventBridge   "

aws events list-rules \
  --name-prefix "${PROJECT}" \
  --query 'Rules[].{Name:Name,State:State,Schedule:ScheduleExpression}' \
  --output table 2>/dev/null || warn "EventBridge  "

# ── Step 3. S3 Raw    ──────────────────────────
step "Step 3 · S3 Raw   "

for PREFIX in aladin events sns; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  if [ "${COUNT}" -gt 0 ]; then
    ok "${PREFIX}/: ${COUNT} "
    aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | tail -2
  else
    warn "${PREFIX}/:   · Lambda invoke "
  fi
done

# ── Step 4. Kinesis    → pos-ingestor  ──
step "Step 4 · Kinesis PutRecord  (pos-ingestor ESM )"

TEST_RECORD=$(python3 -c "
import json, uuid
from datetime import datetime, timezone
print(json.dumps({
  'tx_id': str(uuid.uuid4()),
  'isbn13': '9791162542927',
  'location_id': 3,
  'qty': 1,
  'sale_price': 15000,
  'channel': 'OFFLINE',
  'created_at': datetime.now(timezone.utc).isoformat()
}))
")

info "Kinesis PutRecord..."
aws kinesis put-record \
  --stream-name "${STREAM_NAME}" \
  --data "$(echo -n "${TEST_RECORD}" | base64)" \
  --partition-key "test-9791162542927" \
  --region "${REGION}" \
  --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  ✓ ShardId={r[\"ShardId\"]} · SeqNo={r[\"SequenceNumber\"][:20]}...')
"

info "10   pos-ingestor  ..."
sleep 10
aws logs tail "/aws/lambda/${PROJECT}-pos-ingestor" --since 1m 2>/dev/null | \
  grep -E "\[pos-ingestor\]|ERROR|Traceback" | head -5 || warn "  (ESM )"

# ── Step 5. CloudWatch Logs   ────────────────────────
step "Step 5 · CloudWatch Logs   ( 1)"

for FN in aladin-sync event-sync sns-gen; do
  LOG_GROUP="/aws/lambda/${PROJECT}-${FN}"
  ERR_CNT=$(aws logs filter-log-events \
    --log-group-name "${LOG_GROUP}" \
    --filter-pattern "ERROR" \
    --start-time $(( $(date +%s) * 1000 - 3600000 )) \
    --query 'length(events)' \
    --output text 2>/dev/null || echo "N/A")
  info "${FN}   (1h): ${ERR_CNT}"
done

# ── Step 6.   ────────────────────────────────────────
step "Step 6 ·  ETL  "

echo ""
echo "  ETL1 (ECS Sim)"
for SVC in online-sim offline-sim; do
  CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || echo "${PROJECT}-cluster")
  RUNNING=$(aws ecs describe-services \
    --cluster "${CLUSTER}" --services "${SVC}" \
    --query 'services[0].runningCount' --output text 2>/dev/null || echo "?")
  info "  ${SVC}: ${RUNNING} tasks running"
done

echo ""
echo "  ETL2 (Lambda)"
RAW_TOTAL=$(aws s3 ls "s3://${RAW_BUCKET}/" --recursive 2>/dev/null | wc -l)
info "  S3 Raw   : ${RAW_TOTAL}"

# ──    ──────────────────────────────────────
step "Day 04  "
cat << 'EOF'
  [ ] 7 Lambda  Active
  [ ] EventBridge  5 (aladin·event·sns·spike·forecast)
  [ ] S3 aladin/ · events/ · sns/  
  [ ] Kinesis PutRecord → pos-ingestor ESM 
  [ ] CloudWatch Logs   (  )

(5/2)  : day05_0502_etl1_etl2_done.sh
  → ETL1+2 End-to-End    + Glue 
EOF
