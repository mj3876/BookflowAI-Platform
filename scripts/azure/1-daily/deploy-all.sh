#!/bin/bash
# scripts/deploy-all.sh
# Day 1~3    ( , idempotent)
#       .

set -e
export MSYS_NO_PATHCONV=1   # Git Bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BICEP_DIR="$(cd "${SCRIPT_DIR}/../../../infra/azure" && (pwd -W 2>/dev/null || pwd))"

RESOURCE_GROUP="rg-bookflow"
LOCATION="japanwest"
PREFIX="bookflow01"

# ──   ─────────────────────────────────────────────

# 1. Bicep   (, az bicep build)
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

# 2. Azure    (az deployment group validate)
validate_deployment() {
  local deploy_name=$1
  shift
  echo "  [] Azure  : $deploy_name"
  local result
  if ! result=$(az deployment group validate \
    --resource-group "$RESOURCE_GROUP" \
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

# ARM    
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

#      
deploy_stack() {
  local deploy_name=$1
  local template_file=""
  local args=("$@")

  # --template-file   ( )
  for i in "${!args[@]}"; do
    if [ "${args[$i]}" = "--template-file" ]; then
      template_file="${args[$((i+1))]}"
    fi
  done

  if check_deployed "$deploy_name"; then
    echo "  : $deploy_name   "
    return 0
  fi

  #  + Azure 
  [ -n "$template_file" ] && validate_bicep_syntax "$template_file" || return 1
  validate_deployment "$deploy_name" "${args[@]:1}" || return 1

  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$deploy_name" \
    --output table \
    "${args[@]:1}"
  echo "  : $deploy_name"
}

# ──  ──────────────────────────────────────────────────
echo "========================================"
echo " BOOKFLOW Azure   (Day 1~3)"
echo "========================================"
echo ""
echo "[0]   "
az account show --output table
echo ""
echo "  .  Enter,  Ctrl+C"
read

MY_OBJECT_ID=$(az ad signed-in-user show --query id --output tsv)

# ════════════════════════════════════════════
# STACK 1: Foundation
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 1] Foundation"
echo "════════════════════════════════════════"

# Resource Group
echo ""
echo "[1-1] Resource Group /"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output table

# ARM 배포 이력 초기화 (PREFIX 변경·완전 재배포 시 스킵 방지)
echo ""
echo "[1-0] ARM 배포 이력 초기화"
for DEPLOY_NAME in identity-deploy nsg-deploy monitor-deploy vnet-deploy \
                   keyvault-deploy function-deploy eventgrid-deploy \
                   logicapp-deploy vpn-deploy; do
  az deployment group delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DEPLOY_NAME" 2>/dev/null && echo "  삭제: $DEPLOY_NAME" || true
done

# Identity
echo ""
echo "[1-2]  ID "
deploy_stack "identity-deploy" \
  --template-file "${BICEP_DIR}/modules/identity.bicep" \
  --parameters location="$LOCATION" prefix="$PREFIX"

FUNCTION_IDENTITY_ID=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-function" \
  --query id --output tsv)
FUNCTION_IDENTITY_PRINCIPAL=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-function" \
  --query principalId --output tsv)
FUNCTION_IDENTITY_CLIENT_ID=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-function" \
  --query clientId --output tsv)
LOGICAPP_IDENTITY_ID=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-logicapp" \
  --query id --output tsv)
LOGICAPP_IDENTITY_PRINCIPAL=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-logicapp" \
  --query principalId --output tsv)
LOGICAPP_IDENTITY_CLIENT_ID=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-${PREFIX}-logicapp" \
  --query clientId --output tsv)
echo "  Function Identity ID: $FUNCTION_IDENTITY_ID"
echo "  LogicApp Identity ID: $LOGICAPP_IDENTITY_ID"

# NSG
echo ""
echo "[1-3] NSG "
deploy_stack "nsg-deploy" \
  --template-file "${BICEP_DIR}/modules/nsg.bicep" \
  --parameters location="$LOCATION" prefix="$PREFIX"

SERVICES_NSG_ID=$(az network nsg show \
  --resource-group "$RESOURCE_GROUP" \
  --name "nsg-${PREFIX}-services" \
  --query id --output tsv)
FUNCTION_NSG_ID=$(az network nsg show \
  --resource-group "$RESOURCE_GROUP" \
  --name "nsg-${PREFIX}-function" \
  --query id --output tsv)

# Monitor
echo ""
echo "[1-4] Log Analytics Workspace "
deploy_stack "monitor-deploy" \
  --template-file "${BICEP_DIR}/modules/monitor.bicep" \
  --parameters location="$LOCATION" prefix="$PREFIX" logRetentionDays=90

LOG_ANALYTICS_ID=$(az monitor log-analytics workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "law-${PREFIX}" \
  --query id --output tsv)
echo "  Log Analytics ID: $LOG_ANALYTICS_ID"

# VNet
echo ""
echo "[1-5] VNet "
deploy_stack "vnet-deploy" \
  --template-file "${BICEP_DIR}/modules/vnet.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              vnetAddressPrefix="172.16.0.0/16" \
              gatewaySubnetPrefix="172.16.1.0/27" \
              servicesSubnetPrefix="172.16.2.0/24" \
              functionSubnetPrefix="172.16.3.0/24" \
              servicesNsgId="$SERVICES_NSG_ID" \
              functionNsgId="$FUNCTION_NSG_ID"

GATEWAY_SUBNET_ID=$(az network vnet subnet show \
  --resource-group "$RESOURCE_GROUP" \
  --vnet-name "vnet-${PREFIX}" \
  --name GatewaySubnet \
  --query id --output tsv)
FUNCTION_SUBNET_ID=$(az network vnet subnet show \
  --resource-group "$RESOURCE_GROUP" \
  --vnet-name "vnet-${PREFIX}" \
  --name snet-function \
  --query id --output tsv)

# GatewaySubnet NSG  
GATEWAY_NSG=$(az network vnet subnet show \
  --resource-group "$RESOURCE_GROUP" \
  --vnet-name "vnet-${PREFIX}" \
  --name GatewaySubnet \
  --query networkSecurityGroup --output tsv 2>/dev/null || echo "")
if [ -z "$GATEWAY_NSG" ]; then
  echo "  ✓ GatewaySubnet NSG  "
else
  echo "  ✗ : GatewaySubnet  NSG "
fi

# ════════════════════════════════════════════
# STACK 2: Security
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 2] Security"
echo "════════════════════════════════════════"

