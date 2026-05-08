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
  step "cicd.sh up · 4 CodePipeline (4 병렬)"
  cfn_parallel_deploy <<EOF
bookflow-cicd-eks|$CICD/eks-pipeline.yaml
bookflow-cicd-ecs|$CICD/ecs-pipeline.yaml
bookflow-cicd-lambda-sam|$CICD/lambda-sam-pipeline.yaml
bookflow-cicd-publisher|$CICD/publisher-codedeploy.yaml
EOF
  state_write "cicd" "up"; step "cicd.sh up done" ;;
down)
  step "cicd.sh down"
  cfn_bulk_delete "bookflow-cicd-" "bookflow-00-"
  state_write "cicd" "down"; step "cicd.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
