#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 09 · 5/8 ()  rds-redeploy GHA + E2E    ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. rds-redeploy.yml GHA workflow                        ║
# ║  2. RDS     (  )              ║
# ║  3. ETL1 → ETL2 → ETL3  E2E            ║
# ║  4.    (Kinesis ShardIterator )             ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
STREAM_NAME=$(stack_output "bookflow-20-kinesis" "StreamName" 2>/dev/null || \
              echo "${PROJECT}-pos-events")

# ── Step 1. rds-redeploy.yml  ────────────────────────────
step "Step 1 · rds-redeploy GHA workflow "

RDS_GHA="${REPO_ROOT}/.github/workflows/rds-redeploy.yml"
if [ -f "${RDS_GHA}" ]; then
  ok "rds-redeploy.yml "
  info "trigger paths:"
  grep -A3 "paths:" "${RDS_GHA}" | head -8 || true
else
  warn "rds-redeploy.yml  · .github/workflows/ "
fi

# ── Step 2. RDS  +   ──────────────────────────
step "Step 2 · RDS  +  "

if stack_exists "bookflow-20-rds"; then
  RDS_ENDPOINT=$(stack_output "bookflow-20-rds" "RdsEndpoint" 2>/dev/null || echo "")
  info "RDS Endpoint: ${RDS_ENDPOINT}"

  # psql  Lambda  
  if command -v psql > /dev/null 2>&1 && [ -n "${RDS_ENDPOINT}" ]; then
    info "psql    (VPN  )..."
    # psql VPN   
    info "  : psql -h ${RDS_ENDPOINT} -U bookflow -d bookflow -c '\\dt'"
  else
    info "RDS   Client VPN  Ansible CN  "
    info "  spike-detect   = RDS   "
  fi
else
  warn "RDS  · task-data  "
fi

# ── Step 3. E2E     ────────────────────
step "Step 3 · E2E   "

echo ""
echo "  ┌─────────────────────────────────────────────┐"
echo "  │         ETL  E2E               │"
echo "  └─────────────────────────────────────────────┘"
echo ""

# ETL1: ECS Sim → Kinesis
echo "  [ETL1] ECS Sim → Kinesis → S3"
CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || echo "")
if [ -n "${CLUSTER}" ]; then
  for SVC in online-sim offline-sim; do
    RUNNING=$(aws ecs describe-services \
      --cluster "${CLUSTER}" --services "${SVC}" \
      --query 'services[0].runningCount' --output text 2>/dev/null || echo "?")
    echo "  · ECS ${SVC}: ${RUNNING} tasks"
  done
fi

POS_COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/pos-events/" --recursive 2>/dev/null | wc -l)
echo "  · S3 pos-events/: ${POS_COUNT}"

echo ""
echo "  [ETL2] Lambda → S3 Raw"
for PREFIX in aladin events sns; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  echo "  · ${PREFIX}/: ${COUNT}"
done

echo ""
echo "  [ETL3] Glue → S3 Mart"
for TABLE in pos_events sns_mentions aladin_books calendar_events sales_daily features; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  echo "  · Mart ${TABLE}/: ${COUNT}"
done

echo ""

# ── Step 4. Kinesis    ───────────────────────
step "Step 4 · Kinesis    "

SHARD_ID=$(aws kinesis describe-stream-summary \
  --stream-name "${STREAM_NAME}" \
  --query 'StreamDescriptionSummary.OpenShardCount' \
  --output text 2>/dev/null || echo "0")
info "Kinesis ${STREAM_NAME}: ${SHARD_ID} shards"

#   
SHARD_ITER=$(aws kinesis get-shard-iterator \
  --stream-name "${STREAM_NAME}" \
  --shard-id "shardId-000000000000" \
  --shard-iterator-type LATEST \
  --query 'ShardIterator' --output text 2>/dev/null || echo "")

if [ -n "${SHARD_ITER}" ]; then
  info " 30    ..."
  sleep 30
  RECORDS=$(aws kinesis get-records \
    --shard-iterator "${SHARD_ITER}" \
    --limit 3 \
    --query 'Records[].Data' \
    --output json 2>/dev/null || echo "[]")

  COUNT=$(echo "${RECORDS}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
  if [ "${COUNT}" -gt 0 ]; then
    ok "Kinesis  ${COUNT} "
    echo "${RECORDS}" | python3 -c "
import sys, json, base64
for d in json.load(sys.stdin):
    try:
        rec = json.loads(base64.b64decode(d))
        print(f'  · isbn13={rec.get(\"isbn13\",\"?\")} loc={rec.get(\"location_id\",\"?\")} ch={rec.get(\"channel\",\"?\")}')
    except:
        pass
" 2>/dev/null
  else
    info "Kinesis   (ECS Sim  30~90 )"
  fi
fi

# ── Step 5. Step Functions   () ─────────────────
step "Step 5 · ETL3 Step Functions   (E2E  )"

SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

if [ -n "${SF_ARN}" ]; then
  read -p "  ETL3   ? (y/N): " CONFIRM
  if [ "${CONFIRM}" = "y" ] || [ "${CONFIRM}" = "Y" ]; then
    EXEC_ARN=$(aws stepfunctions start-execution \
      --state-machine-arn "${SF_ARN}" \
      --input "{\"trigger\":\"e2e-day09\",\"date\":\"$(date +%Y-%m-%d)\"}" \
      --query 'executionArn' --output text)
    info "ETL3  : ${EXEC_ARN}"
    info "AWS Console: https://ap-northeast-1.console.aws.amazon.com/states/home"
  else
    info "ETL3   skip"
  fi
else
  warn "Step Functions ARN "
fi

# ──    ──────────────────────────────────────
step "Day 09  "
cat << 'EOF'
  [ ] rds-redeploy.yml  
  [ ] RDS    (spike_events · sales_realtime · inventory)
  [ ] E2E  :
      ECS Sim → Kinesis ✓
      Lambda → S3 Raw ✓
      Glue → S3 Mart ✓
  [ ] Kinesis   
  [ ] ETL3 Step Functions 

(5/9)  : day10_0509_e2e_verify.sh
  →    + CloudWatch  
EOF
