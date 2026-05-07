#!/usr/bin/env bash
# deploy-etl-infra.sh
# One-shot ETL full deploy: s3 -> base-up -> task-data -> task-etl-streaming -> ETL3(SAM+Glue+SF)
#
# Deploy order:
#   0. s3                 : S3 버킷 5개 생성
#   1. base-up            : Tier 10 (VPC 4개) + Tier 30 (ECS cluster)
#   2. task-data          : Tier 20 (RDS + Redis + Kinesis)
#   3. ECR images         : online-sim / offline-sim build & push
#   4. task-etl-streaming : Tier 10 VPC endpoints + Tier 40 ECS sims
#   5. SAM Lambda         : Lambda 8개 + EventBridge cron(13:10 KST) + Kinesis ESM + API GW
#   6. Glue Catalog       : bookflow-99-glue-catalog + Step Functions ETL3
#   7. Glue scripts       : S3 sync (pos_etl / aladin_etl / event_etl / sns_agg / sales_daily_agg / features_build)
#   8. Initial collect    : aladin-sync / event-sync / sns-gen Lambda 즉시 invoke
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="py ${REPO_ROOT}/scripts/aws/bookflow.py"
REGION="${AWS_REGION:-ap-northeast-1}"
PROJECT="bookflow"

# GCS staging 버킷명 (GCP Terraform output: staging_bucket_name)
# 값이 다르면 환경변수로 주입: GCS_STAGING_BUCKET=xxx bash deploy-etl-infra.sh
GCP_PROJECT_ID="${GCP_PROJECT_ID:-project-8ab6bf05-54d2-4f5d-b8d}"
GCS_STAGING_BUCKET="${GCS_STAGING_BUCKET:-${GCP_PROJECT_ID}-bookflow-staging}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT_ID}"
ARTIFACTS_BUCKET="${PROJECT}-cp-artifacts-${ACCOUNT_ID}"

echo "================================================"
echo " BookFlow ETL Full Deploy (ETL1 + ETL2 + ETL3)"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo " Account : ${ACCOUNT_ID}"
echo " ECR     : ${ECR_REGISTRY}"
echo " GCS     : gs://${GCS_STAGING_BUCKET}"
echo "================================================"

# ── Step 0: S3 버킷 ──────────────────────────────────
echo ""
echo "[0/8] S3 buckets (create if missing)..."
for BUCKET in \
  "${PROJECT}-raw-${ACCOUNT_ID}" \
  "${PROJECT}-mart-${ACCOUNT_ID}" \
  "${PROJECT}-cp-artifacts-${ACCOUNT_ID}" \
  "${PROJECT}-glue-scripts-${ACCOUNT_ID}" \
  "${PROJECT}-tf-state-${ACCOUNT_ID}"; do

  if aws s3api head-bucket --bucket "${BUCKET}" --region "${REGION}" 2>/dev/null; then
    echo "  OK s3://${BUCKET} exists"
  else
    echo "  Creating s3://${BUCKET}..."
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration LocationConstraint="${REGION}"
    aws s3api put-public-access-block \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --public-access-block-configuration \
      '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}'
    aws s3api put-bucket-versioning \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --versioning-configuration Status=Enabled
    echo "  OK s3://${BUCKET} created"
  fi
done

# ── Step 1: base-up ──────────────────────────────────
echo ""
echo "[1/8] base-up (Tier 10 VPCs + Tier 30 ECS cluster)..."
${SCRIPT} base-up
echo "  OK base-up complete"

# ── Step 2: task-data ────────────────────────────────
echo ""
echo "[2/8] task-data (RDS + Redis + Kinesis)..."
${SCRIPT} task data
echo "  OK task-data complete"

# ── Step 3: ECR 이미지 확인 / 없으면 CodeBuild로 빌드 ─
echo ""
echo "[3/8] ECR image check (online-sim / offline-sim)..."

