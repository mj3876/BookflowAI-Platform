#!/bin/bash
# scripts/cleanup-selective.sh
#   —    
#
#  (   ):
#   - Entra ID /  : BookFlow-Internal, BF-* 
#   - Public IP 2     : pip-bookflow-vpngw-active/standby
#
#   ( ):
#   VPN Connection → Local NW GW → VPN Gateway
#   → Logic Apps → Event Grid → Function App → App Svc Plan → Storage
#   → Key Vault → Log Analytics → VNet → NSG →  ID

set -e
export MSYS_NO_PATHCONV=1

RESOURCE_GROUP="rg-bookflow"
PREFIX="bookflow01"

echo "========================================"
echo " BOOKFLOW   "
echo "========================================"
echo ""
echo ":"
echo "  ✓ Entra ID   : BookFlow-Internal"
echo "  ✓ Entra ID : BF-HeadQuarter / BF-Logistics / BF-Branch / BF-Admin"
echo "  ✓ Public IP    : pip-${PREFIX}-vpngw-active"
echo "  ✓ Public IP    : pip-${PREFIX}-vpngw-standby"
echo ""
echo " :"
echo "  VPN Connection, Local NW GW, VPN Gateway"
echo "  Logic Apps x2, Event Grid, Function App"
echo "  App Svc Plan, Storage Account"
echo "  Key Vault (soft-delete →    )"
echo "  Log Analytics, VNet, NSG x2,  ID x2"
echo ""
echo " Enter,  Ctrl+C"
read

echo ""
echo "[0]  "
az account show --output table
echo ""
echo " Enter,  Ctrl+C"
read

#    
_exists() { [ -n "$1" ] && [ "$1" != "None" ]; }

