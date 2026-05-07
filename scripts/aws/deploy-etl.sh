#!/usr/bin/env bash
# deploy-etl.sh - BookFlow ETL full pipeline deploy
# Order: ECR image build/push -> SAM Lambda build+deploy -> Glue scripts S3 sync
# Prerequisites: AWS CLI configured, Docker running, SAM CLI installed
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-1}"
PROJECT="bookflow"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# GCS staging bucket: Terraform gcs.tf google_storage_bucket.staging.name
# Override with env var if needed: GCS_STAGING_BUCKET=xxx bash deploy-etl.sh
GCP_PROJECT_ID="${GCP_PROJECT_ID:-project-8ab6bf05-54d2-4f5d-b8d}"
GCS_STAGING_BUCKET="${GCS_STAGING_BUCKET:-${GCP_PROJECT_ID}-bookflow-staging}"

# Get AWS account info
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT_ID}"
RAW_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-00-s3" \
  --query "Stacks[0].Outputs[?OutputKey=='RawBucketName'].OutputValue" \
  --output text 2>/dev/null || echo "${PROJECT}-raw-${ACCOUNT_ID}")

echo "============================================"
echo " BookFlow ETL Deploy"
echo " Account : ${ACCOUNT_ID}"
echo " Region  : ${REGION}"
echo " ECR     : ${ECR_REGISTRY}"
echo " Glue S3 : s3://${GLUE_BUCKET}/scripts/"
echo " GCS     : gs://${GCS_STAGING_BUCKET}"
echo "============================================"

# 1. ECR login
echo ""
echo "[1/5] ECR login..."
aws ecr get-login-password --region "${REGION}" | \
  docker login --username AWS --password-stdin "${ECR_REGISTRY}"

# 2. ECS simulator image build & push
echo ""
echo "[2/5] ECS simulator image build..."

for SIM in online-sim offline-sim; do
  SIM_DIR="${REPO_ROOT}/ecs-sims/${SIM}"
  IMAGE="${ECR_REGISTRY}/${PROJECT}/${SIM}:latest"

  echo "  -> ${SIM} build..."
  docker build -t "${IMAGE}" "${SIM_DIR}"
  docker push "${IMAGE}"
  echo "  OK ${IMAGE} pushed"
done

# 3. ECS service rolling update
echo ""
echo "[3/5] ECS service rolling update..."

ECS_CLUSTER=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-30-ecs-cluster" \
  --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" \
  --output text 2>/dev/null || echo "${PROJECT}-ecs")

for SIM in online-sim offline-sim; do
  echo "  -> ${SIM} force-new-deployment..."
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${SIM}" \
    --force-new-deployment \
    --region "${REGION}" \
    --output json | python3 -c "
import sys, json
s = json.load(sys.stdin)['service']
print(f\"  OK {s['serviceName']} -> {s['desiredCount']} tasks\")
" || echo "  WARN ${SIM} service update failed (service may not be deployed yet)"
done

# 4. Lambda SAM build + deploy
echo ""
echo "[4/5] Lambda SAM build + deploy..."

LAMBDA_DIR="${REPO_ROOT}/infra/aws/99-serverless"
SAM_TEMPLATE="${LAMBDA_DIR}/sam-template.yaml"

# Get Step Functions ARN (if glue stack is deployed)
SF_ARN=$(aws cloudformation describe-stacks \
  --stack-name "${PROJECT}-99-step-functions" \
  --query "Stacks[0].Outputs[?OutputKey=='Etl3StateMachineArn'].OutputValue" \
  --output text 2>/dev/null || echo "")

cd "${LAMBDA_DIR}"

SAM_PARAMS="ProjectName=${PROJECT} GcsStagingBucket=${GCS_STAGING_BUCKET}"
if [ -n "${SF_ARN}" ]; then
  SAM_PARAMS="${SAM_PARAMS} StepFunctionsArn=${SF_ARN}"
  echo "  Step Functions ARN: ${SF_ARN}"
fi

# sam build: lambdas/*/requirements.txt 패키징 (google-cloud-storage 등 외부 라이브러리 포함)
echo "  Building Lambda packages..."
sam build -t sam-template.yaml

# sam deploy: 빌드된 패키지 + GcsStagingBucket 파라미터 전달
sam deploy \
  --stack-name "${PROJECT}-99-lambdas" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_IAM \
  --region "${REGION}" \
  --parameter-overrides ${SAM_PARAMS} \
  --no-fail-on-empty-changeset

echo "  OK Lambda build + deploy complete"

# 5. Glue scripts S3 sync
echo ""
echo "[5/5] Glue scripts S3 sync..."

GLUE_JOBS_DIR="${REPO_ROOT}/glue-jobs"
aws s3 sync "${GLUE_JOBS_DIR}/" "s3://${GLUE_BUCKET}/scripts/" \
  --region "${REGION}" \
  --exclude "*.pyc" \
  --exclude "__pycache__/*"

echo "  OK Glue scripts synced"

# 6. Initial data collection (Lambda invoke)
# cron 대기 없이 배포 직후 즉시 Raw 데이터 수집 → 이후 Glue Job 실행 가능 상태 확보
echo ""
echo "[6/6] Initial data collection (Lambda invoke)..."

for FN in aladin-sync event-sync sns-gen; do
  echo "  -> ${PROJECT}-${FN} invoke..."
  STATUS=$(aws lambda invoke \
    --function-name "${PROJECT}-${FN}" \
    --payload '{}' \
    --cli-binary-format raw-in-base64-out \
    --region "${REGION}" \
    /tmp/${FN}_out.json \
    --query 'StatusCode' --output text 2>/dev/null || echo "ERROR")

  if [ "${STATUS}" = "200" ]; then
    echo "  OK ${FN} → StatusCode 200"
  else
    echo "  WARN ${FN} StatusCode: ${STATUS} (Secrets 미설정 시 정상 · CloudWatch 확인)"
  fi
done

echo ""
echo "============================================"
echo " ETL Deploy Complete"
echo " ECS sims  : online-sim / offline-sim"
echo " Lambdas   : 8 (aladin-sync / event-sync / sns-gen"
echo "             spike-detect / forecast-trigger"
echo "             secret-forwarder / pos-ingestor / mart-to-gcs)"
echo " Glue      : s3://${GLUE_BUCKET}/scripts/"
echo "             (6 jobs: raw_pos/sns/aladin/event / sales_daily / features)"
echo " GCS       : gs://${GCS_STAGING_BUCKET}"
echo ""
echo " Next steps:"
echo "   1. Check ECS tasks: aws ecs list-tasks --cluster ${ECS_CLUSTER}"
echo "   2. Run Glue jobs:   bash scripts/aws/daily/day06_0505_glue_raw.sh"
echo "   3. Check BigQuery:  bq query --use_legacy_sql=false 'SELECT COUNT(*) FROM bookflow_dw.sales_fact'"
echo "   4. CloudWatch Logs: /aws/lambda/${PROJECT}-* / /aws-glue/jobs/"
echo "============================================"
