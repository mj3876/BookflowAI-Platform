#!/bin/bash
# scripts/vpn-connect.sh
# AWS CloudFormation YAML   → Azure Local Network Gateway + VPN Connection 
#
# :
#   1. scripts/deploy-vpn.sh  (Azure VPN Gateway )
#   2. AWS  vpn-site-to-site.yaml   
#      (EnableAzureVpn=true · Azure VPN GW IP  )
#
#  :
#   infra/aws/60-network-cross-cloud/tgw.yaml            → AWS_ASN
#   infra/aws/60-network-cross-cloud/vpn-site-to-site.yaml → Tunnel Inside CIDR
#   infra/aws/10-network-core/vpc-*.yaml                 → VPC CIDR
#   AWS CLI (bookflow-60-vpn-site-to-site )          → Gateway IP, PSK

set -e
export MSYS_NO_PATHCONV=1

RESOURCE_GROUP="rg-bookflow"
PREFIX="bookflow"
REGION="${AWS_REGION:-ap-northeast-1}"
AWS_STACK_PREFIX="bookflow"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
INFRA_AWS="${REPO_ROOT}/infra/aws"

echo "========================================"
echo " BOOKFLOW VPN Connection "
echo " (AWS TGW ↔ Azure VPN Gateway · BGP)"
echo "========================================"
echo ""

# ── 0.   ───────────────────────────────────────
echo "[0]  "

if ! command -v aws &>/dev/null; then
  echo "  ✗ AWS CLI  —   "
  exit 1
fi
if ! az account show --output none 2>/dev/null; then
  echo "  ✗ Azure CLI  — 'az login'  "
  exit 1
