#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 03 · 4/30 ()  VPC Lambda                 ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. spike-detect Lambda   (VPC · RDS )        ║
# ║  2. pos-ingestor Lambda   (Kinesis ESM · RDS)    ║
# ║  3. Lambda Layer  (psycopg2 · redis-py)                  ║
# ║  4. VPC   Kinesis ESM                     ║
# ║  : RDS + Redis + bookflow-ai VPC peering         ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)
ARTIFACT_BUCKET="${PROJECT}-cp-artifacts-${ACCOUNT}"
LAMBDA_BASE="${REPO_ROOT}/infra/aws/99-serverless/lambdas"

# ── Step 1.    (RDS · Redis · VPC Peering) ────────
step "Step 1 ·   "

RDS_OK=false
REDIS_OK=false
PEERING_OK=false

stack_exists "bookflow-20-rds"    && RDS_OK=true    || warn "RDS  (spike-detect·pos-ingestor  )"
stack_exists "bookflow-20-redis"  && REDIS_OK=true  || warn "Redis  (pos-ingestor Redis  )"
stack_exists "bookflow-10-peering-bookflow-ai-data" && PEERING_OK=true || warn "bookflow-ai↔data peering  (VPC Lambda→RDS )"
stack_exists "bookflow-99-lambdas" || err "bookflow-99-lambdas  · day02  "

$RDS_OK    && ok "RDS  " || true
$REDIS_OK  && ok "Redis  " || true
$PEERING_OK && ok "VPC Peering " || true

# ── Step 2. Lambda Layer  (psycopg2 + redis) ──────────────
step "Step 2 · Lambda Layer  (psycopg2 · redis-py)"

LAYER_DIR="/tmp/bookflow-lambda-layer"
rm -rf "${LAYER_DIR}" && mkdir -p "${LAYER_DIR}/python"

info "psycopg2-binary + redis  ..."
pip install --quiet \
  psycopg2-binary \
  redis \
  --target "${LAYER_DIR}/python" \
  --platform manylinux2014_x86_64 \
  --python-version 3.12 \
  --only-binary=:all:

LAYER_ZIP="/tmp/bookflow-vpc-layer.zip"
(cd "${LAYER_DIR}" && zip -qr "${LAYER_ZIP}" .)
aws s3 cp "${LAYER_ZIP}" "s3://${ARTIFACT_BUCKET}/lambda-code/vpc-layer.zip" --quiet
ok "Layer zip → S3"

# Layer publish
LAYER_ARN=$(aws lambda publish-layer-version \
  --layer-name "${PROJECT}-vpc-deps" \
  --description "psycopg2-binary + redis-py for VPC Lambdas" \
  --content "S3Bucket=${ARTIFACT_BUCKET},S3Key=lambda-code/vpc-layer.zip" \
  --compatible-runtimes python3.12 \
  --region "${REGION}" \
  --query 'LayerVersionArn' --output text)
ok "Layer ARN: ${LAYER_ARN}"

# ── Step 3. VPC Lambda  zip +  ─────────────────────
step "Step 3 · spike-detect · pos-ingestor  "

for FN in spike-detect pos-ingestor; do
  SRC="${LAMBDA_BASE}/${FN}/index.py"
  ZIP_PATH="/tmp/${FN}.zip"
  if [ -f "${SRC}" ]; then
    (cd "${LAMBDA_BASE}/${FN}" && zip -q "${ZIP_PATH}" index.py)
    aws s3 cp "${ZIP_PATH}" "s3://${ARTIFACT_BUCKET}/lambda-code/${FN}.zip" --quiet
    ok "${FN}.zip → S3"

    #  
    aws lambda update-function-code \
      --function-name "${PROJECT}-${FN}" \
      --zip-file "fileb://${ZIP_PATH}" \
      --no-publish \
      --region "${REGION}" \
      --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  ✓ {r[\"FunctionName\"]} updated ({r[\"CodeSize\"]} bytes)')
"
    # Layer 
    aws lambda update-function-configuration \
      --function-name "${PROJECT}-${FN}" \
      --layers "${LAYER_ARN}" \
      --region "${REGION}" \
      --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f'  ✓ {r[\"FunctionName\"]} layer attached')
"
  else
    warn "${SRC} "
  fi
done

# ── Step 4. RAW_BUCKET   ─────────────────────────
step "Step 4 · Lambda   (RAW_BUCKET)"

RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")

for FN in aladin-sync event-sync sns-gen spike-detect; do
  info "${PROJECT}-${FN}  ..."
  aws lambda update-function-configuration \
    --function-name "${PROJECT}-${FN}" \
    --environment "Variables={RAW_BUCKET=${RAW_BUCKET},AWS_REGION=${REGION}}" \
    --region "${REGION}" \
    --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
env = r.get('Environment', {}).get('Variables', {})
print(f'  ✓ {r[\"FunctionName\"]} env: {list(env.keys())}')
" 2>/dev/null || warn "${FN}   "
done

# ── Step 5. Kinesis ESM   ──────────────────────────
step "Step 5 · pos-ingestor Kinesis ESM "

ESM_STATE=$(aws lambda list-event-source-mappings \
  --function-name "${PROJECT}-pos-ingestor" \
  --query 'EventSourceMappings[0].State' \
  --output text 2>/dev/null || echo "NONE")
info "pos-ingestor ESM : ${ESM_STATE}"

if [ "${ESM_STATE}" = "Enabled" ]; then
  ok "Kinesis ESM  "
else
  warn "ESM   · CloudFormation  "
fi

# ── Step 6. spike-detect invoke  ───────────────────────
step "Step 6 · spike-detect invoke (RDS  )"

if $RDS_OK; then
  info "spike-detect  invoke..."
  aws lambda invoke \
    --function-name "${PROJECT}-spike-detect" \
    --payload '{}' \
    --cli-binary-format raw-in-base64-out \
    /tmp/spike_out.json \
    --log-type Tail \
    --query 'LogResult' --output text 2>/dev/null | base64 -d | grep -E "\[spike|ERROR|Traceback" | head -10 || true
  [ -f /tmp/spike_out.json ] && info "Response: $(cat /tmp/spike_out.json)"
else
  warn "RDS  · spike-detect invoke skip"
fi

# ──    ──────────────────────────────────────
step "Day 03  "
cat << 'EOF'
  [ ] Lambda Layer (psycopg2 + redis)  + 
  [ ] spike-detect · pos-ingestor  
  [ ] Lambda Layer spike-detect · pos-ingestor  
  [ ] RAW_BUCKET   Lambda 
  [ ] pos-ingestor Kinesis ESM: Enabled
  [ ] spike-detect invoke →   (RDS  )

  ★ RDS   spike-detect connection error 
  ★ Logs: aws logs tail /aws/lambda/bookflow-spike-detect --follow

(5/1)  : day04_0501_lambda_verify.sh
  →  7 Lambda   + EventBridge   
EOF