ensure_ecr_image() {
  local SIM="$1"
  local IMAGE_REPO="${PROJECT}/${SIM}"
  local CB_ROLE_NAME="${PROJECT}-codebuild-role"
  local CB_ROLE="arn:aws:iam::${ACCOUNT_ID}:role/${CB_ROLE_NAME}"
  local CB_PROJECT="${PROJECT}-ecr-build-${SIM}"

  # ECR에 latest 이미지가 있으면 스킵
  local IMAGE_DIGEST
  IMAGE_DIGEST=$(aws ecr describe-images \
    --repository-name "${IMAGE_REPO}" \
    --image-ids imageTag=latest \
    --region "${REGION}" \
    --query "imageDetails[0].imageDigest" \
    --output text 2>/dev/null || echo "")
  if [ -n "${IMAGE_DIGEST}" ] && [ "${IMAGE_DIGEST}" != "None" ]; then
    echo "  SKIP ${IMAGE_REPO}:latest 이미 존재 (${IMAGE_DIGEST:0:19}...)"
    return 0
  fi

  echo "  이미지 없음 → CodeBuild 빌드 시작: ${SIM}"

  # ECR 리포지토리 없으면 생성
  aws ecr describe-repositories \
    --repository-names "${IMAGE_REPO}" \
    --region "${REGION}" 2>/dev/null || \
  aws ecr create-repository \
    --repository-name "${IMAGE_REPO}" \
    --region "${REGION}" --output json > /dev/null

  # CodeBuild 서비스 역할 없으면 생성
  if ! aws iam get-role --role-name "${CB_ROLE_NAME}" 2>/dev/null | grep -q "RoleName"; then
    aws iam create-role --role-name "${CB_ROLE_NAME}" \
      --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"codebuild.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      --output json > /dev/null
    aws iam attach-role-policy --role-name "${CB_ROLE_NAME}" \
      --policy-arn "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
    aws iam attach-role-policy --role-name "${CB_ROLE_NAME}" \
      --policy-arn "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
  fi
  aws iam put-role-policy --role-name "${CB_ROLE_NAME}" \
    --policy-name "S3ArtifactsAccess" \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:GetObjectVersion\",\"s3:PutObject\",\"s3:GetBucketVersioning\",\"s3:GetBucketAcl\",\"s3:GetBucketLocation\"],\"Resource\":[\"arn:aws:s3:::${ARTIFACTS_BUCKET}\",\"arn:aws:s3:::${ARTIFACTS_BUCKET}/*\"]}]}"

  # buildspec.yml 포함 zip 생성 → S3
  py -c "
import zipfile, os, sys
src, dst, account_id, region, image_repo = sys.argv[1:]
buildspec = '''version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region {r} | docker login --username AWS --password-stdin {a}.dkr.ecr.{r}.amazonaws.com
  build:
    commands:
      - docker build -t {i}:latest .
      - docker tag {i}:latest {a}.dkr.ecr.{r}.amazonaws.com/{i}:latest
  post_build:
    commands:
      - docker push {a}.dkr.ecr.{r}.amazonaws.com/{i}:latest
'''.format(r=region, a=account_id, i=image_repo)
with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        for f in files:
            fp = os.path.join(root, f)
            zf.write(fp, os.path.relpath(fp, src))
    zf.writestr('buildspec.yml', buildspec)
" "${REPO_ROOT}/ecs-sims/${SIM}" "/tmp/${SIM}.zip" "${ACCOUNT_ID}" "${REGION}" "${IMAGE_REPO}"
  aws s3 cp "/tmp/${SIM}.zip" \
    "s3://${ARTIFACTS_BUCKET}/codebuild/${SIM}.zip" --region "${REGION}"

  # CodeBuild 프로젝트 없으면 생성
  if aws codebuild batch-get-projects --names "${CB_PROJECT}" \
      --region "${REGION}" \
      --query "projectsNotFound[0]" --output text 2>/dev/null | grep -q "${CB_PROJECT}"; then
    aws codebuild create-project \
      --name "${CB_PROJECT}" \
      --source "type=S3,location=${ARTIFACTS_BUCKET}/codebuild/${SIM}.zip" \
      --artifacts "type=NO_ARTIFACTS" \
      --environment "type=LINUX_CONTAINER,image=aws/codebuild/standard:7.0,computeType=BUILD_GENERAL1_SMALL,privilegedMode=true" \
      --service-role "${CB_ROLE}" \
      --region "${REGION}" --output json > /dev/null
  fi

  local BUILD_ID
  BUILD_ID=$(aws codebuild start-build \
    --project-name "${CB_PROJECT}" \
    --region "${REGION}" --query 'build.id' --output text)
  echo "  빌드 ID: ${BUILD_ID}"

  local ELAPSED=0
  while [ $ELAPSED -lt 900 ]; do
    local STATUS
    STATUS=$(aws codebuild batch-get-builds --ids "${BUILD_ID}" \
      --query 'builds[0].buildStatus' --output text --region "${REGION}")
    case "${STATUS}" in
      SUCCEEDED) echo "  OK ${SIM} → ECR push 완료"; return 0 ;;
      FAILED|FAULT|STOPPED|TIMED_OUT)
        echo "  ERROR ${SIM} 빌드 실패: ${STATUS}"; return 1 ;;
      *) echo "  ... 빌드 중 (${STATUS}, ${ELAPSED}s)"; sleep 20; ELAPSED=$((ELAPSED+20)) ;;
    esac
  done
  echo "  TIMEOUT ${SIM}"; return 1
}