# ── 1. VPN Connection ─────────────────────────────────────
echo ""
echo "[1] VPN Connection "
VAL=$(az network vpn-connection show \
  --resource-group "$RESOURCE_GROUP" \
  --name "conn-${PREFIX}-aws-active" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az network vpn-connection delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "conn-${PREFIX}-aws-active"
  echo "  ✓ : conn-${PREFIX}-aws-active"
else
  echo "  : conn-${PREFIX}-aws-active "
fi

# ── 2. Local Network Gateway ──────────────────────────────
echo ""
echo "[2] Local Network Gateway "
VAL=$(az network local-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "lng-${PREFIX}-aws-active" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az network local-gateway delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "lng-${PREFIX}-aws-active"
  echo "  ✓ : lng-${PREFIX}-aws-active"
else
  echo "  : lng-${PREFIX}-aws-active "
fi

# ── 3. VPN Gateway (PIP ) ─────────────────────────────
echo ""
echo "[3] VPN Gateway  (PIP )"
VAL=$(az network vnet-gateway show \
  --resource-group "$RESOURCE_GROUP" \
  --name "vpngw-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  echo "    (10~20 )..."
  az network vnet-gateway delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "vpngw-${PREFIX}"
  echo "  ✓ : vpngw-${PREFIX}"
else
  echo "  : vpngw-${PREFIX} "
fi

echo ""
echo "  [PIP  ]"
for PIP in "pip-${PREFIX}-vpngw-active" "pip-${PREFIX}-vpngw-standby"; do
  IP=$(az network public-ip show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$PIP" \
    --query ipAddress --output tsv 2>/dev/null || echo "")
  echo "  ✓ : $PIP = $IP"
done

# ── 4. Logic Apps ─────────────────────────────────────────
echo ""
echo "[4] Logic Apps "
for LA in "la-${PREFIX}-notification" "la-${PREFIX}-secret-rotation"; do
  VAL=$(az logic workflow show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$LA" \
    --query name --output tsv 2>/dev/null || echo "")
  if _exists "$VAL"; then
    az logic workflow delete \
      --resource-group "$RESOURCE_GROUP" \
      --name "$LA" \
      --yes
    echo "  ✓ : $LA"
  else
    echo "  : $LA "
  fi
done

# ── 5. Event Grid System Topic ────────────────────────────
echo ""
echo "[5] Event Grid "
VAL=$(az eventgrid system-topic show \
  --resource-group "$RESOURCE_GROUP" \
  --name "egt-${PREFIX}-keyvault" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az eventgrid system-topic delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "egt-${PREFIX}-keyvault" \
    --yes
  echo "  ✓ : egt-${PREFIX}-keyvault"
else
  echo "  : egt-${PREFIX}-keyvault "
fi

# ── 6. Function App ───────────────────────────────────────
echo ""
echo "[6] Function App "
VAL=$(az functionapp show \
  --resource-group "$RESOURCE_GROUP" \
  --name "func-${PREFIX}-sync" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az functionapp delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "func-${PREFIX}-sync"
  echo "  ✓ : func-${PREFIX}-sync"
else
  echo "  : func-${PREFIX}-sync "
fi

# ── 7. App Service Plan ───────────────────────────────────
echo ""
echo "[7] App Service Plan "
VAL=$(az appservice plan show \
  --resource-group "$RESOURCE_GROUP" \
  --name "asp-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az appservice plan delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "asp-${PREFIX}" \
    --yes
  echo "  ✓ : asp-${PREFIX}"
else
  echo "  : asp-${PREFIX} "
fi

# ── 8. Storage Account ────────────────────────────────────
echo ""
echo "[8] Storage Account "
ST_NAME="st${PREFIX//-/}func"
VAL=$(az storage account show \
  --resource-group "$RESOURCE_GROUP" \
  --name "$ST_NAME" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az storage account delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ST_NAME" \
    --yes
  echo "  ✓ : $ST_NAME"
else
  echo "  : $ST_NAME "
fi

# ── 9. Key Vault (soft-delete ) ───────────────────────
echo ""
echo "[9] Key Vault  (soft-delete —  ,    )"
VAL=$(az keyvault show \
  --resource-group "$RESOURCE_GROUP" \
  --name "kv-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az keyvault delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "kv-${PREFIX}"
  echo "  ✓ soft-delete : kv-${PREFIX} (90 ,  )"
else
  echo "  : kv-${PREFIX}  ( soft-delete   )"
fi

# ── 10. Log Analytics ─────────────────────────────────────
echo ""
echo "[10] Log Analytics Workspace "
VAL=$(az monitor log-analytics workspace show \
  --resource-group "$RESOURCE_GROUP" \
  --workspace-name "law-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az monitor log-analytics workspace delete \
    --resource-group "$RESOURCE_GROUP" \
    --workspace-name "law-${PREFIX}" \
    --force \
    --yes
  echo "  ✓ : law-${PREFIX}"
else
  echo "  : law-${PREFIX} "
fi

# ── 11. VNet ──────────────────────────────────────────────
echo ""
echo "[11] VNet "
VAL=$(az network vnet show \
  --resource-group "$RESOURCE_GROUP" \
  --name "vnet-${PREFIX}" \
  --query name --output tsv 2>/dev/null || echo "")
if _exists "$VAL"; then
  az network vnet delete \
    --resource-group "$RESOURCE_GROUP" \
    --name "vnet-${PREFIX}"
  echo "  ✓ : vnet-${PREFIX}"
else
  echo "  : vnet-${PREFIX} "
fi

# ── 12. NSG ───────────────────────────────────────────────
echo ""
echo "[12] NSG "
for NSG in "nsg-${PREFIX}-services" "nsg-${PREFIX}-function"; do
  VAL=$(az network nsg show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$NSG" \
    --query name --output tsv 2>/dev/null || echo "")
  if _exists "$VAL"; then
    az network nsg delete \
      --resource-group "$RESOURCE_GROUP" \
      --name "$NSG"
    echo "  ✓ : $NSG"
  else
    echo "  : $NSG "
  fi
done

# ── 13.  ID ───────────────────────────────────────────
echo ""
echo "[13]  ID "
for ID_NAME in "id-${PREFIX}-function" "id-${PREFIX}-logicapp"; do
  VAL=$(az identity show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$ID_NAME" \
    --query name --output tsv 2>/dev/null || echo "")
  if _exists "$VAL"; then
    az identity delete \
      --resource-group "$RESOURCE_GROUP" \
      --name "$ID_NAME"
    echo "  ✓ : $ID_NAME"
  else
    echo "  : $ID_NAME "
  fi
done

# ── 14. ARM    ───────────────────────────────
# deploy-all.sh check_deployed()     .
#       " "   .
echo ""
echo "[14] ARM   "
for DEPLOY in identity-deploy nsg-deploy monitor-deploy vnet-deploy \
              keyvault-deploy function-deploy eventgrid-deploy \
              logicapp-deploy vpn-deploy; do
  STATE=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DEPLOY" \
    --query properties.provisioningState \
    --output tsv 2>/dev/null || echo "")
  if [ -n "$STATE" ]; then
    az deployment group delete \
      --resource-group "$RESOURCE_GROUP" \
      --name "$DEPLOY" \
      --no-wait
    echo "  ✓  : $DEPLOY"
  else
    echo "  : $DEPLOY  "
  fi
done

# ──   ─────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════"
echo "  "
echo "════════════════════════════════════════"

echo ""
echo "[ 1]     "
az resource list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[].{type:type, name:name}" \
  --output table

echo ""
echo "[ 2] PIP "
for PIP in "pip-${PREFIX}-vpngw-active" "pip-${PREFIX}-vpngw-standby"; do
  IP=$(az network public-ip show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$PIP" \
    --query ipAddress --output tsv 2>/dev/null || echo "❌ ")
  echo "  $PIP = $IP"
done

echo ""
echo "[ 3] Entra ID  "
APP_ID=$(az ad app list \
  --display-name "BookFlow-Internal" \
  --query "[0].appId" --output tsv 2>/dev/null || echo "❌ ")
echo "  BookFlow-Internal App ID = $APP_ID"

echo ""
echo "========================================"
echo "   "
echo "========================================"
echo ""
echo " :"
echo "  bash scripts/deploy-all.sh"
echo "    → Key Vault   ( )"
echo "  VPN  :"
echo "    bash scripts/deploy-vpn.sh  ( PIP  → IP  )"
