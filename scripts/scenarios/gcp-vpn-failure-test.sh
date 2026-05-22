#!/usr/bin/env bash
set -euo pipefail

# Scenario 5: GCP VPN failure and recovery test
# - simulate: disable GCP Cloud Router BGP peers
# - verify: show AWS VPN, GCP VPN tunnel, and BGP status
# - restore: re-enable GCP Cloud Router BGP peers

GCP_PROJECT="${GCP_PROJECT:-project-8ab6bf05-54d2-4f5d-b8d}"
GCP_REGION="${GCP_REGION:-asia-northeast1}"
GCP_ROUTER="${GCP_ROUTER:-bookflow-aws-cr}"
GCP_BGP_PEERS=("bookflow-aws-bgp-tunnel0" "bookflow-aws-bgp-tunnel1")

AWS_REGION="${AWS_REGION:-ap-northeast-1}"
AWS_VPN_TAG_NAME="${AWS_VPN_TAG_NAME:-bookflow-vpn-gcp}"
K8S_NAMESPACE="${K8S_NAMESPACE:-bookflow}"

SIMULATE_TIMEOUT="${SIMULATE_TIMEOUT:-120}"
RESTORE_TIMEOUT="${RESTORE_TIMEOUT:-180}"
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

restore_on_exit() {
  if [[ "$RESTORE_NEEDED" == "true" ]]; then
    warn "Script interrupted. Re-enabling GCP BGP peers..."
    enable_bgp_peers || true
  fi
}
trap restore_on_exit EXIT

get_aws_vpn_conn_id() {
  aws ec2 describe-vpn-connections \
    --filters "Name=tag:Name,Values=${AWS_VPN_TAG_NAME}" "Name=state,Values=available,pending" \
    --region "$AWS_REGION" \
    --query 'VpnConnections[0].VpnConnectionId' \
    --output text 2>/dev/null || true
}

get_aws_tunnel_statuses() {
  local conn_id="$1"
  aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$conn_id" \
    --region "$AWS_REGION" \
    --query 'VpnConnections[0].VgwTelemetry[*].Status' \
    --output text 2>/dev/null || true
}

print_aws_tunnel_table() {
  local conn_id="$1"
  aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$conn_id" \
    --region "$AWS_REGION" \
    --query 'VpnConnections[0].VgwTelemetry[*].{OutsideIp:OutsideIpAddress,Status:Status,BGP:StatusMessage}' \
    --output table 2>/dev/null || echo "  (AWS VPN status lookup failed)"
}

print_gcp_tunnel_table() {
  gcloud compute vpn-tunnels list \
    --project "$GCP_PROJECT" \
    --filter "name~bookflow-aws" \
    --format "table(name:label=TUNNEL,status:label=IPSEC_STATUS,detailedStatus:label=DETAIL,peerIp:label=PEER_IP)" \
    2>/dev/null || echo "  (GCP VPN tunnel lookup failed)"
}

print_gcp_bgp_status() {
  gcloud compute routers get-status "$GCP_ROUTER" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "table(result.bgpPeerStatus[].name:label=BGP_PEER,result.bgpPeerStatus[].status:label=STATUS,result.bgpPeerStatus[].numLearnedRoutes:label=LEARNED_ROUTES)" \
    2>/dev/null || echo "  (GCP BGP status lookup failed)"
}

get_gcp_bgp_up_count() {
  local statuses
  statuses=$(gcloud compute routers get-status "$GCP_ROUTER" \
    --region "$GCP_REGION" \
    --project "$GCP_PROJECT" \
    --format "value(result.bgpPeerStatus[].status)" 2>/dev/null || true)
  awk '{ c += gsub(/UP/, "UP") } END { print c + 0 }' <<< "$statuses"
}

disable_bgp_peers() {
  for peer in "${GCP_BGP_PEERS[@]}"; do
    step "Disable GCP BGP peer: $peer"
    gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
      --peer-name "$peer" \
      --region "$GCP_REGION" \
      --project "$GCP_PROJECT" \
      --no-enabled \
      --quiet
    ok "Disabled: $peer"
  done
}

enable_bgp_peers() {
  for peer in "${GCP_BGP_PEERS[@]}"; do
    step "Enable GCP BGP peer: $peer"
    gcloud compute routers update-bgp-peer "$GCP_ROUTER" \
      --peer-name "$peer" \
      --region "$GCP_REGION" \
      --project "$GCP_PROJECT" \
      --enabled \
      --quiet
    ok "Enabled: $peer"
  done
}

