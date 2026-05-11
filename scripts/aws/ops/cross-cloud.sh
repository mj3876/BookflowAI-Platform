#!/usr/bin/env bash
# cross-cloud.sh · AWS 측 cross-cloud (cgw + tgw + tgw-routes + s2s vpn) · Azure/GCP IP env
# Azure/GCP deploy 는 각 담당자 (민지/우혁) 본인 IaC 로 별도 진행.
#
# 실행 전 환경변수:
#   BOOKFLOW_GCP_VPN_GW_IP   GCP HA VPN Gateway 공인 IP  (필수)
#   BOOKFLOW_GCP_VPN_PSK     GCP VPN Pre-Shared Key      (선택 · 없으면 AWS 자동생성)
#   BOOKFLOW_AZURE_VPN_GW_IP Azure VPN Gateway 공인 IP   (선택)
#   BOOKFLOW_AZURE_VPN_PSK   Azure VPN Pre-Shared Key    (선택)
#
# 주의: start-day.sh(peering 모드) 실행 후 이 스크립트를 쓸 때는
#       peering.sh down 을 먼저 실행할 것.
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

  AZ_IP="${BOOKFLOW_AZURE_VPN_GW_IP:-}"
  AZ_PSK="${BOOKFLOW_AZURE_VPN_PSK:-}"
  GCP_IP="${BOOKFLOW_GCP_VPN_GW_IP:-}"
  GCP_PSK="${BOOKFLOW_GCP_VPN_PSK:-}"
  log "Azure VPN GW IP: ${AZ_IP:-(미입력)}"
  log "GCP HA VPN IP:   ${GCP_IP:-(미입력)}"
  [ -z "$GCP_IP" ] && [ -z "$AZ_IP" ] && { err "GCP/Azure IP 환경변수 미설정 — export BOOKFLOW_GCP_VPN_GW_IP=<IP>"; exit 1; }

  # ── 1. customer-gateway ─────────────────────────────────
  step "1. customer-gateway"
  CG_PARAMS=()
  [ -n "$AZ_IP"  ] && CG_PARAMS+=("AzureVpnGatewayIp=$AZ_IP")
  [ -n "$GCP_IP" ] && CG_PARAMS+=("GcpHaVpnIp=$GCP_IP")
  if [ ${#CG_PARAMS[@]} -gt 0 ]; then
    cfn_deploy bookflow-10-customer-gateway "$INFRA/10-network-core/customer-gateway.yaml" "${CG_PARAMS[@]}"
  else
    cfn_deploy bookflow-10-customer-gateway "$INFRA/10-network-core/customer-gateway.yaml"
  fi

  # ── 2. tgw ──────────────────────────────────────────────
  step "2. tgw"
  cfn_deploy bookflow-60-tgw "$INFRA/60-network-cross-cloud/tgw.yaml"

  # ── 3. tgw-vpc-routes + vpn-site-to-site (병렬) ─────────
  # [BUG FIX] PSK 환경변수를 CFN 파라미터로 전달:
  #   미전달 시 AWS가 PSK·inside CIDR을 자동생성 → GCP terraform.tfvars 값을 맞출 수 없음.
  step "3. tgw-vpc-routes + vpn-site-to-site (2 병렬)"
  VPN_PARAMS=""
  [ -n "$AZ_IP"  ] && VPN_PARAMS+="|EnableAzureVpn=true"
  [ -n "$AZ_PSK" ] && VPN_PARAMS+="|AzurePresharedKey=$AZ_PSK"
  [ -n "$GCP_IP" ] && VPN_PARAMS+="|EnableGcpVpn=true"
  [ -n "$GCP_PSK" ] && VPN_PARAMS+="|GcpPresharedKey=$GCP_PSK"
  GCP_VPC_CIDR="${BOOKFLOW_GCP_VPC_CIDR:-10.50.0.0/24}"
  cfn_parallel_deploy <<EOF
bookflow-60-tgw-vpc-routes|$INFRA/60-network-cross-cloud/tgw-vpc-routes.yaml|GcpVpcCidr=$GCP_VPC_CIDR
bookflow-60-vpn-site-to-site|$INFRA/60-network-cross-cloud/vpn-site-to-site.yaml${VPN_PARAMS}
EOF

  # ── 4. VPN attachment → TGW RT association + propagation ─
  # [BUG FIX] VPN connection 생성 직후 attachment가 pending일 수 있으므로
  #   available 될 때까지 최대 2분 대기 후 associate.
  step "4. VPN attachment → TGW RT association + propagation (BGP 필수)"
  py - <<'PYEOF'
import boto3, os, sys, time
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
session = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
ec2 = session.client('ec2')
cf  = session.client('cloudformation')

tgw_rt_id = next(
    o['OutputValue'] for o in
    cf.describe_stacks(StackName='bookflow-60-tgw')['Stacks'][0]['Outputs']
    if o['OutputKey'] == 'TgwRouteTableId'
)
print(f'  TGW RT: {tgw_rt_id}')

# available 대기 (최대 120s)
for attempt in range(12):
    atts = ec2.describe_transit_gateway_attachments(
        Filters=[{'Name': 'resource-type', 'Values': ['vpn']},
                 {'Name': 'state', 'Values': ['available', 'pending']}]
    )['TransitGatewayAttachments']
    if atts:
        break
    print(f'  VPN attachment 대기 중... ({(attempt+1)*10}s)')
    time.sleep(10)
else:
    print('  WARN: VPN attachment 없음 — gcp-vpn-info.sh 로 수동 확인')
    sys.exit(0)

for att in atts:
    att_id = att['TransitGatewayAttachmentId']
    name   = next((t['Value'] for t in att.get('Tags', []) if t['Key'] == 'Name'), '?')
    for fn, label in [
        (ec2.associate_transit_gateway_route_table, 'associate'),
        (ec2.enable_transit_gateway_route_table_propagation, 'propagate'),
    ]:
        try:
            fn(TransitGatewayRouteTableId=tgw_rt_id, TransitGatewayAttachmentId=att_id)
            print(f'  {label} {att_id} ({name}) -> OK')
        except ec2.exceptions.ClientError as e:
            if 'already' in str(e).lower():
                print(f'  {label} {att_id} ({name}) -> already done')
            else:
                print(f'  {label} WARN: {str(e)[:120]}')
PYEOF

  if [ "$WITH_WAF" = "yes" ]; then
    step "5. waf"
    cfn_deploy bookflow-50-waf "$INFRA/50-network-traffic/waf.yaml"
  fi
  if [ "$WITH_CLIENT_VPN" = "yes" ]; then
    step "6. client-vpn"
    py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn
  fi

  # ── 7. Tunnel IP 출력 ────────────────────────────────────
  # [BUG FIX] aws cli 직접 호출 대신 boto3 사용 (cp949 인코딩 오류 방지)
  step "7. TGW VPN tunnel Outside IP (GCP/Azure 담당자 전달용)"
  py - <<'PYEOF'
import boto3, os, sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
session = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
ec2 = session.client('ec2')
conns = ec2.describe_vpn_connections(
    Filters=[{'Name': 'tag:Name', 'Values': ['bookflow-vpn-*']}]
)['VpnConnections']
for conn in conns:
    name = next((t['Value'] for t in conn.get('Tags', []) if t['Key'] == 'Name'), '?')
    ips  = [t['OutsideIpAddress'] for t in conn.get('VgwTelemetry', [])]
    print(f'  {name}: {ips}')
PYEOF

  state_write "cross-cloud" "up"
  step "cross-cloud.sh up done"
  log "다음 단계: bash scripts/aws/ops/gcp-vpn-info.sh → terraform.tfvars → terraform apply"
  ;;

down)
  step "cross-cloud.sh down"
  py "$PROJECT_ROOT/scripts/aws/bookflow.py" task client-vpn --down 2>/dev/null || true
  cfn_bulk_delete "bookflow-50-waf" "bookflow-00-"
  cfn_bulk_delete "bookflow-60-" "bookflow-00-"
  cfn_bulk_delete "bookflow-10-customer-gateway" "bookflow-00-"
  state_write "cross-cloud" "down"
  step "cross-cloud.sh down done"
  ;;

*) err "usage: $0 up|down [--with-waf] [--with-client-vpn]"; exit 2 ;;
esac
