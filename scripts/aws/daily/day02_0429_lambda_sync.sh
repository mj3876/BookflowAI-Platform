#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 02 · 4/29 ()  Lambda SAM   ( 3)           ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1. SAM template 7 Lambda    (placeholder ) ║
# ║  2. aladin-sync · event-sync · sns-gen  zip        ║
# ║  3. 3 Lambda  invoke                               ║
# ║  4. S3 Raw                                ║
# ║   Lambda: VPC  (aladin-sync · event-sync · sns-gen)  ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

ACCOUNT=$(account_id)

# ── Step 1. SAM CLI   ─────────────────────────────────
step "Step 1 · SAM CLI "
command -v sam > /dev/null || err "SAM CLI  · pip install aws-sam-cli"
ok "SAM CLI: $(sam --version)"

# ── Step 2. Lambda    ──────────────────────────
step "Step 2 · Lambda    (S3 )"

RAW_BUCKET=$(stack_output "bookflow-00-s3" "RawBucketName" 2>/dev/null || \
             echo "${PROJECT}-raw-${ACCOUNT}")
ARTIFACT_BUCKET="${PROJECT}-cp-artifacts-${ACCOUNT}"

LAMBDA_BASE="${REPO_ROOT}/infra/aws/99-serverless/lambdas"

#  Lambda zip  S3 
for FN in aladin-sync event-sync sns-gen; do
  SRC="${LAMBDA_BASE}/${FN}/index.py"
  ZIP_PATH="/tmp/${FN}.zip"
  if [ -f "${SRC}" ]; then
    (cd "${LAMBDA_BASE}/${FN}" && zip -q "${ZIP_PATH}" index.py)
    aws s3 cp "${ZIP_PATH}" "s3://${ARTIFACT_BUCKET}/lambda-code/${FN}.zip" --quiet
    ok "${FN}.zip → s3://${ARTIFACT_BUCKET}/lambda-code/"
  else
    warn "${SRC}  ·   "
  fi
done

# ── Step 3. SAM   (placeholder ) ────────────────────
step "Step 3 · SAM  (bookflow-99-lambdas )"

cd "${REPO_ROOT}"

# Step Functions ARN (glue stack  )
SF_ARN=$(stack_output "bookflow-99-step-functions" "Etl3StateMachineArn" 2>/dev/null || echo "")

SAM_PARAMS="ParameterKey=ProjectName,ParameterValue=${PROJECT}"
if [ -n "${SF_ARN}" ]; then
  SAM_PARAMS="${SAM_PARAMS} ParameterKey=StepFunctionsArn,ParameterValue=${SF_ARN}"
fi

if ! stack_exists "bookflow-99-lambdas"; then
  info "   (CREATE)..."
else
  info "  (UPDATE)..."
fi

bookflow task lambdas
ok "bookflow-99-lambdas  "

# ── Step 4.  3 Lambda    ─────────────────
step "Step 4 · Lambda    (zip)"

for FN in aladin-sync event-sync sns-gen; do
  ZIP_PATH="/tmp/${FN}.zip"
  if [ -f "${ZIP_PATH}" ]; then
    info "${PROJECT}-${FN}  ..."
    aws lambda update-function-code \
      --function-name "${PROJECT}-${FN}" \
      --zip-file "fileb://${ZIP_PATH}" \
      --no-publish \
      --region "${REGION}" \
      --output json | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(f\"  ✓ {r['FunctionName']} → {r['CodeSize']} bytes\")
"
  fi
done

# ── Step 5. 3 Lambda  invoke  ────────────────────────
step "Step 5 · Lambda invoke "

# aladin-sync 
info "aladin-sync invoke..."
OUT=$(aws lambda invoke \
  --function-name "${PROJECT}-aladin-sync" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/aladin_out.json \
  --query 'StatusCode' --output text 2>/dev/null || echo "ERROR")
info "aladin-sync StatusCode: ${OUT}"
[ -f /tmp/aladin_out.json ] && info "Response: $(cat /tmp/aladin_out.json | head -c 200)"

# event-sync 
info "event-sync invoke..."
OUT=$(aws lambda invoke \
  --function-name "${PROJECT}-event-sync" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/event_out.json \
  --query 'StatusCode' --output text 2>/dev/null || echo "ERROR")
info "event-sync StatusCode: ${OUT}"
[ -f /tmp/event_out.json ] && info "Response: $(cat /tmp/event_out.json | head -c 200)"

# sns-gen 
info "sns-gen invoke..."
OUT=$(aws lambda invoke \
  --function-name "${PROJECT}-sns-gen" \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/sns_out.json \
  --query 'StatusCode' --output text 2>/dev/null || echo "ERROR")
info "sns-gen StatusCode: ${OUT}"
[ -f /tmp/sns_out.json ] && info "Response: $(cat /tmp/sns_out.json | head -c 200)"

# ── Step 6. S3 Raw    ────────────────────────────
step "Step 6 · S3 Raw  "

TODAY=$(date +%Y/%-m/%-d)
info "aladin/  :"
aws s3 ls "s3://${RAW_BUCKET}/aladin/" --recursive | tail -3 || warn "aladin   (invoke   Secrets )"
info "events/  :"
aws s3 ls "s3://${RAW_BUCKET}/events/" --recursive | tail -3 || warn "events  "
info "sns/  :"
aws s3 ls "s3://${RAW_BUCKET}/sns/" --recursive | tail -3 || warn "sns  "

# ──    ──────────────────────────────────────
step "Day 02  "
cat << 'EOF'
  [ ] SAM Lambda   (bookflow-99-lambdas CREATE)
  [ ] aladin-sync invoke → S3 aladin/  
  [ ] event-sync invoke → S3 events/  
  [ ] sns-gen invoke → S3 sns/  
  [ ] CloudWatch Logs : /aws/lambda/bookflow-aladin-sync

  ★   :
    aws logs tail /aws/lambda/bookflow-aladin-sync --follow
    aws secretsmanager describe-secret --secret-id bookflow/aladin/ttbkey

(4/30)  : day03_0430_lambda_vpc.sh
  → spike-detect · pos-ingestor (VPC Lambda) 
EOF
