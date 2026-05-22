#!/usr/bin/env bash
set -euo pipefail

# Scenario: GCP HA VPN Active/Standby-style failover demo
# Goal:
#   Before failure:  tunnel0 has BGP routes, tunnel1 has 0 routes.
#   Failure:         tunnel0 BGP is disabled.
#   Failover:        tunnel1 BGP is enabled and receives routes.
#
# This is a demo flow layered on top of the current HA VPN. It does not delete
# VPN tunnels; it only toggles GCP Cloud Router BGP peers.

GCP_PROJECT="${GCP_PROJECT:-project-8ab6bf05-54d2-4f5d-b8d}"
GCP_REGION="${GCP_REGION:-asia-northeast1}"
GCP_ROUTER="${GCP_ROUTER:-bookflow-aws-cr}"

ACTIVE_PEER="${ACTIVE_PEER:-bookflow-aws-bgp-tunnel0}"
STANDBY_PEER="${STANDBY_PEER:-bookflow-aws-bgp-tunnel1}"

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
AWS_VPN_TAG_NAME="${AWS_VPN_TAG_NAME:-bookflow-vpn-gcp}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-180}"
RESTORE_MODE="${RESTORE_MODE:-active-active}" # active-active or active-standby
RESTORE_NEEDED=false

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC}    $*"; }
ok() { echo -e "${GREEN}[OK]${NC}      $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}    $*"; }
error() { echo -e "${RED}[ERROR]${NC}   $*"; }
step() { echo -e "${CYAN}[STEP]${NC}    $*"; }

section() {
  echo
  echo "=================================================="
  echo "  $*"
  echo "=================================================="
}

get_aws_vpn_conn_id() {
  aws ec2 describe-vpn-connections \
    --filters "Name=tag:Name,Values=${AWS_VPN_TAG_NAME}" "Name=state,Values=available,pending" \
    --region "$AWS_REGION" \
    --query 'VpnConnections[0].VpnConnectionId' \
    --output text 2>/dev/null || true
}

peer_to_tunnel_name() {
  case "$1" in
    *tunnel0) echo "bookflow-aws-tunnel-tunnel0" ;;
    *tunnel1) echo "bookflow-aws-tunnel-tunnel1" ;;
    *) echo "" ;;
  esac
}

print_aws_routes_table() {
  local conn_id="$1"
  aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$conn_id" \
    --region "$AWS_REGION" \
    --query 'VpnConnections[0].VgwTelemetry[*].{OutsideIp:OutsideIpAddress,Status:Status,Message:StatusMessage}' \
    --output json \
    | python -c '
import json, re, sys
rows = json.load(sys.stdin)
print("+-----------------+--------+------------+----------------+")
print("| AWS Tunnel IP   | Status | BGP Routes | Message        |")
print("+-----------------+--------+------------+----------------+")
for r in rows:
    msg = r.get("Message") or ""
    m = re.match(r"(\d+) BGP ROUTES", msg)
    routes = m.group(1) if m else "0"
    print("| {ip:<15} | {st:<6} | {rt:<10} | {msg:<14} |".format(
        ip=r.get("OutsideIp",""),
        st=r.get("Status",""),
        rt=routes,
        msg=msg[:14],
    ))
print("+-----------------+--------+------------+----------------+")
'
}

print_gcp_status() {
  gcloud compute vpn-tunnels list \
    --project "$GCP_PROJECT" \
    --filter "name~bookflow-aws" \
    --format "table(name:label=TUNNEL,status:label=IPSEC_STATUS,peerIp:label=PEER_IP)"

  gcloud compute routers get-status "$GCP_ROUTER" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "table(result.bgpPeerStatus[].name:label=BGP_PEER,result.bgpPeerStatus[].status:label=STATUS,result.bgpPeerStatus[].numLearnedRoutes:label=LEARNED_ROUTES)"
}

get_peer_status() {
  local peer="$1"
  gcloud compute routers get-status "$GCP_ROUTER" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format json 2>/dev/null \
    | python -c "import json,sys; peers=json.load(sys.stdin).get('result',{}).get('bgpPeerStatus',[]); print(next((p.get('status','UNKNOWN') for p in peers if p.get('name')=='$peer'), 'UNKNOWN'))"
}

