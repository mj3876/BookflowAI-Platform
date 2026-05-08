#!/usr/bin/env bash
# gcp-vpn-info.sh · AWS TGW → GCP HA VPN 연결에 필요한 정보 한 번에 출력
# 실제 배포된 tunnel inside CIDR 에서 BGP IP 동적 계산 → terraform.tfvars 블록 출력
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/common.sh"
load_env
pre_flight

echo ""
echo "════════════════════════════════════════════════════════"
echo "  AWS → GCP VPN 연결 정보 추출"
echo "════════════════════════════════════════════════════════"

VPN_CONN_ID=$(aws cloudformation describe-stacks \
  --stack-name bookflow-60-vpn-site-to-site \
  --query "Stacks[0].Outputs[?OutputKey=='GcpVpnConnectionId'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [ -z "$VPN_CONN_ID" ] || [ "$VPN_CONN_ID" = "None" ]; then
  echo "  [ERROR] bookflow-60-vpn-site-to-site 스택이 없거나 GCP VPN 비활성화 상태"
  echo "  먼저 실행: BOOKFLOW_GCP_VPN_GW_IP=<IP> bash scripts/aws/ops/network-mode.sh tgw"
  exit 1
fi
echo ""
echo "  VPN Connection ID : $VPN_CONN_ID"

TGW_ASN=$(aws cloudformation describe-stacks \
  --stack-name bookflow-60-tgw \
  --query "Stacks[0].Outputs[?OutputKey=='TgwAsn'].OutputValue" \
  --output text 2>/dev/null || echo "64512")

# Python으로 전체 파싱 (inside CIDR → BGP IP 계산, PSK 추출, VPC CIDR 목록)
py - <<PYEOF
import boto3, os, sys, re, json, ipaddress
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

session  = boto3.Session(profile_name=os.environ['AWS_PROFILE'], region_name=os.environ['AWS_REGION'])
ec2      = session.client('ec2')

vpn = ec2.describe_vpn_connections(
    VpnConnectionIds=['$VPN_CONN_ID']
)['VpnConnections'][0]

# ── Tunnel Outside IP + Inside CIDR ──
telemetry = vpn.get('VgwTelemetry', [])
options   = vpn.get('Options', {}).get('TunnelOptions', [])

tunnels = []
for i, opt in enumerate(options[:2]):
    outside_ip   = telemetry[i]['OutsideIpAddress'] if i < len(telemetry) else '?'
    inside_cidr  = opt.get('TunnelInsideCidr', '')
    if inside_cidr:
        net      = ipaddress.ip_network(inside_cidr, strict=False)
        hosts    = list(net.hosts())
        aws_ip   = str(hosts[0])   # AWS 가 첫 번째 호스트 사용
        gcp_ip   = str(hosts[1])   # GCP (CGW) 가 두 번째 호스트 사용
        gcp_cidr = f'{gcp_ip}/{net.prefixlen}'
    else:
        aws_ip = gcp_ip = gcp_cidr = '?'
    tunnels.append({
        'idx': i,
        'outside_ip':  outside_ip,
        'inside_cidr': inside_cidr,
        'aws_ip':      aws_ip,
        'gcp_ip':      gcp_ip,
        'gcp_cidr':    gcp_cidr,
    })
    print(f'  Tunnel {i} Outside IP  : {outside_ip}')
    print(f'  Tunnel {i} Inside CIDR : {inside_cidr}  (AWS={aws_ip}  GCP={gcp_ip})')

# ── PSK 추출 ──
xml   = vpn.get('CustomerGatewayConfiguration', '')
psks  = re.findall(r'<pre_shared_key>([^<]+)</pre_shared_key>', xml)
psk   = psks[0] if psks else '<PSK_HERE>'
print(f'\n  PSK : {psk}')

# ── AWS VPC CIDRs ──
vpcs = ec2.describe_vpcs(
    Filters=[{'Name': 'tag:Name', 'Values': ['bookflow-*']}]
)['Vpcs']
cidrs = sorted(v['CidrBlock'] for v in vpcs)
print(f'\n  AWS VPC CIDRs : {cidrs}')

cidr_tf = '[' + ', '.join(f'"{c}"' for c in cidrs) + ']'
t0, t1  = tunnels[0], tunnels[1]

# ── terraform.tfvars 출력 ──
print("""
════════════════════════════════════════════════════════
  infra/gcp/20-network-daily/terraform.tfvars
════════════════════════════════════════════════════════""")

print(f"""
project_id        = "project-8ab6bf05-54d2-4f5d-b8d"
region            = "asia-northeast1"
vpc_name          = "bookflow-vpc"

aws_peer_ips      = ["{t0['outside_ip']}", "{t1['outside_ip']}"]
aws_tgw_bgp_asn   = $TGW_ASN
gcp_router_asn    = 64514

vpn_shared_secret = "{psk}"

aws_vpc_cidrs     = {cidr_tf}
azure_vnet_cidr   = "10.1.0.0/16"
gcp_routed_cidr   = "10.50.0.0/24"
psc_endpoint_host_offset = 10

bgp_sessions = {{
  tunnel0 = {{
    vpn_gateway_interface           = 0
    peer_external_gateway_interface = 0
    router_ip_cidr                  = "{t0['gcp_cidr']}"
    peer_ip_address                 = "{t0['aws_ip']}"
    advertised_route_priority       = 100
  }}
  tunnel1 = {{
    vpn_gateway_interface           = 1
    peer_external_gateway_interface = 1
    router_ip_cidr                  = "{t1['gcp_cidr']}"
    peer_ip_address                 = "{t1['aws_ip']}"
    advertised_route_priority       = 100
  }}
}}""")

print("""
════════════════════════════════════════════════════════
  다음 단계:
  1. 위 내용을 infra/gcp/20-network-daily/terraform.tfvars 로 저장
  2. cd infra/gcp/20-network-daily && terraform apply -auto-approve
════════════════════════════════════════════════════════""")
PYEOF
