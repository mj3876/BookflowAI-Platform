#!/bin/bash
# scripts/deploy-vpn.sh
# VPN Gateway   (30~45 )
# deploy-all.sh  Stack 1~5   

set -e
export MSYS_NO_PATHCONV=1

RESOURCE_GROUP="rg-bookflow"
LOCATION="japanwest"
PREFIX="bookflow"

validate_bicep_syntax() {
  local template=$1
  echo "  []  : $template"
  if ! az bicep build --file "$template" --outfile /dev/null 2>/tmp/bicep_err; then
    echo "  ✗ Bicep  :"
    cat /tmp/bicep_err | sed 's/^/    /'
    return 1
  fi
  echo "  ✓   "
}

validate_deployment() {
  local deploy_name=$1
  shift
  echo "  [] Azure  : $deploy_name"
  local result
  if ! result=$(az deployment group validate \
    --resource-group "$RESOURCE_GROUP" \
    --name "$deploy_name" \
    --output json \
    "$@" 2>&1); then
    echo "  ✗   :"
    echo "$result" | python3 -c "
import sys, json
try:
    err = json.load(sys.stdin)
    details = err.get('error', {}).get('details', [err.get('error', {})])
    for d in details:
        print('    -', d.get('message', d))
except:
    print(sys.stdin.read())
" 2>/dev/null || echo "$result" | sed 's/^/    /'
    return 1
  fi
  echo "  ✓   "
}

check_deployed() {
  local name=$1
  local state
  state=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$name" \
    --query properties.provisioningState \
    --output tsv 2>/dev/null || echo "NotFound")
  [ "$state" = "Succeeded" ]
}

echo "========================================"
echo " BOOKFLOW VPN Gateway "
echo "========================================"
echo ""
echo "[0]   "
az account show --output table
echo ""
echo "VPN Gateway  30~45 ."
echo " Enter,  Ctrl+C"
read

# GatewaySubnet ID 
echo ""
echo "[1] GatewaySubnet ID "
GATEWAY_SUBNET_ID=$(az network vnet subnet show \
  --resource-group "$RESOURCE_GROUP" \
  --vnet-name "vnet-${PREFIX}" \
  --name GatewaySubnet \
  --query id --output tsv)
echo "  GatewaySubnet ID: $GATEWAY_SUBNET_ID"

# VPN Gateway 
echo ""
echo "[2]  Public IP zones    "
for PIP_NAME in "pip-${PREFIX}-vpngw-active" "pip-${PREFIX}-vpngw-standby"; do
  PIP_EXISTS=$(az network public-ip show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$PIP_NAME" \
    --query name --output tsv 2>/dev/null || echo "")
  if [ -n "$PIP_EXISTS" ]; then
    PIP_ZONES=$(az network public-ip show \
      --resource-group "$RESOURCE_GROUP" \
      --name "$PIP_NAME" \
      --query "zones" --output tsv 2>/dev/null || echo "")
    if [ -z "$PIP_ZONES" ]; then
      echo "  zones  PIP  → : $PIP_NAME"
      az network public-ip delete \
        --resource-group "$RESOURCE_GROUP" \
        --name "$PIP_NAME"
      echo "   : $PIP_NAME"
    else
      echo "  ✓ $PIP_NAME zones : $PIP_ZONES"
    fi
  fi
done

echo ""
echo "[3] VPN Gateway  "
if check_deployed "vpn-deploy"; then
  echo "  : vpn-deploy   "
else
  validate_bicep_syntax "modules/vpn.bicep" || exit 1
  validate_deployment "vpn-deploy" \
    --template-file modules/vpn.bicep \
    --parameters location="$LOCATION" \
                prefix="$PREFIX" \
                gatewaySubnetId="$GATEWAY_SUBNET_ID" \
                vpnBgpAsn=65001 || exit 1

  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "vpn-deploy" \
    --template-file modules/vpn.bicep \
    --parameters location="$LOCATION" \
                prefix="$PREFIX" \
                gatewaySubnetId="$GATEWAY_SUBNET_ID" \
                vpnBgpAsn=65001 \
    --output table
  echo "  : VPN Gateway "
fi

# AWS   
echo ""
echo "[4] AWS   "
ACTIVE_IP=$(az network public-ip show \
  --resource-group "$RESOURCE_GROUP" \
  --name "pip-${PREFIX}-vpngw-active" \
  --query ipAddress --output tsv)
STANDBY_IP=$(az network public-ip show \
  --resource-group "$RESOURCE_GROUP" \
  --name "pip-${PREFIX}-vpngw-standby" \
  --query ipAddress --output tsv)
BGP_ASN=$(az network vnet-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "vpngw-${PREFIX}" \
  --query bgpSettings.asn --output tsv)
BGP_PEERING=$(az network vnet-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "vpngw-${PREFIX}" \
  --query bgpSettings.bgpPeeringAddress --output tsv)

echo ""
echo "========================================"
echo " AWS   "
echo "========================================"
echo "  Active  IP:  $ACTIVE_IP"
echo "  Standby  IP: $STANDBY_IP"
echo "  BGP ASN:         $BGP_ASN"
echo "  BGP Peering IP:  $BGP_PEERING"
echo "========================================"