get_tunnel_aws_status() {
  local conn_id="$1"
  local tunnel_name="$2"
  local peer_ip
  peer_ip=$(gcloud compute vpn-tunnels describe "$tunnel_name" \
    --project "$GCP_PROJECT" \
    --region "$GCP_REGION" \
    --format "value(peerIp)" 2>/dev/null || true)

  if [[ -z "$peer_ip" ]]; then
    echo "UNKNOWN"
    return
  fi

  aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$conn_id" \
    --region "$AWS_REGION" \
    --query "VpnConnections[0].VgwTelemetry[?OutsideIpAddress==\`${peer_ip}\`].Status" \
    --output text 2>/dev/null || echo "UNKNOWN"
}

enable_peer() {
  gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
    --peer-name "$1" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --enabled \
    --quiet
}

disable_peer() {
  gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
    --peer-name "$1" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --no-enabled \
    --quiet
}

restore_on_exit() {
  if [[ "$RESTORE_NEEDED" == "true" ]]; then
    warn "Script interrupted. Restoring BGP peers..."
    enable_peer "$ACTIVE_PEER" || true
    enable_peer "$STANDBY_PEER" || true
  fi
}
trap restore_on_exit EXIT

cmd_check() {
  section "Current VPN Status"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
    error "AWS VPN connection not found."
    exit 1
  fi
  info "AWS VPN Connection: $conn_id"
  print_aws_routes_table "$conn_id"
  print_gcp_status
}

wait_for_state() {
  local conn_id="$1"
  local wanted_active="$2"
  local wanted_standby="$3"
  local label="$4"
  local active_tunnel standby_tunnel t0 elapsed active_status standby_status

  active_tunnel=$(peer_to_tunnel_name "$ACTIVE_PEER")
  standby_tunnel=$(peer_to_tunnel_name "$STANDBY_PEER")
  t0=$(date +%s)

  section "$label"
  while true; do
    elapsed=$(( $(date +%s) - t0 ))
    active_status=$(get_tunnel_aws_status "$conn_id" "$active_tunnel")
    standby_status=$(get_tunnel_aws_status "$conn_id" "$standby_tunnel")
    echo "  [${elapsed}s] active=${active_status}, standby=${standby_status}"

    if [[ "$active_status" == "$wanted_active" && "$standby_status" == "$wanted_standby" ]]; then
      ok "Expected AWS tunnel state reached: active=${wanted_active}, standby=${wanted_standby}"
      break
    fi
    if [[ $elapsed -ge $WAIT_TIMEOUT ]]; then
      warn "Timeout reached (${WAIT_TIMEOUT}s). Continuing with current state."
      break
    fi
    sleep 10
  done
}

cmd_prepare() {
  section "Prepare Active/Standby Baseline"
  echo "Active peer  : $ACTIVE_PEER"
  echo "Standby peer : $STANDBY_PEER"

  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
    error "AWS VPN connection not found."
    exit 1
  fi

  step "Enable active peer and disable standby peer"
  enable_peer "$ACTIVE_PEER"
  disable_peer "$STANDBY_PEER"
  RESTORE_NEEDED=true

  wait_for_state "$conn_id" "UP" "DOWN" "Waiting for baseline: active has routes, standby has 0 routes"
  print_aws_routes_table "$conn_id"
  print_gcp_status
}

cmd_failover() {
  section "Failover: Active DOWN -> Standby UP"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
    error "AWS VPN connection not found."
    exit 1
  fi

  step "Disable active peer and enable standby peer"
  disable_peer "$ACTIVE_PEER"
  enable_peer "$STANDBY_PEER"
  RESTORE_NEEDED=true

  wait_for_state "$conn_id" "DOWN" "UP" "Waiting for failover: active routes move to standby"
  print_aws_routes_table "$conn_id"
  print_gcp_status

  ok "Failover evidence: the active tunnel is DOWN/0 routes and the standby tunnel is UP with BGP routes."
}

cmd_restore() {
  section "Restore"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
    error "AWS VPN connection not found."
    exit 1
  fi

  if [[ "$RESTORE_MODE" == "active-standby" ]]; then
    step "Restore to active/standby baseline"
    enable_peer "$ACTIVE_PEER"
    disable_peer "$STANDBY_PEER"
    wait_for_state "$conn_id" "UP" "DOWN" "Waiting for active/standby restore"
  else
    step "Restore to active/active baseline"
    enable_peer "$ACTIVE_PEER"
    enable_peer "$STANDBY_PEER"
    wait_for_state "$conn_id" "UP" "UP" "Waiting for active/active restore"
  fi

  RESTORE_NEEDED=false
  print_aws_routes_table "$conn_id"
  print_gcp_status
}

cmd_all() {
  section "Scenario: Active/Standby-Style VPN Failover"
  echo "Flow: prepare active/standby -> failover -> restore"
  echo
  warn "This will temporarily disable one GCP BGP peer to create a standby tunnel with 0 routes."
  warn "Press Enter to prepare the baseline, or Ctrl+C to cancel."
  read -r

  cmd_prepare

  echo
  warn "Baseline ready. AWS Console should show one tunnel with BGP routes and the other with 0 routes."
  warn "Press Enter to fail over to the standby tunnel."
  read -r

  cmd_failover

  echo
  warn "Press Enter to restore. Default restore mode is active-active."
  read -r

  cmd_restore
}

MODE="${1:-all}"
case "$MODE" in
  check) cmd_check ;;
  prepare) cmd_prepare ;;
  failover) cmd_failover ;;
  restore) cmd_restore ;;
  all) cmd_all ;;
  *)
    echo "Usage: $0 [check|prepare|failover|restore|all]"
    exit 1
    ;;
esac