for SIM in online-sim offline-sim; do
  ensure_ecr_image "${SIM}"
done

# ── Step 4: task-etl-streaming ───────────────────────
echo ""
echo "[4/8] task-etl-streaming (VPC endpoints + ECS online/offline-sim)..."

${SCRIPT} task etl-streaming
echo "  OK task-etl-streaming complete"

# ── ETL3 시작 ────────────────────────────────────────

# ── Step 5: SAM Lambda build + deploy ────────────────
echo ""
echo "[5/8] SAM Lambda build + deploy (ETL3 · Lambda 8개)..."

LAMBDA_DIR="${REPO_ROOT}/infra/aws/99-serverless"

# Step Functions ARN 주입 (bookflow-99-step-functions 스택이 있으면 자동 연결)
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

sam build -t sam-template.yaml
sam deploy \
  --stack-name "${PROJECT}-99-lambdas" \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_IAM \
  --region "${REGION}" \
  --parameter-overrides ${SAM_PARAMS} \
  --no-fail-on-empty-changeset

cd "${REPO_ROOT}"
echo "  OK SAM deploy complete (Lambda 8개 · EventBridge cron 13:10 KST 등록)"

# ── Step 6: Glue Catalog + Step Functions ────────────
echo ""
echo "[6/8] Glue Catalog + Step Functions (bookflow-99-glue-catalog)..."
${SCRIPT} task glue
echo "  OK Glue Catalog + Step Functions deployed"

# ── Step 7: Glue 스크립트 S3 sync ────────────────────
echo ""
echo "[7/8] Glue scripts S3 sync..."
GLUE_JOBS_DIR="${REPO_ROOT}/glue-jobs"
aws s3 sync "${GLUE_JOBS_DIR}/" "s3://${GLUE_BUCKET}/scripts/" \
  --region "${REGION}" \
  --exclude "*.pyc" \
  --exclude "__pycache__/*"
echo "  OK Glue scripts → s3://${GLUE_BUCKET}/scripts/"
aws s3 ls "s3://${GLUE_BUCKET}/scripts/"

# ── Step 8: 초기 데이터 수집 (Lambda invoke) ──────────
echo ""
echo "[8/8] Initial data collection (Lambda invoke)..."
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
echo "================================================"
echo " Deploy Complete (ETL1 + ETL2 + ETL3)"
echo " - Tier 10 : VPC 4개 + VPC endpoints"
echo " - Tier 20 : RDS / Redis / Kinesis"
echo " - Tier 30 : ECS cluster (bookflow-ecs)"
echo " - Tier 40 : ECS online-sim / offline-sim"
echo " - Lambda  : 8개 (cron 13:10 KST 등록)"
echo " - ETL3    : Glue 6 Jobs + Step Functions"
echo " - Data    : aladin-sync / event-sync / sns-gen 초기 수집 완료"
echo ""
echo " Next: Glue Raw Jobs 실행 (S3 Raw → Mart Parquet 변환)"
echo "   bash scripts/aws/daily/day06_0505_glue_raw.sh   # Raw 4 Jobs"
echo "   bash scripts/aws/daily/day07_0506_glue_agg.sh   # Agg 2 Jobs + ETL3 Step Functions"
echo ""
echo " Verify:"
echo "   py scripts/aws/bookflow.py status"
echo "   aws glue list-jobs --region ${REGION}"
echo "   aws ecs describe-services --cluster bookflow-ecs \\"
echo "     --services online-sim offline-sim \\"
echo "     --region ${REGION} \\"
echo "     --query 'services[*].{name:serviceName,running:runningCount,status:status}'"
echo "================================================"
