// Entra (Azure AD) Tenant Diagnostic Settings — SigninLogs / AuditLogs → Log Analytics workspace.
// 이 설정이 없으면 Grafana Row 2 "Entra OIDC 로그인 성공/실패" + Row 9 SCN-09/10 보안 시나리오 11 panel
// 이 모두 No-data (SigninLogs/AuditLogs 테이블이 빈 채로 유지).
//
// 배포 scope = tenant (Azure AD 자체) — main.bicep (resourceGroup scope) 와 별도 deployment.
// 배포 명령:
//   ws=$(az resource show -g rg-bookflow -n law-bookflowmj \
//        --resource-type Microsoft.OperationalInsights/workspaces --query id -o tsv)
//   az deployment tenant create \
//     --name bookflow-entra-diag \
//     --location koreacentral \
//     --template-file infra/azure/entra-diagnostic.bicep \
//     --parameters workspaceResourceId=$ws
//
// 필요 권한: Global Administrator 또는 Security Administrator (tenant-level).
// idempotent: 같은 name 으로 다시 배포 시 update.

targetScope = 'tenant'

@description('Log Analytics workspace 의 resource id · monitor 모듈의 output workspaceId 사용')
param workspaceResourceId string

@description('Diagnostic settings 이름 · 변경 시 새 settings 생성')
param diagnosticSettingsName string = 'bookflow-entra-diag'

resource entraDiagnostics 'Microsoft.aadiam/diagnosticSettings@2017-04-01-preview' = {
  name: diagnosticSettingsName
  scope: tenant()
  properties: {
    workspaceId: workspaceResourceId
    logs: [
      // 대화형 user signin (Entra OIDC) — Row 2 + Row 9 SCN-09 핵심
      { category: 'SignInLogs', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
      // 디렉토리·정책·SP 변경 — Row 9 SCN-10 핵심
      { category: 'AuditLogs', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
      // 비대화형 signin (refresh token · token refresh)
      { category: 'NonInteractiveUserSignInLogs', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
      // Service Principal signin (앱 · 매니지드 ID)
      { category: 'ServicePrincipalSignInLogs', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
      // 관리형 ID signin
      { category: 'ManagedIdentitySignInLogs', enabled: true, retentionPolicy: { enabled: false, days: 0 } }
      // SP credential 사용 이벤트 (SCN-10 관련)
      { category: 'ADFSSignInLogs', enabled: false, retentionPolicy: { enabled: false, days: 0 } }
    ]
  }
}

output diagnosticSettingsId string = entraDiagnostics.id
