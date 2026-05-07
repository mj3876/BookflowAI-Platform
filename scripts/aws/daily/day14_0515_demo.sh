#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 14 · 5/15 ()                          ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1.  30 :   READY                      ║
# ║  2.  :                                    ║
# ║  3.  :   ()                              ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || \
          echo "${PROJECT}-cluster")
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")
STREAM_NAME=$(stack_output "bookflow-20-kinesis" "StreamName" 2>/dev/null || \
              echo "${PROJECT}-pos-events")

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║    BookFlow ETL   (5/15)      ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# ──  30 : READY  ─────────────────────────────────
step "▶  30  "

ALL_READY=true

# ECS Sim
for SVC in online-sim offline-sim; do
  R=$(aws ecs describe-services --cluster "${CLUSTER}" --services "${SVC}" \
      --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")
  if [ "${R}" -ge 1 ]; then
    ok "[ETL1] ${SVC}: RUNNING (${R})"
  else
    warn "[ETL1] ${SVC}: STOPPED →  ..."
    aws ecs update-service --cluster "${CLUSTER}" --service "${SVC}" \
      --desired-count 1 --region "${REGION}" > /dev/null
    ALL_READY=false
  fi
done

# Lambda 7
for FN in aladin-sync event-sync sns-gen spike-detect forecast-trigger secret-forwarder pos-ingestor; do
  ST=$(aws lambda get-function-configuration --function-name "${PROJECT}-${FN}" \
       --query 'State' --output text 2>/dev/null || echo "ERR")
  [ "${ST}" = "Active" ] && ok "[ETL2] ${FN}: Active" || { warn "[ETL2] ${FN}: ${ST}"; ALL_READY=false; }
done

# Kinesis
K_ST=$(aws kinesis describe-stream-summary --stream-name "${STREAM_NAME}" \
       --query 'StreamDescriptionSummary.StreamStatus' --output text 2>/dev/null || echo "ERR")
[ "${K_ST}" = "ACTIVE" ] && ok "[ETL1] Kinesis: ACTIVE" || warn "[ETL1] Kinesis: ${K_ST}"

# Step Functions
[ -n "${SF_ARN}" ] && ok "[ETL3] Step Functions: " || { warn "[ETL3] Step Functions: "; ALL_READY=false; }

echo ""
$ALL_READY && ok " READY ·    ✓" || warn "   "

# ──  : SNS   + ETL3  ───────────────────
step "▶   ( )"

info "1. sns-gen invoke (SNS    )..."
aws lambda invoke \
  --function-name "${PROJECT}-sns-gen" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/demo_sns.json > /dev/null 2>&1
SNS_RESULT=$(cat /tmp/demo_sns.json 2>/dev/null || echo "{}")
ok "sns-gen: $(echo ${SNS_RESULT} | python3 -c 'import sys,json; r=json.load(sys.stdin); print(f"{r.get(\"records\",\"?\")}" + " records")' 2>/dev/null || echo 'done')"

info "2. ETL3 Step Functions ..."
if [ -n "${SF_ARN}" ]; then
  EXEC_ARN=$(aws stepfunctions start-execution \
    --state-machine-arn "${SF_ARN}" \
    --input "{\"trigger\":\"demo-live-0515\",\"date\":\"$(date +%Y-%m-%d)\"}" \
    --query 'executionArn' --output text)
  ok "ETL3  : ${EXEC_ARN}"
  echo "  Console: https://ap-northeast-1.console.aws.amazon.com/states/home"
fi

# ──  :    ──────────────────────────────
step "▶    (  )"

info "spike-detect invoke..."
aws lambda invoke \
  --function-name "${PROJECT}-spike-detect" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/demo_spike.json \
  --log-type Tail \
  --query 'LogResult' --output text 2>/dev/null | base64 -d | \
  grep -E "\[spike-detect\]" | head -3 || true

SPIKE_RESULT=$(cat /tmp/demo_spike.json 2>/dev/null || echo "{}")
ok "spike-detect: $(echo ${SPIKE_RESULT} | python3 -c 'import sys,json; r=json.load(sys.stdin); print(f"{r.get(\"spikes\",0)} spikes detected")' 2>/dev/null || echo 'done')"

# ── S3     ─────────────────────────────
step "▶      "

cat << 'EOF'
  # Kinesis   ( )
  aws cloudwatch get-metric-statistics \
    --namespace AWS/Kinesis \
    --metric-name IncomingRecords \
    --dimensions Name=StreamName,Value=bookflow-pos-events \
    --start-time $(date -u -d "5 min ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ) \
    --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --period 60 --statistics Sum

  # S3 pos-events   
  aws s3 ls s3://bookflow-raw-ACCOUNT/pos-events/ --recursive | tail -5

  # S3 Mart features 
  aws s3 ls s3://bookflow-mart-ACCOUNT/features/ | tail -5

  # Lambda   ( )
  aws logs tail /aws/lambda/bookflow-sns-gen --follow
  aws logs tail /aws/lambda/bookflow-spike-detect --follow
EOF

# ──    ────────────────────────────────────────────
step "▶     "

echo ""
echo "  ┌─────────────────────────────────────────┐"
echo "  │        BookFlow ETL         │"
echo "  ├─────────────────────────────────────────┤"

#   
POS_FILES=$(aws s3 ls "s3://${RAW_BUCKET}/pos-events/" --recursive 2>/dev/null | wc -l)
SNS_FILES=$(aws s3 ls "s3://${RAW_BUCKET}/sns/" --recursive 2>/dev/null | wc -l)
FEAT_FILES=$(aws s3 ls "s3://${MART_BUCKET}/features/" --recursive 2>/dev/null | wc -l)

echo "  │  ETL1 pos-events S3 : ${POS_FILES}"
echo "  │  ETL2 SNS S3 :        ${SNS_FILES}"
echo "  │  ETL3 features Parquet:   ${FEAT_FILES}"
echo "  │"
echo "  │  : 4/28 ~ 5/15 (14 working days)"
echo "  │  : "
echo "  └─────────────────────────────────────────┘"

# ── ()      ──────────────────────────
step "▶     ( ·   )"

cat << 'EOF'
  # ECS Sim  ( )
  python scripts/aws/bookflow.py task etl-streaming --down

  # Lambda + Glue  ( )
  aws events disable-rule --name bookflow-sns-gen-cron
  aws events disable-rule --name bookflow-aladin-sync-cron

  #   (  )
  python scripts/aws/bookflow.py base-down

  ★ base-down     
  ★ S3      (data )
EOF

echo ""
ok "Day 14 ·   🎉"
echo ""
