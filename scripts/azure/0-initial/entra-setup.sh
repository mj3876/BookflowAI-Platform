#!/bin/bash
# scripts/entra-setup.sh
# Entra ID App + Service Principal + redirect URI + Microsoft Graph permissions
# + Client Secret 발급 + Key Vault 저장 + AWS Secrets Manager 동기화 (idempotent)
#
# 첫 실행: 신규 생성. 재실행: 기존 fetch + redirect URI patch + Secret rotate + Vault PUT.
# 영구 자원: BookFlow-Internal App (절대 삭제 금지)
# Logic App `la-bookflow-secret-rotation` 가 30일마다 자동 rotate (이 스크립트는 1회 + 비상 rotate 만)

set -e

PREFIX="bookflowmj"  # Bicep 의 prefix 와 일치 (kv-${PREFIX})
APP_NAME="BookFlow-Internal"
REDIRECT_URIS_JSON='["https://auth.bookflow.internal/callback","https://bookflow.duckdns.org/auth/callback"]'

echo "========================================"
echo " Entra ID   "
echo "========================================"

# ── 1. App 등록 (idempotent: 있으면 fetch · 없으면 create) ───────────
echo ""
echo "[1] App 등록"
APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)
if [ -z "$APP_ID" ] || [ "$APP_ID" = "null" ]; then
  APP_ID=$(az ad app create \
    --display-name "$APP_NAME" \
    --sign-in-audience AzureADMyOrg \
    --query appId --output tsv)
  echo "Created App: $APP_ID"
else
  echo "Existing App: $APP_ID"
fi

# ── 2. Service Principal (idempotent) ─────────────────────────────
echo ""
echo "[2] Service Principal"
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv 2>/dev/null || true)
if [ -z "$SP_ID" ] || [ "$SP_ID" = "null" ]; then
  az ad sp create --id $APP_ID >/dev/null
  echo "ServicePrincipal created"
else
  echo "ServicePrincipal exists: $SP_ID"
fi

# ── 3. Redirect URI · multi (idempotent · Graph PATCH) ─────────────
echo ""
echo "[3] Redirect URIs: $REDIRECT_URIS_JSON"
APP_OBJECT_ID=$(az ad app show --id "$APP_ID" --query id -o tsv)
az rest --method PATCH \
  --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
  --body "{\"web\":{\"redirectUris\":$REDIRECT_URIS_JSON,\"implicitGrantSettings\":{\"enableIdTokenIssuance\":true}}}" >/dev/null
echo "Redirect URIs synced"

# ── 4. Microsoft Graph permissions: openid · profile · email (idempotent) ──
echo ""
echo "[4] Graph permissions"
for PERM in e1fe6dd8-ba31-4d61-89e7-88639da4683d 37f7f235-527c-4136-accd-4a02d197296e 14dad69e-099b-42c9-810b-d002981feec1; do
  az ad app permission add --id $APP_ID \
    --api 00000003-0000-0000-c000-000000000000 \
    --api-permissions $PERM=Scope 2>/dev/null || true
done

# ── 5. Client Secret rotate (--append: 기존 secret 유지하며 새 secret 추가) ────
echo ""
echo "[5] Client Secret rotate (append)"
CLIENT_SECRET=$(az ad app credential reset --id $APP_ID --append --years 1 --query password --output tsv)
TENANT_ID=$(az account show --query tenantId --output tsv)
echo "Secret length: ${#CLIENT_SECRET} chars"

# ── 6. Key Vault PUT (idempotent · 매 실행 갱신) ──────────────────────
echo ""
echo "[6] Key Vault PUT (kv-${PREFIX})"
EXPIRES=$(date -u -d "+1 year" '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -v+1y '+%Y-%m-%dT%H:%M:%SZ')
az keyvault secret set --vault-name "kv-${PREFIX}" --name "bookflow-tenant-id" --value "$TENANT_ID" --expires "$EXPIRES" >/dev/null
az keyvault secret set --vault-name "kv-${PREFIX}" --name "bookflow-client-id" --value "$APP_ID" --expires "$EXPIRES" >/dev/null
az keyvault secret set --vault-name "kv-${PREFIX}" --name "bookflow-client-secret" --value "$CLIENT_SECRET" --expires "$EXPIRES" >/dev/null
echo "✓ tenant-id · client-id · client-secret"

# ── 7. AWS Secrets Manager 동기화 (Logic App rotation 가 30일마다 자동 처리하지만 초기 sync) ──
echo ""
echo "[7] AWS Secrets Manager sync"
PAYLOAD=$(printf '{"client_id":"%s","tenant_id":"%s","client_secret":"%s"}' "$APP_ID" "$TENANT_ID" "$CLIENT_SECRET")
aws secretsmanager put-secret-value --secret-id bookflow/auth/entra-client-secret --secret-string "$PAYLOAD" >/dev/null
echo "✓ AWS Secrets Manager bookflow/auth/entra-client-secret"

# ── 8. Entra ID 그룹 (idempotent · BF-* 4종) ─────────────────────────
echo ""
echo "[8] Entra ID groups"
for G in BF-HeadQuarter BF-Logistics BF-Branch BF-Admin; do
  if [ -z "$(az ad group list --display-name "$G" --query "[0].id" -o tsv 2>/dev/null)" ]; then
    az ad group create --display-name "$G" --mail-nickname "$G" >/dev/null
    echo "  + $G (created)"
  else
    echo "  · $G (exists)"
  fi
done

# ── 9. .env.local append (idempotent) ────────────────────────────────
echo ""
echo "[9] .env.local"
ENV_LOCAL="$(dirname "$0")/../../aws/config/.env.local"
touch "$ENV_LOCAL"
TMP=$(mktemp)
grep -v -E "^BOOKFLOW_ENTRA_(CLIENT_ID|TENANT_ID)=" "$ENV_LOCAL" > "$TMP" || true
mv "$TMP" "$ENV_LOCAL"
echo "BOOKFLOW_ENTRA_CLIENT_ID=$APP_ID" >> "$ENV_LOCAL"
echo "BOOKFLOW_ENTRA_TENANT_ID=$TENANT_ID" >> "$ENV_LOCAL"
echo "✓ .env.local updated"

echo ""
echo "========================================"
echo " Entra ID 셋업 완료"
echo "========================================"
echo "  CLIENT_ID:  $APP_ID"
echo "  TENANT_ID:  $TENANT_ID"
echo "  KV:         kv-${PREFIX}"
echo "  AWS:        bookflow/auth/entra-client-secret"
echo ""
echo "이후 30일마다 Logic App la-bookflow-secret-rotation 가 자동 rotate."
