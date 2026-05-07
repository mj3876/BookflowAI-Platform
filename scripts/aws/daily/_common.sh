#!/usr/bin/env bash
# _common.sh ·   source     
# : source "$(dirname "$0")/_common.sh"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REGION="${AWS_REGION:-ap-northeast-1}"
PROJECT="bookflow"

step()  { echo ""; echo "══════════════════════════════════"; echo "▶ $*"; echo "══════════════════════════════════"; }
ok()    { echo "  ✓ $*"; }
warn()  { echo "  ⚠ $*"; }
err()   { echo "  ✗ $*"; exit 1; }
info()  { echo "  · $*"; }

# AWS  ID  ()
account_id() {
  aws sts get-caller-identity --query Account --output text
}

# CloudFormation   
stack_exists() {
  local stack_name="$1"
  aws cloudformation describe-stacks --stack-name "${stack_name}" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null | grep -qv "^None$"
}

# CloudFormation Output  
stack_output() {
  local stack_name="$1"
  local output_key="$2"
  aws cloudformation describe-stacks --stack-name "${stack_name}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
    --output text 2>/dev/null
}

# Lambda invoke +  
lambda_invoke() {
  local fn_name="${PROJECT}-$1"
  local payload="${2:-{}}"
  local out="/tmp/bookflow_lambda_out.json"
  info "invoke ${fn_name}..."
  aws lambda invoke --function-name "${fn_name}" \
    --payload "${payload}" \
    --cli-binary-format raw-in-base64-out \
    "${out}" --log-type Tail --query 'LogResult' --output text 2>/dev/null | base64 -d | tail -5 || true
  echo "  Response: $(cat ${out})"
}

# ECR 
ecr_login() {
  local account
  account=$(account_id)
  local registry="${account}.dkr.ecr.${REGION}.amazonaws.com"
  aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin "${registry}" > /dev/null 2>&1
  echo "${registry}"
}

# Docker  + ECR push
build_push_image() {
  local service="$1"      # e.g. online-simul
  local context_dir="$2"  # e.g. $REPO_ROOT/ecs-sims/online-simul
  local registry
  registry=$(ecr_login)
  local image="${registry}/${PROJECT}/${service}:latest"
  info "build ${service}..."
  docker build -t "${image}" "${context_dir}" --quiet
  docker push "${image}" --quiet
  ok "${image} pushed"
}

# bookflow.py  
bookflow() {
  python "${REPO_ROOT}/scripts/aws/bookflow.py" "$@"
}

#   
check_env() {
  step " "
  command -v aws   > /dev/null || err "AWS CLI "
  command -v python > /dev/null || err "Python "

  local caller
  caller=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null) || \
    err "AWS   · aws configure   "
  ok "AWS : ${caller}"
  ok "Region: ${REGION}"
}