echo ""
echo "[2-1] Key Vault "
SECURITY_ADMIN_OBJECT_ID="$MY_OBJECT_ID"

# Soft-delete  Key Vault  (purge protection   )
KV_NAME="kv-${PREFIX}"
DELETED_KV=$(az keyvault list-deleted \
  --query "[?name=='${KV_NAME}'].name" \
  --output tsv 2>/dev/null || echo "")
if [ -n "$DELETED_KV" ]; then
  echo "  []   Key Vault : $KV_NAME →  ..."
  az keyvault recover --name "$KV_NAME" --location "$LOCATION"
  echo "  ✓ Key Vault  "
fi

deploy_stack "keyvault-deploy" \
  --template-file "${BICEP_DIR}/modules/keyvault.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              logAnalyticsWorkspaceId="$LOG_ANALYTICS_ID" \
              functionIdentityPrincipalId="$FUNCTION_IDENTITY_PRINCIPAL" \
              logicappIdentityPrincipalId="$LOGICAPP_IDENTITY_PRINCIPAL" \
              securityAdminObjectId="$SECURITY_ADMIN_OBJECT_ID"

KV_URI=$(az keyvault show \
  --resource-group "$RESOURCE_GROUP" \
  --name "kv-${PREFIX}" \
  --query properties.vaultUri --output tsv)
KV_ID=$(az keyvault show \
  --resource-group "$RESOURCE_GROUP" \
  --name "kv-${PREFIX}" \
  --query id --output tsv)
echo "  Key Vault URI: $KV_URI"

# ════════════════════════════════════════════
# STACK 3: Compute
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 3] Compute"
echo "════════════════════════════════════════"