check_forecast_logs() {
  if ! command -v kubectl >/dev/null 2>&1; then
    warn "kubectl not found. Skipping forecast-svc log check."
    return
  fi

  local pod
  pod=$(kubectl get pod -n "$K8S_NAMESPACE" -l app=forecast-svc \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

  if [[ -z "$pod" ]]; then
    warn "forecast-svc running pod not found. Skipping log check."
    return
  fi

  local lines
  lines=$(kubectl logs -n "$K8S_NAMESPACE" "$pod" --tail=50 2>/dev/null | grep -iE "error|timeout|bigquery" || true)
  if [[ -n "$lines" ]]; then
    warn "Recent forecast-svc error/timeout/bigquery logs:"
    echo "$lines" | head -10 | sed 's/^/    /'
  else
    ok "No recent forecast-svc error/timeout/bigquery logs found."
  fi
}

print_pending_orders_sql() {
  echo
  echo "[RDS pending_orders verification SQL]"
  echo "  SELECT COUNT(*) FROM pending_orders"
  echo "  WHERE created_at > NOW() - INTERVAL '1 hour';"
  echo
  echo "Expected:"
  echo "  VPN normal  : COUNT can increase after forecast/order workflow"
  echo "  VPN failure : COUNT does not increase because BigQuery/forecast path is unavailable"
}

cmd_check() {
  section "Current VPN Status"

  step "1/3 AWS VPN tunnel status"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -z "$conn_id" || "$conn_id" == "None" ]]; then
    warn "AWS VPN connection not found."
  else
    info "AWS VPN Connection: $conn_id"
    print_aws_tunnel_table "$conn_id"
  fi

  step "2/3 GCP VPN tunnel status"
  print_gcp_tunnel_table

  step "3/3 GCP Cloud Router BGP status"
  print_gcp_bgp_status
}

cmd_simulate() {
  section "Simulate Failure: Disable GCP BGP Peers"

  local bgp_up
  bgp_up=$(get_gcp_bgp_up_count)
  if [[ "$bgp_up" -eq 0 ]]; then
    error "GCP BGP peers are already DOWN. Run restore first."
    exit 1
  fi
  ok "GCP BGP peers UP before test: $bgp_up"

  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
    info "Before failure - AWS tunnel status:"
    print_aws_tunnel_table "$conn_id"
  fi

  RESTORE_NEEDED=true
  disable_bgp_peers

  if [[ -n "${conn_id:-}" && "$conn_id" != "None" ]]; then
    section "Waiting for AWS VPN tunnel DOWN / BGP route withdrawal"
    local t0 elapsed statuses
    t0=$(date +%s)
    while true; do
      statuses=$(get_aws_tunnel_statuses "$conn_id")
      elapsed=$(( $(date +%s) - t0 ))
      echo "  [${elapsed}s] AWS tunnel status: ${statuses}"

      if [[ -n "$statuses" ]] && ! grep -q "UP" <<< "$statuses"; then
        ok "AWS tunnels are DOWN (${elapsed}s)."
        break
      fi
      if [[ $elapsed -ge $SIMULATE_TIMEOUT ]]; then
        warn "Timeout reached (${SIMULATE_TIMEOUT}s). BGP hold timer may still be in progress."
        break
      fi
      sleep 10
    done
  fi

  info "After failure - GCP BGP status:"
  print_gcp_bgp_status
}

cmd_verify() {
  section "Failure Impact Verification"

  step "1/4 AWS VPN tunnel status"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
    print_aws_tunnel_table "$conn_id"
  else
    warn "AWS VPN connection not found."
  fi

  step "2/4 GCP VPN tunnel / BGP status"
  print_gcp_tunnel_table
  print_gcp_bgp_status

  local bgp_up
  bgp_up=$(get_gcp_bgp_up_count)
  if [[ "$bgp_up" -eq 0 ]]; then
    ok "GCP BGP peers are DOWN."
  else
    warn "GCP BGP peers still UP: $bgp_up"
  fi

  step "3/4 forecast-svc log check"
  check_forecast_logs

  step "4/4 pending_orders verification guide"
  print_pending_orders_sql
}

cmd_restore() {
  section "Restore: Enable GCP BGP Peers"
  enable_bgp_peers
  RESTORE_NEEDED=false

  section "Waiting for BGP Recovery"
  local t0 elapsed bgp_up
  t0=$(date +%s)
  while true; do
    bgp_up=$(get_gcp_bgp_up_count)
    elapsed=$(( $(date +%s) - t0 ))
    echo "  [${elapsed}s] GCP BGP UP: ${bgp_up}/${#GCP_BGP_PEERS[@]}"

    if [[ "$bgp_up" -eq "${#GCP_BGP_PEERS[@]}" ]]; then
      ok "GCP BGP recovery complete (${elapsed}s)."
      break
    fi
    if [[ $elapsed -ge $RESTORE_TIMEOUT ]]; then
      warn "Recovery timeout reached (${RESTORE_TIMEOUT}s). BGP may still be converging."
      break
    fi
    sleep 10
  done

  info "After restore - AWS VPN status:"
  local conn_id
  conn_id=$(get_aws_vpn_conn_id)
  if [[ -n "$conn_id" && "$conn_id" != "None" ]]; then
    print_aws_tunnel_table "$conn_id"
  fi

  info "After restore - GCP tunnel status:"
  print_gcp_tunnel_table
  print_gcp_bgp_status
}

cmd_all() {
  section "Scenario 5: GCP VPN Failure Test"
  echo "Flow: check -> simulate -> verify -> restore -> final check"

  cmd_check

  echo
  warn "This will disable GCP Cloud Router BGP peers and interrupt AWS-GCP private routing."
  warn "Press Enter to continue, or Ctrl+C to cancel."
  read -r

  cmd_simulate
  cmd_verify
  cmd_restore

  section "Final Status"
  cmd_check
  ok "Scenario 5 complete."
}

MODE="${1:-all}"
case "$MODE" in
  check) cmd_check ;;
  simulate) cmd_simulate ;;
  verify) cmd_verify ;;
  restore) cmd_restore ;;
  all) cmd_all ;;
  *)
    echo "Usage: $0 [check|simulate|verify|restore|all]"
    exit 1
    ;;
esac
