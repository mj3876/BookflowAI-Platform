#!/usr/bin/env bash
# Daily 09:00 boot · Tier 10/20/30 base + EKS + ECS sims + mocks helm.
#
# Idempotent: safe to re-run. Each step uses CFN deploy (no-op if unchanged) or
# helm upgrade --install (no-op if already installed).
#
# Env:
#   AWS_PROFILE=bookflow-admin (required · or `aws configure --profile`)
#   AWS_REGION=ap-northeast-1  (default)
#   BOOKFLOW_APPS_DIR=../BookFlowAI-Apps (default · for mocks chart)
#
# CI/CD: when CodeStar is available (weekday), wrap this script in a CodeBuild
# project triggered by EventBridge cron 09:00 KST. helm step stays unchanged.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== 09:00 BOOKFLOW start-day ==="
echo "Project root: $PROJECT_ROOT"
echo "AWS profile: ${AWS_PROFILE:-default} · region: ${AWS_REGION:-ap-northeast-1}"
echo

# 1. Tier 10 (VPC) + Tier 30 base (ECS cluster)
py scripts/aws/bookflow.py base-up

# 2. Tier 20 data (RDS · Redis · Kinesis) + ansible-data peering
py scripts/aws/bookflow.py task data

# 3. EKS cluster + IRSA + node group + addons + bookflow-ai endpoints/peering
py scripts/aws/bookflow.py task msa-pods

# 4. CSP mocks helm install (Phase 1-3 dev · swap to real CSP later via env)
py scripts/aws/bookflow.py task mocks

echo
echo "=== start-day complete ==="
echo "Verify:"
echo "  py scripts/aws/bookflow.py status"
echo "  kubectl get pods -A"
