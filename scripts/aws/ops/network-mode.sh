#!/usr/bin/env bash
# network-mode.sh · peering ↔ TGW 전환 wrapper
# 사용: ./network-mode.sh peering | tgw
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
MODE="${1:-}"

case "$MODE" in
peering)
  step "network-mode.sh peering · TGW down → peering up"
  "$SCRIPT_DIR/cross-cloud.sh" down || true
  "$SCRIPT_DIR/peering.sh" up
  state_write "network-mode" "peering"
  ;;
tgw)
  step "network-mode.sh tgw · peering down → cross-cloud up"
  "$SCRIPT_DIR/peering.sh" down || true
  "$SCRIPT_DIR/cross-cloud.sh" up
  state_write "network-mode" "tgw"
  ;;
*)
  err "usage: $0 peering|tgw"
  exit 2 ;;
esac