fi
VPN_GW=$(az network vnet-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "vpngw-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if [ -z "$VPN_GW" ]; then
  echo "  ✗ Azure VPN Gateway 'vpngw-${PREFIX}'  — deploy-vpn.sh  "
  exit 1
fi
echo "  ✓ AWS CLI / Azure CLI / VPN Gateway  "
echo ""

# ── 1. CloudFormation YAML  ( ) ─────────────────
echo "[1] CloudFormation YAML "

TGW_YAML="${INFRA_AWS}/60-network-cross-cloud/tgw.yaml"
VPN_YAML="${INFRA_AWS}/60-network-cross-cloud/vpn-site-to-site.yaml"

# AWS_ASN: tgw.yaml → TgwAsn.Default
AWS_ASN=$(grep -A3 'TgwAsn:' "$TGW_YAML" | grep 'Default:' | grep -oE '[0-9]+' | head -1)
if [ -z "$AWS_ASN" ]; then
  echo "  ✗ AWS_ASN   — $TGW_YAML  "
  exit 1
fi
echo "  AWS_ASN (TGW BGP): $AWS_ASN"

# Azure  Inside CIDR → AWS BGP Peer IP (CIDR +1)
# vpn-site-to-site.yaml: AzureVpnConnection Tunnel1 = 169.254.21.4/30 → AWS=169.254.21.5
TUNNEL1_CIDR=$(grep -A20 'AzureVpnConnection:' "$VPN_YAML" \
  | grep 'TunnelInsideCidr:' | head -1 \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+')
if [ -z "$TUNNEL1_CIDR" ]; then
  echo "  ✗ Tunnel Inside CIDR   — $VPN_YAML  "
  exit 1
fi
TUNNEL_NET=$(echo "$TUNNEL1_CIDR" | cut -d'/' -f1)
IFS='.' read -r _o1 _o2 _o3 _o4 <<< "$TUNNEL_NET"
AWS_BGP_PEER_IP="${_o1}.${_o2}.${_o3}.$((_o4 + 1))"
echo "  Tunnel1 Inside CIDR : $TUNNEL1_CIDR"
echo "  AWS BGP Peer IP     : $AWS_BGP_PEER_IP  (Azure BGP  AWS  IP)"

# VPC CIDRs (TGW  4 VPC)
VPC_FILES=(
  "${INFRA_AWS}/10-network-core/vpc-bookflow-ai.yaml"
  "${INFRA_AWS}/10-network-core/vpc-sales-data.yaml"
  "${INFRA_AWS}/10-network-core/vpc-egress.yaml"
  "${INFRA_AWS}/10-network-core/vpc-data.yaml"
)
AWS_VPC_CIDRS=()
for _f in "${VPC_FILES[@]}"; do
  _cidr=$(grep -m1 'CidrBlock:' "$_f" | grep -oE '10\.[0-9]+\.0\.0/16' || echo "")
  [ -n "$_cidr" ] && AWS_VPC_CIDRS+=("$_cidr")
done
if [ ${#AWS_VPC_CIDRS[@]} -eq 0 ]; then
  echo "  ✗ VPC CIDR  "
  exit 1
fi
echo "  AWS VPC CIDRs       : ${AWS_VPC_CIDRS[*]}"

# ── 2. AWS CLI    ────────────────────────────
echo ""
echo "[2] AWS CLI VPN Connection  "
echo "  : ${AWS_STACK_PREFIX}-60-vpn-site-to-site (: $REGION)"

VPN_CONN_ID=$(aws cloudformation describe-stacks \
  --stack-name "${AWS_STACK_PREFIX}-60-vpn-site-to-site" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='AzureVpnConnectionId'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [ -z "$VPN_CONN_ID" ] || [ "$VPN_CONN_ID" = "None" ]; then
  echo ""
  echo "  ⚠️  AWS VPN Connection   "
  echo "     : AWS CLI    "
  echo "     AWS  → EC2 → Site-to-Site VPN → bookflow-vpn-azure"
  echo "      Tunnel 1 Outside IP  PSK   ."
  echo ""
  read -p "  AWS TGW Outside IP (Tunnel1): " AWS_GATEWAY_IP
  read -s -p "  Pre-Shared Key (PSK)        : " PSK
  echo ""
else
  echo "  VPN Connection ID: $VPN_CONN_ID"

  # Tunnel1 Outside IP
  AWS_GATEWAY_IP=$(aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$VPN_CONN_ID" \
    --region "$REGION" \
    --query "VpnConnections[0].VgwTelemetry[0].OutsideIpAddress" \
    --output text 2>/dev/null || echo "")

  # PSK: CustomerGatewayConfiguration XML → <pre_shared_key>   
  CONFIG_XML=$(aws ec2 describe-vpn-connections \
    --vpn-connection-ids "$VPN_CONN_ID" \
    --region "$REGION" \
    --query "VpnConnections[0].CustomerGatewayConfiguration" \
    --output text 2>/dev/null || echo "")
  PSK=$(echo "$CONFIG_XML" \
    | sed -n 's/.*<pre_shared_key>\([^<]*\)<\/pre_shared_key>.*/\1/p' \
    | head -1)

  if [ -z "$AWS_GATEWAY_IP" ] || [ "$AWS_GATEWAY_IP" = "None" ]; then
    echo "  ⚠️  TGW Outside IP    (VPN Connection     )"
    read -p "  AWS TGW Outside IP (Tunnel1,  ): " AWS_GATEWAY_IP
  fi
  if [ -z "$PSK" ]; then
    echo "  ⚠️  PSK   "
    read -s -p "  Pre-Shared Key (PSK,  ): " PSK
    echo ""
  fi
fi

echo "  AWS_GATEWAY_IP: $AWS_GATEWAY_IP"
echo "  PSK           : ****"
echo ""

# ──    ──────────────────────────────────────────
echo " :"
echo "  AWS_ASN         : $AWS_ASN"
echo "  AWS_BGP_PEER_IP : $AWS_BGP_PEER_IP"
echo "  AWS_GATEWAY_IP  : $AWS_GATEWAY_IP"
echo "  VPC CIDRs       : ${AWS_VPC_CIDRS[*]}"
echo ""
echo "  Azure VPN  .  Enter,  Ctrl+C"
read

# ── 3. Azure Local Network Gateway / ──────────────
echo ""
echo "[3] Azure Local Network Gateway  (BGP  )"
LNG_EXISTS=$(az network local-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "lng-${PREFIX}-aws-active" \
  --query name --output tsv 2>/dev/null || echo "")

if [ -n "$LNG_EXISTS" ]; then
  echo "   LNG  —    (BGP    )"
  az network local-gateway delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "lng-${PREFIX}-aws-active"
  echo "  ✓  LNG  "
fi

az network local-gateway create \
  --resource-group "$RESOURCE_GROUP" \
  --name "lng-${PREFIX}-aws-active" \
  --gateway-ip-address "$AWS_GATEWAY_IP" \
  --local-address-prefixes "${AWS_VPC_CIDRS[@]}" \
  --asn "$AWS_ASN" \
  --bgp-peering-address "$AWS_BGP_PEER_IP" \
  --output table
echo "  ✓ Local Network Gateway  "
echo "    --asn              : $AWS_ASN"
echo "    --bgp-peering-address: $AWS_BGP_PEER_IP"
echo "    --gateway-ip-address : $AWS_GATEWAY_IP"

# ── 4. Azure VPN Connection  ──────────────────────────
echo ""
echo "[4] Azure VPN Connection  (BGP )"
CONN_EXISTS=$(az network vpn-connection show \
  --resource-group "$RESOURCE_GROUP" \
  --name "conn-${PREFIX}-aws-active" \
  --query name --output tsv 2>/dev/null || echo "")

if [ -n "$CONN_EXISTS" ]; then
  echo "   Connection  —   "
  az network vpn-connection delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "conn-${PREFIX}-aws-active"
  echo "  ✓  Connection  "
fi

az network vpn-connection create \
  --resource-group "$RESOURCE_GROUP" \
  --name "conn-${PREFIX}-aws-active" \
  --vnet-gateway1 "vpngw-${PREFIX}" \
  --local-gateway2 "lng-${PREFIX}-aws-active" \
  --shared-key "$PSK" \
  --enable-bgp \
  --output table
echo "  ✓ VPN Connection   (BGP )"

# ── 5.    ─────────────────────────────────────
echo ""
echo "[5]    (BGP negotiation 2~5 )"
echo "  2  ..."
sleep 120

az network vpn-connection show \
  --resource-group "$RESOURCE_GROUP" \
  --name "conn-${PREFIX}-aws-active" \
  --query "{name:name, status:connectionStatus, bgpEnabled:enableBgp, egress:egressBytesTransferred}" \
  --output table

echo ""
echo "[6] BGP   "
az network vnet-gateway list-learned-routes \
  --resource-group "$RESOURCE_GROUP" \
  --name "vpngw-${PREFIX}" \
  --output table

echo ""
echo "========================================"
echo " VPN Connection  "
echo "========================================"
echo "  Local NW GW   : lng-${PREFIX}-aws-active"
echo "  VPN Connection: conn-${PREFIX}-aws-active"
echo "  BGP ASN (AWS) : $AWS_ASN"
echo "  BGP Peer IP   : $AWS_BGP_PEER_IP"
echo "  AWS VPC CIDRs : ${AWS_VPC_CIDRS[*]}"
echo ""
echo " : bash scripts/test-connectivity.sh"
