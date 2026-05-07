#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 10 · 5/9 ()     + CloudWatch        ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. S3 Mart 6                        ║
# ║  2. Lambda  CloudWatch                          ║
# ║  3. Glue Job                                  ║
# ║  4.     (  · ECS )               ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")

# ── Step 1. S3 Mart    ─────────────────────────
step "Step 1 · S3 Mart  "

for TABLE in pos_events sns_mentions sales_daily features; do
  info "${TABLE}  :"
  aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" 2>/dev/null | head -5 || warn "${TABLE} "
done

# ── Step 2. Parquet     ────────────────────
step "Step 2 · Parquet   (python3 + pyarrow)"

command -v python3 > /dev/null && python3 -c "import pyarrow" 2>/dev/null || {
  info "pyarrow  · : pip install pyarrow"
  info "Parquet   skip (Glue Studio Console  )"
}

# Glue DataBrew  Athena  
info ""
info "Athena   ():"
echo ""
cat << 'ATHENA_SQL'
  -- Athena Console: https://ap-northeast-1.console.aws.amazon.com/athena
  -- Database: bookflow_mart

  SELECT * FROM bookflow_mart.pos_events LIMIT 5;
  SELECT * FROM bookflow_mart.sales_daily LIMIT 5;
  SELECT COUNT(*) FROM bookflow_mart.features;
ATHENA_SQL

# ── Step 3. Lambda   ───────────────────────────────
step "Step 3 · Lambda  ( 24)"

START_TIME=$(date -u -d "24 hours ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || \
             date -u -v-24H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || \
             python3 -c "from datetime import datetime,timedelta,timezone; print((datetime.now(timezone.utc)-timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ'))")
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

for FN in aladin-sync event-sync sns-gen spike-detect pos-ingestor; do
  ERRORS=$(aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Errors \
    --dimensions "Name=FunctionName,Value=${PROJECT}-${FN}" \
    --start-time "${START_TIME}" \
    --end-time "${END_TIME}" \
    --period 86400 \
    --statistics Sum \
    --query 'Datapoints[0].Sum' \
    --output text 2>/dev/null || echo "N/A")

  INVOCATIONS=$(aws cloudwatch get-metric-statistics \
    --namespace AWS/Lambda \
    --metric-name Invocations \
    --dimensions "Name=FunctionName,Value=${PROJECT}-${FN}" \
    --start-time "${START_TIME}" \
    --end-time "${END_TIME}" \
    --period 86400 \
    --statistics Sum \
    --query 'Datapoints[0].Sum' \
    --output text 2>/dev/null || echo "0")

  info "${FN}: ${INVOCATIONS}  · ${ERRORS} "
done

# ── Step 4. Glue Job    ──────────────────────────
step "Step 4 · Glue Job  "

for JOB in raw-pos-mart raw-sns-mart raw-aladin-mart raw-event-mart sales-daily-agg features-build; do
  LAST_RUN=$(aws glue get-job-runs \
    --job-name "${PROJECT}-${JOB}" \
    --max-results 1 \
    --query 'JobRuns[0].{State:JobRunState,Duration:ExecutionTime,Started:StartedOn}' \
    --output json 2>/dev/null | python3 -c "
import sys, json
try:
    r = json.load(sys.stdin)
    dur = r.get('Duration', 0)
    print(f'    {r[\"State\"]:12} {dur}s · {r[\"Started\"][:10]}')
except:
    print('     ')
" 2>/dev/null)
  info "${JOB}:${LAST_RUN}"
done

# ── Step 5.    (spike_events ) ────────
step "Step 5 · spike-detect  "

info "spike-detect  invoke ( SNS  Z-score )..."
aws lambda invoke \
  --function-name "${PROJECT}-spike-detect" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/spike_result.json \
  --log-type Tail \
  --query 'LogResult' --output text 2>/dev/null | base64 -d | grep "\[spike-detect\]" | head -3 || true

[ -f /tmp/spike_result.json ] && info "Response: $(cat /tmp/spike_result.json)"

# ── Step 6.    ───────────────────────────────────
step "Step 6 ·   "

CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName" 2>/dev/null || \
          echo "${PROJECT}-cluster")

echo ""
info "ECS Sim :"
for SVC in online-sim offline-sim; do
  RUNNING=$(aws ecs describe-services \
    --cluster "${CLUSTER}" --services "${SVC}" \
    --query 'services[0].runningCount' --output text 2>/dev/null || echo "?")
  info "  ${SVC}: ${RUNNING} tasks"
done

echo ""
cat << 'EOF'
   ECS Sim  ():
    aws ecs update-service --cluster bookflow-cluster --service online-sim --desired-count 0
    aws ecs update-service --cluster bookflow-cluster --service offline-sim --desired-count 0

  Lambda    (SNS  10 · / )
   S3 Raw    → 5/12 Glue Job     

EOF

# ──    ──────────────────────────────────────
step "Day 10  "
cat << 'EOF'
  [ ] S3 Mart 6    
  [ ] Lambda   (   < 5%)
  [ ] Glue Job   
  [ ] spike-detect  
  [ ]  ECS Sim   ( or )

  ■ ETL1 (ECS Sim) ✓
  ■ ETL2 (Lambda × 7) ✓
  ■ ETL3 (Glue × 6 + Step Functions) ✓
  ■ CI/CD (glue-redeploy GHA) ✓

(5/12)  : day11_0512_integration.sh
  →   +  
EOF
