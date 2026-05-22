#!/usr/bin/env bash
# ecs.sh · POS sim 3 Fargate (sales-data + egress)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "ecs"; init_log "ecs" "$ACTION"; pre_flight
INFRA="$PROJECT_ROOT/infra/aws"

case "$ACTION" in
up)
  # inventory-api 는 publisher.sh(task publisher) 가 ALB TargetGroupArn 주입 후 배포.
  # 이 스크립트와 동시에 배포하면 UPDATE_IN_PROGRESS 충돌 → ROLLBACK 발생.
  step "ecs.sh up · 2 ECS sims (2 병렬)"
  cfn_parallel_deploy <<EOF
bookflow-40-ecs-online-sim|$INFRA/40-compute-runtime/ecs-online-sim.yaml
bookflow-40-ecs-offline-sim|$INFRA/40-compute-runtime/ecs-offline-sim.yaml
EOF
  state_write "ecs" "up"; step "ecs.sh up done" ;;
down)
  step "ecs.sh down"; cfn_bulk_delete "bookflow-40-ecs-" "bookflow-00-"
  state_write "ecs" "down"; step "ecs.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
