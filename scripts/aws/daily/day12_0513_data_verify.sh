#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 12 · 5/13 ()                        ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. S3 Mart features/  +                       ║
# ║  2. Athena                            ║
# ║  3. spike_events                                     ║
# ║  4.                                       ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
MART_BUCKET=$(stack_output "bookflow-00-s3" "MartBucketName" 2>/dev/null || \
              echo "${PROJECT}-mart-${ACCOUNT}")
RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")

# ── Step 1. S3 Mart      ──────────────────
step "Step 1 · S3 Mart  "

echo ""
printf "  %-25s %10s %10s\n" "" " " "(MB)"
printf "  %-25s %10s %10s\n" "─────────────────────" "────────" "────────"
for TABLE in pos_events sns_mentions aladin_books calendar_events sales_daily features; do
  FILES=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | wc -l)
  SIZE=$(aws s3 ls "s3://${MART_BUCKET}/${TABLE}/" --recursive 2>/dev/null | \
         awk '{sum+=$3} END {printf "%.2f", sum/1024/1024}')
  printf "  %-25s %10s %10s\n" "${TABLE}/" "${FILES}" "${SIZE}"
done
echo ""

# ── Step 2. features/     ────────────────────
step "Step 2 · features/   ()"

info "features/  :"
aws s3 ls "s3://${MART_BUCKET}/features/" 2>/dev/null | grep "PRE" | head -10 || \
  warn "features/  · day07  "

# ── Step 3. Athena   ───────────────────────────────
step "Step 3 · Athena    "

cat << 'ATHENA'
  ─────────────────────────────────────────────
  Athena Console: https://ap-northeast-1.console.aws.amazon.com/athena
  Database: bookflow_mart
  ─────────────────────────────────────────────

  -- 1.   TOP 5 
  SELECT isbn13, SUM(total_qty) AS total_sold
  FROM bookflow_mart.sales_daily
  GROUP BY isbn13
  ORDER BY total_sold DESC
  LIMIT 5;

  -- 2.   
  SELECT channel,
         SUM(total_revenue) AS revenue,
         SUM(total_qty) AS qty
  FROM bookflow_mart.sales_daily
  GROUP BY channel;

  -- 3. features  
  SELECT COUNT(*) as rows,
         COUNT(DISTINCT isbn13) as isbns,
         AVG(sns_mention_cnt) as avg_sns,
         AVG(rolling_14d_qty) as avg_14d_qty
  FROM bookflow_mart.features;

  -- 4. spike  
  SELECT is_spike_seed,
         COUNT(*) as cnt,
         AVG(mention_count) as avg_mentions
  FROM bookflow_mart.sns_mentions
  GROUP BY is_spike_seed;

ATHENA

# ── Step 4. spike-detect   ───────────────────────────
step "Step 4 · spike-detect   "

info "spike-detect  invoke..."
aws lambda invoke \
  --function-name "${PROJECT}-spike-detect" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/spike_final.json \
  --log-type Tail \
  --query 'LogResult' --output text 2>/dev/null | base64 -d | \
  grep -E "\[spike-detect\]" | head -5 || true

[ -f /tmp/spike_final.json ] && info "Response: $(cat /tmp/spike_final.json)"

# ── Step 5.  sns-gen    ─────────────────
step "Step 5 ·    "

cat << 'EOF'
       :

  1. sns-gen   ISBN baseline_lam 
     (Secrets Manager: bookflow/sns-gen-config )

  2. sns-gen  invoke  SNS  
     aws lambda invoke \
       --function-name bookflow-sns-gen \
       --payload '{}' \
       /tmp/sns_spike.json

  3.  spike-detect invoke
     aws lambda invoke \
       --function-name bookflow-spike-detect \
       --payload '{}' \
       /tmp/spike_detect.json

  4. RDS spike_events   (Ansible CN  VPN )
     SELECT * FROM spike_events ORDER BY detected_at DESC LIMIT 5;
EOF

# ── Step 6.     ──────────────────────────────
step "Step 6 ·    "

echo ""
info "    :"
ITEMS=(
  "ECS Sim 2  running"
  "Lambda 7 Active"
  "S3 Raw 4   "
  "S3 Mart 6   "
  "Step Functions ETL3 SUCCEEDED"
  "glue-redeploy GHA workflow "
  "CloudWatch Logs  "
)
for ITEM in "${ITEMS[@]}"; do
  echo "  [ ] ${ITEM}"
done

# ──    ──────────────────────────────────────
step "Day 12  "
cat << 'EOF'
  [ ] S3 Mart 6  /  
  [ ] Athena    (4 )
  [ ] spike-detect   
  [ ]    
  [ ]    

(5/14)  : day13_0514_demo_prep.sh
  →     + 
EOF
