#!/usr/bin/env bash
# deploy-remaining.sh
#
# start-day.sh 이후 미배포 AWS 인프라를 순서대로 전부 배포한다.
# Idempotent: 이미 배포된 스택은 CFN changeset no-change로 스킵된다.
#
# 배포 순서:
#   [1] 00 ACM (phase0 idempotent)
#   [2] 10/30 direct: route53 · ansible-node · endpoints-ansible · peering-ansible-data
#   [3] 60 TGW + tgw-vpc-routes  (etl-streaming 전에 필요 — peering 중복 방지)
#   [4] 50 NAT Gateway
#   [5] ECS sim 이미지 빌드/푸시 + task etl-streaming
#   [6] task publisher  (ALB · WAF · publisher-asg · ecs-inventory-api)
#   [7] task client-vpn + task glue  (catalog · step-functions)
#   [8] SAM Lambdas (sam build → sam deploy) + Glue scripts S3 sync
#
# 의도적 스킵:
#   bookflow-10-customer-gateway / bookflow-60-vpn-site-to-site
#     → 실제 Azure/GCP VPN 공인 IP 필요
#     → 준비되면: BOOKFLOW_AZURE_VPN_GW_IP=<ip> BOOKFLOW_GCP_VPN_GW_IP=<ip> ./deploy-remaining.sh
#   bookflow-00-cloudtrail / bookflow-00-cloudwatch  → 선택적 감사/모니터링
#
# Prereq: AWS CLI · Python · Docker · SAM CLI · helm · kubectl
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

REGION="${AWS_REGION:-ap-northeast-1}"
PROJECT="bookflow"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "=== deploy-remaining ==="
echo "Root    : $PROJECT_ROOT"
echo "Account : $ACCOUNT_ID  Region : $REGION"
echo

# ─────────────────────────────────────────────────────────────
# 1. Tier 00 · Foundation (ACM)
#    나머지 00 스택(kms/iam/s3/ecr/secrets/parameter-store/codestar)은 이미 배포됨
# ─────────────────────────────────────────────────────────────
echo "[1/8] Tier 00 · phase0 (acm + idempotent for existing stacks)"
py scripts/aws/bookflow.py phase0
echo

# ─────────────────────────────────────────────────────────────
# 2. Tier 10/30 · 태스크 모듈에 없는 직접 스택
#    route53 · ansible-node · endpoints-ansible · peering-ansible-data
# ─────────────────────────────────────────────────────────────
echo "[2/8] Tier 10/30 direct stacks"
py - <<'PYEOF'
import sys
sys.path.insert(0, '.')
from scripts.aws.lib import Stack
Stack('10', 'route53',              '10-network-core/route53.yaml').deploy()
Stack('30', 'ansible-node',         '30-compute-cluster/ansible-node.yaml').deploy()
Stack('10', 'endpoints-ansible',    '10-network-core/endpoints/endpoints-ansible.yaml').deploy()
Stack('10', 'peering-ansible-data', '10-network-core/peering/ansible-data.yaml').deploy()
PYEOF
echo

# ─────────────────────────────────────────────────────────────
# 3. Tier 60 · Transit Gateway + VPC 라우트 테이블 엔트리
#    tgw-vpc-routes 가 활성화돼야 etl-streaming에서
#    peering-sales-data-egress 가 자동 스킵된다 (중복 라우트 충돌 방지)
# ─────────────────────────────────────────────────────────────
echo "[3/8] Tier 60 · TGW + tgw-vpc-routes"
py - <<'PYEOF'
import sys
sys.path.insert(0, '.')
from scripts.aws.lib import Stack
Stack('60', 'tgw',            '60-network-cross-cloud/tgw.yaml').deploy()
Stack('60', 'tgw-vpc-routes', '60-network-cross-cloud/tgw-vpc-routes.yaml').deploy()
PYEOF
echo

# ─────────────────────────────────────────────────────────────
# 4. Tier 50 · NAT Gateway
# ─────────────────────────────────────────────────────────────
echo "[4/8] Tier 50 · NAT Gateway"
py - <<'PYEOF'
import sys
sys.path.insert(0, '.')
from scripts.aws.lib import Stack
Stack('50', 'nat-gateway', '50-network-traffic/nat-gateway.yaml').deploy()
PYEOF
echo

