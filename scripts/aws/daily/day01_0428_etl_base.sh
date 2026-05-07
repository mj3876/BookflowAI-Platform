#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║  Day 01 · 4/28 ()  ETL    + ECS Sim      ║
# ╠══════════════════════════════════════════════════════════════╣
# ║  :                                                       ║
# ║  1.  (VPC · Kinesis · ECS Cluster)            ║
# ║  2. ECS Sim Docker   + ECR Push                    ║
# ║  3. ecs-online-sim · ecs-offline-sim CloudFormation       ║
# ║  4. Kinesis PutRecord                                ║
# ║  : base-up  (vpc · ecs-cluster · kinesis  )   ║
# ╚══════════════════════════════════════════════════════════════╝
source "$(dirname "$0")/_common.sh"

check_env

# ── Step 1.    ────────────────────────────────────
step "Step 1 ·   "

REQUIRED_STACKS=(
  "bookflow-10-vpc-sales-data"
  "bookflow-20-kinesis"
  "bookflow-30-ecs-cluster"
)
for s in "${REQUIRED_STACKS[@]}"; do
  if stack_exists "${s}"; then
    ok "${s} · "
  else
    err "${s}  → python bookflow.py base-up  "
  fi
done

ACCOUNT=$(account_id)
ECR_REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

# ── Step 2. ECS Sim   + ECR Push ───────────────────
step "Step 2 · ECS Sim   + ECR Push"

# ECR    ( )
for SIM in online-simul offline-simul; do
  aws ecr describe-repositories --repository-names "${PROJECT}/${SIM}" \
    --region "${REGION}" > /dev/null 2>&1 || \
  aws ecr create-repository --repository-name "${PROJECT}/${SIM}" \
    --region "${REGION}" > /dev/null
  ok "ECR repo: ${PROJECT}/${SIM}"
done

ecr_login > /dev/null

for SIM in online-simul offline-simul; do
  SIM_DIR="${REPO_ROOT}/ecs-sims/${SIM}"
  IMAGE="${ECR_REGISTRY}/${PROJECT}/${SIM}:latest"
  info ": ${SIM}..."
  docker build -t "${IMAGE}" "${SIM_DIR}"
  docker push "${IMAGE}"
  ok "${SIM} → ${IMAGE}"
done

# ── Step 3. ECS Sim CloudFormation  ────────────────────────
step "Step 3 · ECS Sim   (etl-streaming)"

# endpoints-sales-data (S3/Kinesis VPC endpoint)
if ! stack_exists "bookflow-10-endpoints-sales-data"; then
  info "endpoints-sales-data  ..."
  bookflow task etl-streaming
else
  info "etl-streaming    · update ..."
  bookflow task etl-streaming
fi

# ── Step 4. ECS    ─────────────────────────────
step "Step 4 · ECS   "

CLUSTER=$(stack_output "bookflow-30-ecs-cluster" "ClusterName")
sleep 10

for SVC in online-sim offline-sim; do
  STATUS=$(aws ecs describe-services \
    --cluster "${CLUSTER}" \
    --services "${SVC}" \
    --query 'services[0].deployments[0].rolloutState' \
    --output text 2>/dev/null || echo "NOT_FOUND")
  info "${SVC}: ${STATUS}"
done

# ── Step 5. Kinesis   ───────────────────────────────
step "Step 5 · Kinesis   "

STREAM_NAME=$(stack_output "bookflow-20-kinesis" "StreamName")
STATUS=$(aws kinesis describe-stream-summary \
  --stream-name "${STREAM_NAME}" \
  --query 'StreamDescriptionSummary.StreamStatus' --output text)
ok "Kinesis ${STREAM_NAME}: ${STATUS}"

SHARD_CNT=$(aws kinesis describe-stream-summary \
  --stream-name "${STREAM_NAME}" \
  --query 'StreamDescriptionSummary.OpenShardCount' --output text)
info "Shard : ${SHARD_CNT}"

# ──    ──────────────────────────────────────
step "Day 01  "
cat << 'EOF'
  [ ] bookflow-10-vpc-sales-data 
  [ ] bookflow-20-kinesis 
  [ ] bookflow-30-ecs-cluster 
  [ ] ECR online-simul / offline-simul  Push
  [ ] ecs-online-sim  
  [ ] ecs-offline-sim  
  [ ] Kinesis  ACTIVE 

(4/29)  : day02_0429_lambda_sync.sh
  → event-sync Lambda   + SAM  
EOF
