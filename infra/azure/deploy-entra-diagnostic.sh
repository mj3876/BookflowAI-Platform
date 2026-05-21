#!/bin/bash
# Entra Tenant Diagnostic Settings IaC 적용.
# SigninLogs / AuditLogs / NonInteractiveUserSignInLogs / ServicePrincipalSignInLogs /
# ManagedIdentitySignInLogs 를 Log Analytics workspace (law-bookflowmj) 로 stream.
#
# 필요 권한: Global Administrator 또는 Security Administrator (Entra tenant 수준).
# 적용 후 효과:
#   - Row 2 "Entra OIDC 로그인 성공/실패" panel 데이터 시작
#   - Row 9 SCN-09 (brute-force) · SCN-10 (권한 escalation) 11 panel 데이터 시작
# 주의: forward-only · 과거 활동 backfill 안 됨. 적용 직후 새 signin/audit 부터 잡힘.

set -e

RG="${RG:-rg-bookflow}"
WORKSPACE_NAME="${WORKSPACE_NAME:-law-bookflowmj}"
DEPLOYMENT_NAME="${DEPLOYMENT_NAME:-bookflow-entra-diag-$(date +%s)}"
LOCATION="${LOCATION:-koreacentral}"

# 1. Log Analytics workspace resource id 조회
ws=$(az resource show \
       -g "$RG" \
       -n "$WORKSPACE_NAME" \
       --resource-type Microsoft.OperationalInsights/workspaces \
       --query id -o tsv)

if [ -z "$ws" ]; then
    echo "ERROR: Log Analytics workspace $RG/$WORKSPACE_NAME 못 찾음" >&2
    exit 1
fi

echo "workspace resource id: $ws"

# 2. tenant-scope 배포
az deployment tenant create \
    --name "$DEPLOYMENT_NAME" \
    --location "$LOCATION" \
    --template-file "$(dirname "$0")/entra-diagnostic.bicep" \
    --parameters workspaceResourceId="$ws"

echo ""
echo "✓ Entra Diagnostic Settings 적용 완료."
echo "검증: 5~15분 후 SigninLogs / AuditLogs 테이블에 신규 row 적재 시작."
echo "      Grafana Row 2 + Row 9 보안 panel 자동 회복 (refresh 시)."
