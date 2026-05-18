#!/usr/bin/env bash
# cicd.sh · CodePipeline × 4 (Tier 00 만 의존 · 언제든)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "cicd"; init_log "cicd" "$ACTION"; pre_flight
CICD="$PROJECT_ROOT/cicd/codepipeline"

case "$ACTION" in
up)
  step "cicd.sh up · 3 CodePipeline (3 병렬 · lambda-sam 제외)"
  # GCP Vertex 연동 파라미터 (.env.local 에서 로드 · eks-pipeline.yaml 필수 파라미터)
  GCP_PROJECT_ID="${GCP_PROJECT_ID:-project-8ab6bf05-54d2-4f5d-b8d}"
  GCP_VERTEX_INVOKE_URL="${GCP_VERTEX_INVOKE_URL:-https://asia-northeast1-project-8ab6bf05-54d2-4f5d-b8d.cloudfunctions.net/bookflow-vertex-invoke}"
  # lambda-sam-pipeline.yaml 은 0 byte (Lambda SAM CodePipeline 미사용 결정 · 사용자 plan)
  cfn_parallel_deploy <<EOF
bookflow-cicd-eks|$CICD/eks-pipeline.yaml|GcpProjectId=$GCP_PROJECT_ID|GcpVertexInvokeUrl=$GCP_VERTEX_INVOKE_URL
bookflow-cicd-ecs|$CICD/ecs-pipeline.yaml
bookflow-cicd-publisher|$CICD/publisher-codedeploy.yaml
EOF
  state_write "cicd" "up"; step "cicd.sh up done" ;;
down)
  step "cicd.sh down"
  cfn_bulk_delete "bookflow-cicd-" "bookflow-00-"
  state_write "cicd" "down"; step "cicd.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
