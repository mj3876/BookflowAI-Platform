// modules/logicapp-consumption-diag.bicep
// Logic Apps Consumption 워크플로 3개의 diagnosticSettings IaC
//   - la-bookflowmj-approval-request  (승인 요청)
//   - la-bookflowmj-stock-depart      (입고)
//   - la-bookflowmj-stock-arrival     (출고/도착)
//
// 배경: 이 3개는 Portal에서 직접 생성된 Consumption 워크플로(workflow 본체는 IaC 대상 아님).
// 진단 설정만 누락되어 있어 AzureDiagnostics(WorkflowRuntime) 로그가 law-bookflowmj 에
// 라우팅되지 않았고, 그 결과 row2_azure 대시보드의 "Logic Apps 워크플로 실행 현황" 표에
// 해당 3개가 표시되지 않는 문제가 발생했다.
//
// 본 모듈은 워크플로 자체는 건드리지 않고 (existing 참조) diagnosticSettings 만 추가한다.
//
// 적용:
//   az deployment group create -g rg-bookflow \
//     --template-file modules/logicapp-consumption-diag.bicep \
//     --parameters logAnalyticsWorkspaceId=<LAW_RESOURCE_ID>

param logAnalyticsWorkspaceId string

// 진단 설정 대상 워크플로 이름 (Portal에서 생성된 Consumption Logic Apps)
var consumptionWorkflows = [
  'la-bookflowmj-approval-request'
  'la-bookflowmj-stock-depart'
  'la-bookflowmj-stock-arrival'
]

// 기존 워크플로 참조 (existing) — 본체는 변경하지 않음
resource consumptionLogicApps 'Microsoft.Logic/workflows@2019-05-01' existing = [for wf in consumptionWorkflows: {
  name: wf
}]

// 각 워크플로에 diagnosticSettings 추가 (WorkflowRuntime + AllMetrics → law-bookflowmj)
resource diagConsumption 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = [for (wf, i) in consumptionWorkflows: {
  name: 'diag-${wf}'
  scope: consumptionLogicApps[i]
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      { category: 'WorkflowRuntime', enabled: true }
    ]
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
  }
}]
