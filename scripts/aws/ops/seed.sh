#!/usr/bin/env bash
# seed.sh · parquet → CSV → S3 → SSM ansible-node → psql COPY
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"
load_env; acquire_lock "seed"; init_log "seed" "$ACTION"; pre_flight

case "$ACTION" in
up)
  step "seed.sh up · parquet → RDS"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task rds-seed
  state_write "seed" "up"; step "seed.sh up done" ;;
down)
  step "seed.sh down · RDS truncate (옵션 · RDS stack destroy 시 자동)"
  log "skip · base.sh down 시 RDS 자체가 destroy 됨"
  state_write "seed" "down" ;;
*) err "usage: $0 up|down"; exit 2 ;;
esac
