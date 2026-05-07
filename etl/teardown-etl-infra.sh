#!/usr/bin/env bash
# teardown-etl-infra.sh
# BookFlow ETL 인프라 전체 삭제
#
# 삭제 순서:
#   1. cicd-ecs       : CodePipeline + CodeBuild
#   2. sam-app        : Lambda 7개 (SAM 스택)
#   3. base-down      : Tier 10-99 (VPC / RDS / Redis / Kinesis / ECS)
#   4. S3 buckets     : 버킷 비우기 + 삭제
#   5. bookflow-00-s3 : S3 CFN 스택 삭제
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="py ${REPO_ROOT}/scripts/aws/bookflow.py"
REGION="${AWS_REGION:-ap-northeast-1}"
PROJECT="bookflow"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "================================================"
echo " BookFlow ETL Infrastructure Teardown"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo " Account : ${ACCOUNT_ID}"
echo "================================================"
echo ""
echo "  WARNING: BookFlow 리소스 전체를 삭제합니다."
echo "  Tier 00 (KMS/IAM/ECR/Secrets)는 유지됩니다."
echo "  취소하려면 Ctrl+C 를 누르세요. 5초 후 시작..."
sleep 5

# ── 버전 관리 버킷 비우기 + 삭제 함수 ──────────────────────────
delete_bucket() {
  local bucket=$1
  if ! aws s3api head-bucket --bucket "${bucket}" --region "${REGION}" 2>/dev/null; then
    echo "  SKIP s3://${bucket} (없음)"
    return 0
  fi

  echo "  Emptying s3://${bucket} ..."

  # Python으로 버전 + 삭제마커 일괄 제거 (버전 ID 특수문자 파싱 오류 방지)
  py - "${bucket}" "${REGION}" <<'PYEOF'
import boto3, sys
bucket, region = sys.argv[1], sys.argv[2]
s3 = boto3.client("s3", region_name=region)
paginator = s3.get_paginator("list_object_versions")
for page in paginator.paginate(Bucket=bucket):
    objects = [
        {"Key": v["Key"], "VersionId": v["VersionId"]}
        for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
    ]
    if objects:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
PYEOF

  aws s3 rb "s3://${bucket}" --region "${REGION}"
  echo "  OK s3://${bucket} 삭제 완료"
}

# ── CFN 스택 삭제 함수 ───────────────────────────────────────────
delete_stack() {
  local stack=$1
  local status
  status=$(aws cloudformation describe-stacks \
    --stack-name "${stack}" \
    --query "Stacks[0].StackStatus" \
    --output text --region "${REGION}" 2>/dev/null || echo "DOES_NOT_EXIST")

  if [ "${status}" = "DOES_NOT_EXIST" ]; then
    echo "  SKIP ${stack} (없음)"
    return 0
  fi

  echo "  Deleting ${stack} (${status})..."
  aws cloudformation delete-stack --stack-name "${stack}" --region "${REGION}"
  aws cloudformation wait stack-delete-complete \
    --stack-name "${stack}" \
    --region "${REGION}" \
    --no-paginate
  echo "  OK ${stack} 삭제 완료"
}

# ── Step 1: CodePipeline ─────────────────────────────────────────
echo ""
echo "[1/5] CodePipeline (${PROJECT}-cicd-ecs)..."
delete_stack "${PROJECT}-cicd-ecs"

# ── Step 2: SAM Lambda 스택 ──────────────────────────────────────
echo ""
echo "[2/5] SAM Lambda stack (sam-app)..."
delete_stack "sam-app"

# ── Step 3: Tier 10-99 (VPCs / RDS / Redis / Kinesis / ECS) ─────
echo ""
echo "[3/5] Tier 10-99 (base-down)..."
${SCRIPT} base-down
echo "  OK Tier 10-99 삭제 완료"

# ── Step 4: S3 버킷 비우기 + 삭제 ───────────────────────────────
echo ""
echo "[4/5] S3 buckets..."
for BUCKET in \
  "${PROJECT}-raw-${ACCOUNT_ID}" \
  "${PROJECT}-mart-${ACCOUNT_ID}" \
  "${PROJECT}-cp-artifacts-${ACCOUNT_ID}" \
  "${PROJECT}-glue-scripts-${ACCOUNT_ID}" \
  "${PROJECT}-tf-state-${ACCOUNT_ID}"; do
  delete_bucket "${BUCKET}"
done

# SAM 관리 버킷 (aws-sam-cli-managed-default)
SAM_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name aws-sam-cli-managed-default \
  --query "Stacks[0].Outputs[?OutputKey=='SourceBucket'].OutputValue" \
  --output text --region "${REGION}" 2>/dev/null || echo "")
if [ -n "${SAM_BUCKET}" ]; then
  delete_bucket "${SAM_BUCKET}"
  delete_stack "aws-sam-cli-managed-default"
fi

# ── Step 5: bookflow-00-s3 CFN 스택 ─────────────────────────────
echo ""
echo "[5/5] S3 CFN stack (${PROJECT}-00-s3)..."
delete_stack "${PROJECT}-00-s3"

echo ""
echo "================================================"
echo " Teardown Complete"
echo ""
echo " 유지된 Tier 00 스택 (재배포 불필요):"
echo "   ${PROJECT}-00-kms / iam / ecr / secrets"
echo "   ${PROJECT}-00-parameter-store / codestar-connection"
echo ""
echo " 다음 배포:"
echo "   bash etl/deploy-etl-infra.sh"
echo "================================================"