# ─────────────────────────────────────────────────────────────
# 5. ECS sim 이미지 빌드/푸시 + task etl-streaming
#    endpoints-sales-data · ecs-online-sim · ecs-offline-sim
#    (peering-sales-data-egress: tgw-vpc-routes 활성 → 자동 스킵)
# ─────────────────────────────────────────────────────────────
echo "[5/8] ECS sim images + task etl-streaming"

aws ecr get-login-password --region "${REGION}" | \
    docker login --username AWS --password-stdin "${ECR}"

for SIM in online-sim offline-sim; do
    IMG="${ECR}/${PROJECT}/${SIM}:latest"
    echo "  build/push ${SIM}..."
    docker build -t "${IMG}" "${PROJECT_ROOT}/ecs-sims/${SIM}"
    docker push "${IMG}"
done

py scripts/aws/bookflow.py task etl-streaming
echo

# ─────────────────────────────────────────────────────────────
# 6. task publisher
#    peering-egress-data · alb-external · waf · publisher-asg · ecs-inventory-api
# ─────────────────────────────────────────────────────────────
echo "[6/8] task publisher"
py scripts/aws/bookflow.py task publisher
echo

# ─────────────────────────────────────────────────────────────
# 7. task client-vpn + task glue
#    client-vpn · glue-catalog · step-functions
# ─────────────────────────────────────────────────────────────
echo "[7/8] task client-vpn + task glue"
py scripts/aws/bookflow.py task client-vpn
py scripts/aws/bookflow.py task glue
echo

# ─────────────────────────────────────────────────────────────
# 8. SAM Lambdas + Glue scripts S3 sync
#    sam build → sam deploy (bookflow-99-lambdas)
#    glue-jobs/ → s3://bookflow-glue-scripts-ACCOUNT/scripts/
# ─────────────────────────────────────────────────────────────
echo "[8/8] SAM Lambdas + Glue scripts sync"

ARTIFACT_BUCKET="${PROJECT}-cp-artifacts-${ACCOUNT_ID}"
GLUE_BUCKET="${PROJECT}-glue-scripts-${ACCOUNT_ID}"
GCS_STAGING="${GCS_STAGING_BUCKET:-project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging}"

SF_ARN=$(aws cloudformation describe-stacks \
    --stack-name "${PROJECT}-99-step-functions" \
    --query "Stacks[0].Outputs[?OutputKey=='Etl3StateMachineArn'].OutputValue" \
    --output text 2>/dev/null || echo "")

SAM_PARAMS="ProjectName=${PROJECT} GcsStagingBucket=${GCS_STAGING}"
[[ -n "${SF_ARN}" ]] && SAM_PARAMS="${SAM_PARAMS} StepFunctionsArn=${SF_ARN}"

echo "  SAM params: ${SAM_PARAMS}"

cd "${PROJECT_ROOT}/infra/aws/99-serverless"
sam build -t sam-template.yaml
sam deploy \
    --stack-name "${PROJECT}-99-lambdas" \
    --s3-bucket "${ARTIFACT_BUCKET}" \
    --s3-prefix "lambda-packages" \
    --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_IAM \
    --no-fail-on-empty-changeset \
    --region "${REGION}" \
    --parameter-overrides ${SAM_PARAMS}

cd "${PROJECT_ROOT}"

echo "  Glue scripts sync → s3://${GLUE_BUCKET}/scripts/"
aws s3 sync "${PROJECT_ROOT}/glue-jobs/" "s3://${GLUE_BUCKET}/scripts/" \
    --region "${REGION}" \
    --exclude "*.pyc" \
    --exclude "__pycache__/*"

# ─────────────────────────────────────────────────────────────
echo
echo "=== deploy-remaining complete ==="
echo
echo "배포된 스택:"
echo "  00: acm"
echo "  10: route53 · endpoints-ansible · peering-ansible-data"
echo "  30: ansible-node"
echo "  60: tgw · tgw-vpc-routes"
echo "  50: nat-gateway"
echo "  10/40: endpoints-sales-data · ecs-online-sim · ecs-offline-sim"
echo "  10/40/50: peering-egress-data · alb-external · waf · publisher-asg · ecs-inventory-api"
echo "  60: client-vpn"
echo "  99: glue-catalog · step-functions · lambdas"
echo
echo "의도적 스킵:"
echo "  customer-gateway / vpn-site-to-site  → VPN 공인 IP 설정 후 task auth-pod / task forecast 실행"
echo "  cloudtrail / cloudwatch              → 필요 시 phase0 STACKS_OPTIONAL 참고"
echo
echo "다음 단계:"
echo "  py scripts/aws/bookflow.py status"
echo "  kubectl get pods -A"
