#!/usr/bin/env bash
# cross-cloud.sh · AWS 측 cross-cloud (cgw + tgw + tgw-routes + s2s vpn) · Azure/GCP IP env
# Azure/GCP deploy 는 각 담당자 (민지/우혁) 본인 IaC 로 별도 진행.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
ACTION="${1:-up}"; shift || true
load_env; acquire_lock "cross-cloud"; init_log "cross-cloud" "$ACTION"; pre_flight
INFRA="$PROJECT_ROOT/infra/aws"

WITH_WAF=no
WITH_CLIENT_VPN=no
for arg in "$@"; do
  case "$arg" in
    --with-waf) WITH_WAF=yes ;;
    --with-client-vpn) WITH_CLIENT_VPN=yes ;;
  esac
done

case "$ACTION" in
up)
  step "cross-cloud.sh up [waf=$WITH_WAF · client-vpn=$WITH_CLIENT_VPN]"

  # Azure/GCP IP 환경변수 검증
  AZ_IP="${BOOKFLOW_AZURE_VPN_GW_IP:-}"
  GCP_IP="${BOOKFLOW_GCP_VPN_GW_IP:-}"
  log "Azure VPN GW IP: ${AZ_IP:-(미입력)}"
  log "GCP HA VPN IP:   ${GCP_IP:-(미입력)}"

  step "1. customer-gateway"
  CG_PARAMS=()
  [ -n "$AZ_IP" ] && CG_PARAMS+=("AzureVpnGatewayIp=$AZ_IP")
  [ -n "$GCP_IP" ] && CG_PARAMS+=("GcpHaVpnIp=$GCP_IP")
  cfn_deploy bookflow-10-customer-gateway "$INFRA/10-network-core/customer-gateway.yaml" "${CG_PARAMS[@]}"

  step "2. tgw"
  cfn_deploy bookflow-60-tgw "$INFRA/60-network-cross-cloud/tgw.yaml"

  step "3. tgw-vpc-routes + vpn-site-to-site (2 병렬)"
  VPN_PARAMS=""
  [ -n "$AZ_IP" ] && VPN_PARAMS+="|EnableAzureVpn=true"
  [ -n "$GCP_IP" ] && VPN_PARAMS+="|EnableGcpVpn=true"
  cfn_parallel_deploy <<EOF
bookflow-60-tgw-vpc-routes|$INFRA/60-network-cross-cloud/tgw-vpc-routes.yaml
bookflow-60-vpn-site-to-site|$INFRA/60-network-cross-cloud/vpn-site-to-site.yaml${VPN_PARAMS}
EOF

  if [ "$WITH_WAF" = "yes" ]; then
    step "4. waf"
    cfn_deploy bookflow-50-waf "$INFRA/50-network-traffic/waf.yaml"
  fi
  if [ "$WITH_CLIENT_VPN" = "yes" ]; then
    step "5. client-vpn"
    py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn
  fi

  step "6. TGW VPN tunnel IP 출력 (Azure/GCP 담당자에게)"
  aws ec2 describe-vpn-connections \
    --filters "Name=tag:Name,Values=bookflow-vpn-*" \
    --query "VpnConnections[].{Name:Tags[?Key=='Name']|[0].Value, Tunnels:VgwTelemetry[].OutsideIpAddress}" \
    --output table 2>&1 || true

  state_write "cross-cloud" "up"; step "cross-cloud.sh up done" ;;
down)
  step "cross-cloud.sh down"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn --down 2>/dev/null || true
  cfn_bulk_delete "bookflow-50-waf" "bookflow-00-"
  cfn_bulk_delete "bookflow-60-" "bookflow-00-"
  cfn_bulk_delete "bookflow-10-customer-gateway" "bookflow-00-"
  state_write "cross-cloud" "down"; step "cross-cloud.sh down done" ;;
*) err "usage: $0 up|down [--with-waf] [--with-client-vpn]"; exit 2 ;;
esac
