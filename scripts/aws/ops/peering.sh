#!/usr/bin/env bash
# peering.sh · 5 VPC peering (Phase 1-2 · 무과금 · cross-VPC 통신 필수)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "peering"; init_log "peering" "$ACTION"; pre_flight
INFRA="$PROJECT_ROOT/infra/aws/10-network-core/peering"

case "$ACTION" in
up)
  step "peering.sh up · 5 peering (5 병렬 · RT route 같이)"
  cfn_parallel_deploy <<EOF
bookflow-10-peering-bookflow-ai-data|$INFRA/bookflow-ai-data.yaml
bookflow-10-peering-bookflow-ai-egress|$INFRA/bookflow-ai-egress.yaml
bookflow-10-peering-egress-data|$INFRA/egress-data.yaml
bookflow-10-peering-sales-data-egress|$INFRA/sales-data-egress.yaml
bookflow-10-peering-ansible-data|$INFRA/ansible-data.yaml
EOF
  state_write "peering" "up"; step "peering.sh up done" ;;
down)
  step "peering.sh down"
  cfn_bulk_delete "bookflow-10-peering-" "bookflow-00-"
  state_write "peering" "down"; step "peering.sh down done" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