echo ""
echo "[3-1] Function App "
deploy_stack "function-deploy" \
  --template-file "${BICEP_DIR}/modules/function.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              keyVaultUri="$KV_URI" \
              functionIdentityId="$FUNCTION_IDENTITY_ID" \
              functionIdentityClientId="$FUNCTION_IDENTITY_CLIENT_ID" \
              logAnalyticsWorkspaceId="$LOG_ANALYTICS_ID"

FUNCTION_APP_ID=$(az functionapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "func-${PREFIX}-sync" \
  --query id --output tsv)

echo ""
echo "[3-2] Function  "
if [ -d "functions/sync-secret" ]; then
  if check_deployed "function-deploy"; then
    # Function App SCM    3 
    echo "  [] Function App   ..."
    for i in $(seq 1 18); do
      STATUS=$(az functionapp show \
        --resource-group "$RESOURCE_GROUP" \
        --name "func-${PREFIX}-sync" \
        --query state --output tsv 2>/dev/null || echo "Unknown")
      if [ "$STATUS" = "Running" ]; then
        echo "  ✓ Function App   (${i} )"
        break
      fi
      echo "  ...   ($((i*10))s, : $STATUS)"
      sleep 10
    done

    cd functions/sync-secret
    # --build remote: Azure  →  Python    
    func azure functionapp publish "func-${PREFIX}-sync" --python --build remote
    cd ../..
    echo "  : Function  "
  fi
else
  echo "  : functions/sync-secret  "
fi

# ════════════════════════════════════════════
# STACK 4: Integration
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 4] Integration"
echo "════════════════════════════════════════"

echo ""
echo "[4-1] Event Grid "
deploy_stack "eventgrid-deploy" \
  --template-file "${BICEP_DIR}/modules/eventgrid.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              keyVaultId="$KV_ID"

# ════════════════════════════════════════════
# STACK 5: Automation
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 5] Automation"
echo "════════════════════════════════════════"

echo ""
echo "[5-1] Logic Apps "
deploy_stack "logicapp-deploy" \
  --template-file "${BICEP_DIR}/modules/logicapp.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              logicappIdentityId="$LOGICAPP_IDENTITY_ID" \
              logicappIdentityClientId="$LOGICAPP_IDENTITY_CLIENT_ID" \
              keyVaultUri="$KV_URI" \
              logAnalyticsWorkspaceId="$LOG_ANALYTICS_ID"

# ════════════════════════════════════════════
# STACK 6: Network (VPN Gateway, 30~45)
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo " [STACK 6] Network (VPN Gateway)"
echo "════════════════════════════════════════"
echo ""
echo "VPN Gateway  30~45 ."
echo " Enter,  Ctrl+C   "
read

echo ""
echo "[6-1] VPN Gateway "
deploy_stack "vpn-deploy" \
  --template-file "${BICEP_DIR}/modules/vpn.bicep" \
  --parameters location="$LOCATION" \
              prefix="$PREFIX" \
              gatewaySubnetId="$GATEWAY_SUBNET_ID" \
              vpnBgpAsn=65001

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

# ════════════════════════════════════════════
#  
# ════════════════════════════════════════════
echo ""
echo "════════════════════════════════════════"
echo "  "
echo "════════════════════════════════════════"

echo ""
echo "[ 1]   "
az resource list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[].{type:type, name:name}" \
  --output table

echo ""
echo "[ 2] Key Vault RBAC "
az role assignment list --scope "$KV_ID" --output table

echo ""
echo "[ 3] Logic Apps "
az logic workflow list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[].{name:name, state:state}" \
  --output table

echo ""
echo "========================================"
echo "   "
echo "========================================"
echo ""
echo "  :"
echo "  1. Azure Portal → la-${PREFIX}-notification: Teams·Outlook  "
echo "  2. Azure Portal → la-${PREFIX}-secret-rotation: Outlook  "
echo "  3. Entra ID  : bash scripts/entra-setup.sh"
echo "  4. VPN  : bash scripts/vpn-connect.sh"
