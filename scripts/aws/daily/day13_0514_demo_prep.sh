#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 13 · 5/14 ()      +             ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1.                                       ║
# ║  2.      ()                 ║
# ║  3. ETL3 Step Functions   ( fresh data)         ║
# ║  4.   start                               ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)

# ── Step 1.      ─────────────────────────
step "Step 1 ·  CloudFormation  "

bookflow status 2>/dev/null || {
  aws cloudformation list-stacks \
    --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
    --query "StackSummaries[?starts_with(StackName,\`${PROJECT}\`)].{Name:StackName,Status:StackStatus}" \
    --output table 2>/dev/null
}

# ── Step 2. ETL  READY  ─────────────────────────
step "Step 2 · ETL READY "

RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || \
          echo "${PROJECT}-cluster")
SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

READY=true

# ECS Sim
for SVC in online-sim offline-sim; do
  RUNNING=$(aws ecs describe-services \
    --cluster "${CLUSTER}" --services "${SVC}" \
    --query 'services[0].runningCount' --output text 2>/dev/null || echo "0")
  if [ "${RUNNING}" -ge 1 ]; then
    ok "[ETL1] ${SVC}: ${RUNNING} tasks"
  else
    warn "[ETL1] ${SVC}: "
    READY=false
  fi
done

# Lambda
for FN in aladin-sync event-sync sns-gen spike-detect pos-ingestor; do
  STATE=$(aws lambda get-function-configuration \
    --function-name "${PROJECT}-${FN}" \
    --query 'State' --output text 2>/dev/null || echo "NOT_FOUND")
  [ "${STATE}" = "Active" ] && ok "[ETL2] ${FN}: Active" || { warn "[ETL2] ${FN}: ${STATE}"; READY=false; }
done

# S3 Raw
for PREFIX in pos-events aladin events sns; do
  COUNT=$(aws s3 ls "s3://${RAW_BUCKET}/${PREFIX}/" --recursive 2>/dev/null | wc -l)
  [ "${COUNT}" -gt 0 ] && ok "[ETL2] S3 ${PREFIX}/: ${COUNT}" || { warn "[ETL2] ${PREFIX}/: "; READY=false; }
done

# S3 Mart
for TABLE in pos_events sales_daily features; do
  COUNT=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  [ "${COUNT}" -gt 0 ] && ok "[ETL3] Mart ${TABLE}/: ${COUNT}" || { warn "[ETL3] ${TABLE}/: "; READY=false; }
done

# Step Functions
[ -n "${SF_ARN}" ] && ok "[ETL3] Step Functions: " || { warn "[ETL3] Step Functions: "; READY=false; }

echo ""
$READY && ok "  READY ✓" || warn "    (  )"

# ── Step 3.   ( ) ──────────────────
step "Step 3 ·  "

cat << 'EOF'
  ─────────────────────────────────────────────
    ()
  ─────────────────────────────────────────────

  1. [ETL1] ECS Sim  POS   
     → AWS Console: ECS > Clusters > bookflow-cluster > Services
     → online-sim·offline-sim Running 

  2. [ETL1] Kinesis    
     → Kinesis Data Streams > bookflow-pos-events
     → Monitoring  > Get records (bytes/sec )

  3. [ETL1] Firehose → S3 pos-events  
     → S3 > bookflow-raw > pos-events/year=.../
     →  1  

  4. [ETL2] Lambda   
     → Lambda > bookflow-sns-gen > Test ({} invoke)
     → CloudWatch Logs  

  5. [ETL2] SNS   
     → Lambda > bookflow-spike-detect > Test
     → Response spikes  

  6. [ETL3] Step Functions ETL3  
     → Step Functions > bookflow-etl3 > Start execution
     →    

  7. [ETL3] S3 Mart features/   
     → S3 > bookflow-mart > features/
     → Athena : SELECT COUNT(*) FROM bookflow_mart.features

  8. [CI/CD] glue-redeploy GHA 
     → VS Code glue-jobs/raw_pos_mart.py 
     → git push → GitHub Actions   

EOF

# ── Step 4. ETL3   (  ) ─────────────
step "Step 4 · ETL3   "

if [ -n "${SF_ARN}" ]; then
  read -p "   ETL3  ? (y/N): " CONFIRM
  if [ "${CONFIRM}" = "y" ] || [ "${CONFIRM}" = "Y" ]; then
    EXEC_ARN=$(aws stepfunctions start-execution \
      --state-machine-arn "${SF_ARN}" \
      --input "{\"trigger\":\"demo-rehearsal\",\"date\":\"$(date +%Y-%m-%d)\"}" \
      --query 'executionArn' --output text)
    ok "ETL3  : ${EXEC_ARN}"
    info "  15 ·  "
  fi
fi

# ── Step 5.   START   ─────────────────────
step "Step 5 ·   (5/15)  "

cat << 'EOF'
  ─────────────────────────────────────────────
  5/15     
  ─────────────────────────────────────────────

  # 1.     (30)
  python scripts/aws/bookflow.py status

  # 2. ECS Sim  ( running skip)
  aws ecs update-service --cluster bookflow-cluster --service online-sim --desired-count 1
  aws ecs update-service --cluster bookflow-cluster --service offline-sim --desired-count 1

  # 3. sns-gen  invoke (SNS   )
  aws lambda invoke --function-name bookflow-sns-gen --payload '{}' /tmp/sns.json

  # 4. ETL3 Step Functions  (    )
  SF_ARN=$(aws cloudformation describe-stacks \
    --stack-name bookflow-99-step-functions \
    --query "Stacks[0].Outputs[?OutputKey=='Etl3StateMachineArn'].OutputValue" \
    --output text)
  aws stepfunctions start-execution \
    --state-machine-arn $SF_ARN \
    --input '{"trigger":"demo-live"}'

  # 5.     (step 4  )
  aws lambda invoke --function-name bookflow-spike-detect --payload '{}' /tmp/spike.json
  cat /tmp/spike.json

EOF

# ──    ──────────────────────────────────────
step "Day 13  "
cat << 'EOF'
  [ ]   READY  (2  FAIL)
  [ ]   7 
  [ ] ETL3   
  [ ] 5/15   

(5/15)  : day14_0515_demo.sh
  →     +  
EOF
