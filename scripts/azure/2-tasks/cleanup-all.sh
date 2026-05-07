#!/bin/bash
# scripts/cleanup-all.sh
#     
# : Resource Group  + Entra ID /

set -e
export MSYS_NO_PATHCONV=1

RESOURCE_GROUP="rg-bookflow"
PREFIX="bookflow01"

echo "========================================"
echo " BOOKFLOW Azure   "
echo "========================================"
echo ""
echo " :"
echo "  [Azure]"
echo "  - Resource Group : $RESOURCE_GROUP (  )"
echo "    ├── VPN Gateway      : vpngw-${PREFIX}"
echo "    ├── Public IP        : pip-${PREFIX}-vpngw-active/standby"
echo "    ├── Logic Apps       : la-${PREFIX}-notification, la-${PREFIX}-secret-rotation"
echo "    ├── Event Grid       : egt-${PREFIX}-keyvault"
echo "    ├── Function App     : func-${PREFIX}-sync"
echo "    ├── App Service Plan : asp-${PREFIX}"
echo "    ├── Storage Account  : stbookflowfunc"
echo "    ├── Key Vault        : kv-${PREFIX} (soft-delete 90 )"
echo "    ├── Log Analytics    : law-${PREFIX}"
echo "    ├── VNet             : vnet-${PREFIX}"
echo "    ├── NSG              : nsg-${PREFIX}-services/function"
echo "    └──  ID          : id-${PREFIX}-function/logicapp"
echo ""
echo "  [Entra ID] (entra-setup.sh  )"
echo "  -    : BookFlow-Internal"
echo "  -      : BF-HeadQuarter, BF-Logistics, BF-Branch, BF-Admin"
echo ""
echo "⚠️  Key Vault Purge Protection  90 soft-delete  "
echo "       90  "
echo ""
echo " Enter,  Ctrl+C"
read

# ── 0.   ───────────────────────────────────────────
echo ""
echo "[0]   "
az account show --output table
echo ""
echo "   .   Enter,  Ctrl+C"
read

# ── 1. Resource Group   ───────────────────────────
echo ""
echo "[1] Resource Group  "
RG_EXISTS=$(az group exists --name "$RESOURCE_GROUP")
if [ "$RG_EXISTS" = "false" ]; then
  echo "  Resource Group '$RESOURCE_GROUP'   ."
else
  echo "   : $RESOURCE_GROUP (5~15 )"
  az group delete \
    --name "$RESOURCE_GROUP" \
    --yes \
    --no-wait
  echo "     —   "

  echo "     ..."
  az group wait \
    --name "$RESOURCE_GROUP" \
    --deleted \
    --timeout 900
  echo "  ✓ Resource Group  "
fi

# ── 2. Key Vault soft-delete   ────────────────────
echo ""
echo "[2] Key Vault soft-delete  "
KV_DELETED=$(az keyvault list-deleted \
  --query "[?name=='kv-${PREFIX}'].name" \
  --output tsv 2>/dev/null || echo "")
if [ -n "$KV_DELETED" ]; then
  echo "  ℹ️  kv-${PREFIX}: soft-delete  (90  )"
  echo "     deploy-all.sh     (purge protection  , recovery )"
else
  echo "  ✓ soft-delete  "
fi

# ── 3. Entra ID   ───────────────────────────────────
echo ""
echo "[3] Entra ID   "
APP_ID=$(az ad app list \
  --display-name "BookFlow-Internal" \
  --query "[0].appId" --output tsv 2>/dev/null || echo "")
if [ -n "$APP_ID" ] && [ "$APP_ID" != "None" ]; then
  az ad app delete --id "$APP_ID"
  echo "  ✓ BookFlow-Internal   "
else
  echo "  : BookFlow-Internal  "
fi

# ── 4. Entra ID   ─────────────────────────────────
echo ""
echo "[4] Entra ID  "
for GROUP in "BF-HeadQuarter" "BF-Logistics" "BF-Branch" "BF-Admin"; do
  GROUP_ID=$(az ad group show \
    --group "$GROUP" \
    --query id --output tsv 2>/dev/null || echo "")
  if [ -n "$GROUP_ID" ]; then
    az ad group delete --group "$GROUP_ID"
    echo "  ✓ $GROUP  "
  else
    echo "  : $GROUP "
  fi
done

# ── 5.   ──────────────────────────────────────────
echo ""
echo "[5]  "
RG_AFTER=$(az group exists --name "$RESOURCE_GROUP")
if [ "$RG_AFTER" = "false" ]; then
  echo "  ✓ Resource Group  "
else
  echo "  ✗ Resource Group   — Portal   "
fi

echo ""
echo "========================================"
echo "    "
echo "========================================"
