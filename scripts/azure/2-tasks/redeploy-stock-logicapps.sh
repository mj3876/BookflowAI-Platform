#!/usr/bin/env bash
# redeploy-stock-logicapps.sh
#
# la-bookflowmj-stock-arrival / la-bookflowmj-stock-depart 강제 재배포
#
# 변경 내용 (2026-05-22):
#   Send_StockArrival / Send_StockDepart HTTP 액션
#   - timeout PT40S 추가 (per-call 타임아웃 — ACS 지연 시 무한 대기 방지)
#   - retryPolicy: count 2→1, interval PT15S→PT5S
#   - 최대 소요: 40s + 30s + 40s = 110s (Consumption LA 120s 제한 이내)
#   → Response_Failed ActionResponseTimedOut(504) 해소
#
# 사용법:
#   bash redeploy-stock-logicapps.sh

set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash path 변환 방지

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW_DIR="$(cd "${SCRIPT_DIR}/../../../infra/azure/workflows" && (pwd -W 2>/dev/null || pwd))"

RESOURCE_GROUP="rg-bookflow"
LOCATION="japanwest"
SUB_ID=$(az account show --query id --output tsv)

echo "================================================"
echo " la-bookflowmj-stock-arrival/depart 재배포"
echo "================================================"
echo "  Subscription: ${SUB_ID}"
echo "  Resource Group: ${RESOURCE_GROUP}"
echo ""

# ── 환경변수 조회 ──────────────────────────────────────
echo "[1/4] LogicApp Managed Identity 조회..."
export LOGICAPP_IDENTITY_ID
LOGICAPP_IDENTITY_ID=$(az identity show \
  --resource-group "$RESOURCE_GROUP" \
  --name "id-bookflowmj-logicapp" \
  --query id --output tsv 2>/dev/null || echo "")

if [[ -z "$LOGICAPP_IDENTITY_ID" ]]; then
  echo "  ✗ id-bookflowmj-logicapp 조회 실패 — az login 또는 리소스 이름 확인"
  exit 1
fi
echo "  ✓ ${LOGICAPP_IDENTITY_ID}"

echo ""
echo "[2/4] ACS 엔드포인트 / 발신 도메인 조회..."
ACS_ACCOUNT=$(az communication list \
  --resource-group "$RESOURCE_GROUP" \
  --query "[0].name" --output tsv 2>/dev/null || echo "")
ACS_HOST=$(az communication show \
  --name "$ACS_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query "hostName" --output tsv 2>/dev/null || echo "")
export ACS_EMAIL_URI="https://${ACS_HOST}/emails:send?api-version=2023-03-31"

ACS_EMAIL_SVC=$(az resource list \
  --resource-group "$RESOURCE_GROUP" \
  --resource-type "Microsoft.Communication/emailServices" \
  --query "[0].name" --output tsv 2>/dev/null || echo "")
ACS_DOMAIN=$(az rest --method GET \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Communication/emailServices/${ACS_EMAIL_SVC}/domains?api-version=2023-04-01" \
  --query "value[0].properties.mailFromSenderDomain" --output tsv 2>/dev/null || echo "")
export ACS_SENDER="DoNotReply@${ACS_DOMAIN}"

echo "  ACS Email URI : ${ACS_EMAIL_URI}"
echo "  ACS Sender    : ${ACS_SENDER}"

export DASHBOARD_URL="https://bookflow.myosoon.store"
export LOCATION

echo ""
echo "[3/4] Logic App 재배포 (Enabled 여부 무관 강제 PUT)..."

deploy_logicapp_force() {
  local la_name="$1"
  local template="$2"
  local tmp_file="/tmp/la-arm-${la_name}.json"

  if [[ ! -f "$template" ]]; then
    echo "  ✗ 템플릿 없음: $template"
    return 1
  fi

  envsubst '${LOCATION} ${LOGICAPP_IDENTITY_ID} ${ACS_EMAIL_URI} ${ACS_SENDER} ${DASHBOARD_URL}' < "$template" > "$tmp_file"
  echo ""
  echo "  배포 중: ${la_name} ..."

  if az rest --method PUT \
    --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Logic/workflows/${la_name}?api-version=2016-06-01" \
    --body "$(cat "${tmp_file}")" \
    --headers "Content-Type=application/json" \
    --output none 2>/tmp/la_deploy_err; then
    echo "  ✓ 배포 완료: ${la_name}"
  else
    echo "  ✗ 배포 실패: ${la_name}"
    cat /tmp/la_deploy_err | sed 's/^/    /'
    return 1
  fi
}

deploy_logicapp_force "la-bookflowmj-stock-arrival" "${WORKFLOW_DIR}/stock-arrival/arm-deploy.json"
deploy_logicapp_force "la-bookflowmj-stock-depart"  "${WORKFLOW_DIR}/stock-depart/arm-deploy.json"

echo ""
echo "[4/4] 배포 상태 확인..."
for la_name in la-bookflowmj-stock-arrival la-bookflowmj-stock-depart; do
  state=$(az rest --method GET \
    --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/${RESOURCE_GROUP}/providers/Microsoft.Logic/workflows/${la_name}?api-version=2016-06-01" \
    --query "properties.state" --output tsv 2>/dev/null || echo "조회실패")
  echo "  ${la_name}: ${state}"
done

echo ""
echo "================================================"
echo " 재배포 완료"
echo " retry: count=1, interval=PT30S, timeout=PT40S"
echo " 최대 소요 110s → 120s 제한 이내 보장"
echo "================================================"
