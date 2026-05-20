"""Row 2 — Azure (Azure Monitor) 대시보드.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 2) 기준:
  - Entra ID: auth-pod OIDC 로그인 성공/실패
  - Logic Apps ×6: 실행·성공/실패 (notification 메일 발송률)
  - Function App: 호출·에러
  - Event Grid: 이벤트 처리 수
  - Key Vault: 접근·시크릿 만료 임박

데이터소스: Azure Monitor (고정 UID `azure-monitor`).
구독 / 리소스 그룹은 라이브 Azure(2026-05-19 실측)에서 확인:
  - subscription : e98a94bb-7532-4e49-8a36-bc42e30d5a81
  - resourceGroup: rg-bookflow (japanwest)

메트릭은 라이브 Azure Monitor 의 metricDefinitions 로 실재 확인했다.
Logic Apps 전체 현황은 단일 메트릭으로 표현 불가하므로 Log Analytics
워크스페이스(law-bookflowmj)의 AzureDiagnostics(WorkflowRuntime) 를
KQL 로 집계해 6개 워크플로를 한 표로 보여준다.

NOTE: 표에 표시되려면 각 워크플로에 diagnosticSettings(WorkflowRuntime →
law-bookflowmj)이 설정되어 있어야 한다. Consumption 3개
(approval-request·stock-depart·stock-arrival) 의 진단 설정은
infra/azure/modules/logicapp-consumption-diag.bicep 으로 IaC 화 되어 있다.

새 Row 모듈 패턴(README §"새 Row 추가 패턴")을 따른다:
  1. lib.meta.base_dashboard() 로 시작
  2. lib.panels.* 헬퍼로 패널 생성
  3. lib.datasources.ref() 로 데이터소스 지정
  4. dashboard() 함수 하나를 export (build.py 가 호출)
"""

from grafana_foundation_sdk.builders.azuremonitor import (
    AzureLogsQuery,
    AzureMetricQuery,
    AzureMonitorQuery,
    AzureMonitorResource,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.models.azuremonitor import ResultFormat

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row2-azure"
TITLE = "BookFlow 운영 — Azure"
DESCRIPTION = (
    "Azure Monitor 기반 Azure 리소스 운영 현황. Logic Apps 6개 실행/메일 발송률 · "
    "Function App 호출·에러 · Event Grid 이벤트 · Key Vault 접근/가용성. "
    "rg-bookflow (subscription e98a94bb-…). Entra OIDC 로그인은 §하단 노트 참조."
)

# ── 라이브 Azure 좌표 (2026-05-19 /resources 실측) ──────────────────────
SUBSCRIPTION = "e98a94bb-7532-4e49-8a36-bc42e30d5a81"
RESOURCE_GROUP = "rg-bookflow"

# Log Analytics 워크스페이스 (Logic Apps WorkflowRuntime 진단 로그 보관)
LAW_RESOURCE_ID = (
    f"/subscriptions/{SUBSCRIPTION}/resourceGroups/{RESOURCE_GROUP}"
    f"/providers/Microsoft.OperationalInsights/workspaces/law-bookflowmj"
)

# 알림 발송 핵심 Logic App (메일 발송률 산정 기준)
NOTIFICATION_WORKFLOW = "la-bookflowmj-notification"


# ── Azure Monitor 메트릭 쿼리 헬퍼 ──────────────────────────────────────
def _metric_query(
    namespace: str, resource_name: str, metric: str, aggregation: str, *,
    alias: str = "", time_grain: str = "PT1H",
) -> AzureMonitorQuery:
    """단일 Azure 리소스의 메트릭을 조회하는 AzureMonitorQuery 빌더."""
    resource = (
        AzureMonitorResource()
        .subscription(SUBSCRIPTION)
        .resource_group(RESOURCE_GROUP)
        .resource_name(resource_name)
        .metric_namespace(namespace)
    )
    metric_q = (
        AzureMetricQuery()
        .resources([resource])
        .metric_namespace(namespace)
        .metric_name(metric)
        .aggregation(aggregation)
        .time_grain(time_grain)
    )
    if alias:
        metric_q = metric_q.alias(alias)
    return (
        AzureMonitorQuery()
        .query_type("Azure Monitor")
        .subscription(SUBSCRIPTION)
        .azure_monitor(metric_q)
        .datasource(ds.ref(ds.AZURE_MONITOR))
    )


def _logs_query(kql: str, result_format: ResultFormat) -> AzureMonitorQuery:
    """Log Analytics(law-bookflowmj) KQL 쿼리 AzureMonitorQuery 빌더."""
    logs = (
        AzureLogsQuery()
        .query(kql)
        .resources([LAW_RESOURCE_ID])
        .result_format(result_format)
        .dashboard_time(True)
    )
    return (
        AzureMonitorQuery()
        .query_type("Azure Log Analytics")
        .subscription(SUBSCRIPTION)
        .azure_log_analytics(logs)
        .datasource(ds.ref(ds.AZURE_MONITOR))
    )


# ── Entra OIDC 로그인 ──────────────────────────────────────────────────
def _entra_signins() -> object:
    """Entra OIDC 로그인 성공/실패 — auth-pod 의 Entra(SigninLogs).

    NOTE: law-bookflowmj 워크스페이스에는 SigninLogs 가 라우팅되어 있지 않다
    (2026-05-19 실측: AzureMetrics / AzureDiagnostics / Usage 만 존재).
    AAD 진단설정에서 SignInLogs 를 이 워크스페이스로 보내면 본 패널이 즉시
    동작한다. 그 전까지 KQL 은 SigninLogs 부재 시 빈 시리즈를 반환한다.
    """
    kql = (
        "SigninLogs "
        "| where TimeGenerated > ago(6h) "
        "| summarize 성공=countif(ResultType==0), 실패=countif(ResultType!=0) "
        "by bin(TimeGenerated, 15m) "
        "| order by TimeGenerated asc"
    )
    panel = pb.timeseries_panel(
        "Entra OIDC 로그인 성공/실패",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "auth-pod 의 Entra(Azure AD) OIDC 로그인 결과. "
            "law-bookflowmj 에 SigninLogs 진단설정 라우팅 필요 (현재 미수집)."
        ),
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _logs_query(kql, ResultFormat.TIME_SERIES)
    )


# ── Logic Apps ×6 ──────────────────────────────────────────────────────
def _logic_apps_table() -> object:
    """Logic Apps 6개 워크플로 실행 현황 — 성공/실패/실행중/스킵 카운트.

    AzureDiagnostics(MICROSOFT.LOGIC · WorkflowRuntime) 를 워크플로별로 집계.
    개별 Logic App 메트릭(RunsCompleted 등)은 리소스당 1개씩이라 6개를 한
    표로 못 보므로, 진단 로그 KQL pivot 으로 전 워크플로를 한눈에 본다.
    """
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        "| where TimeGenerated > ago(6h) "
        "| summarize 실행=count(), "
        "성공=countif(status_s=='Succeeded'), "
        "실패=countif(status_s=='Failed'), "
        "실행중=countif(status_s=='Running'), "
        "스킵=countif(status_s=='Skipped') "
        "by 워크플로=resource_workflowName_s "
        "| order by 실패 desc, 실행 desc"
    )
    panel = pb.table_panel(
        "Logic Apps 워크플로 실행 현황 (6개)",
        span=pb.SPAN_HALF,
        description=(
            "AzureDiagnostics WorkflowRuntime 6h 집계. 워크플로별 실행/성공/"
            "실패/실행중/스킵. 실패 많은 순 정렬. "
            "주의: diagnosticSettings(WorkflowRuntime → law-bookflowmj) 미설정 "
            "워크플로는 본 표에 표시되지 않는다. Consumption 3개는 "
            "logicapp-consumption-diag.bicep 으로 IaC 화."
        ),
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _logs_query(kql, ResultFormat.TABLE)
    )


def _logic_runs_timeseries() -> object:
    """Logic Apps 전체 실행 추세 — 완료 vs 실패 (전 워크플로 합계).

    개별 워크플로 메트릭을 6번 합치는 대신, 진단 로그에서 status 별
    시계열로 집계해 전체 실행 흐름을 본다.
    """
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        "| where TimeGenerated > ago(6h) "
        "| summarize 완료=countif(status_s=='Succeeded'), "
        "실패=countif(status_s=='Failed') "
        "by bin(TimeGenerated, 15m) "
        "| order by TimeGenerated asc"
    )
    panel = pb.timeseries_panel(
        "Logic Apps 실행 추세 (완료/실패 · 6개 합계)",
        unit="short",
        span=pb.SPAN_HALF,
        description="AzureDiagnostics WorkflowRuntime — 전 워크플로 status 별 15m 집계.",
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _logs_query(kql, ResultFormat.TIME_SERIES)
    )


def _mail_success_rate() -> object:
    """notification Logic App 메일 발송 성공률 (%).

    la-bookflowmj-notification 의 RunsSucceeded / RunsCompleted 비율.
    Azure Monitor 메트릭만으로는 비율 산정이 안 되므로 진단 로그에서
    이 워크플로의 성공/완료를 세어 발송률을 계산한다.
    """
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        f"| where resource_workflowName_s == '{NOTIFICATION_WORKFLOW}' "
        "| where TimeGenerated > ago(6h) "
        "| where status_s in ('Succeeded','Failed') "
        "| summarize 성공=countif(status_s=='Succeeded'), 총건=count() "
        "| extend 발송률 = iff(총건==0, real(null), round(100.0*성공/총건, 1)) "
        "| project 발송률"
    )
    panel = pb.gauge_panel(
        "메일 발송 성공률 (notification)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        minimum=0,
        maximum=100,
        span=pb.SPAN_QUARTER,
        decimals=1,
        description=(
            "la-bookflowmj-notification 발송 성공률. "
            "AzureDiagnostics 성공/(성공+실패) · 6h."
        ),
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _logs_query(kql, ResultFormat.TABLE)
    )


# ── Function App ───────────────────────────────────────────────────────
def _function_executions() -> object:
    """Function App 함수 실행 수 — func-bookflowmj-sync.

    Microsoft.Web/sites 메트릭 FunctionExecutionCount (Total).
    """
    panel = pb.timeseries_panel(
        "Function App 실행 수 (func-sync)",
        unit="short",
        span=pb.SPAN_HALF,
        description="func-bookflowmj-sync · FunctionExecutionCount (Total · 1h grain).",
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _metric_query(
            "Microsoft.Web/sites", "func-bookflowmj-sync",
            "FunctionExecutionCount", "Total", alias="실행 수",
        )
    )


def _function_errors() -> object:
    """Function App HTTP 5xx 에러 수 — func-bookflowmj-sync.

    Microsoft.Web/sites 메트릭 Http5xx (Total).
    """
    panel = pb.timeseries_panel(
        "Function App 5xx 에러 (func-sync)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description="func-bookflowmj-sync · Http5xx (Total · 1h grain).",
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _metric_query(
            "Microsoft.Web/sites", "func-bookflowmj-sync",
            "Http5xx", "Total", alias="5xx 에러",
        )
    )


# ── Event Grid ─────────────────────────────────────────────────────────
def _event_grid() -> object:
    """Event Grid 이벤트 처리 수 — egt-bookflowmj-keyvault system topic.

    Microsoft.EventGrid/systemTopics 메트릭 PublishSuccessCount /
    DeliverySuccessCount (Total).
    """
    panel = pb.timeseries_panel(
        "Event Grid 이벤트 처리 (keyvault topic)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "egt-bookflowmj-keyvault · 발행 성공(PublishSuccessCount)과 "
            "전달 성공(DeliverySuccessCount). KeyVault 이벤트 발생 시 카운트."
        ),
    )
    return (
        panel.datasource(ds.ref(ds.AZURE_MONITOR))
        .with_target(
            _metric_query(
                "Microsoft.EventGrid/systemTopics", "egt-bookflowmj-keyvault",
                "PublishSuccessCount", "Total", alias="발행 성공",
            )
        )
        .with_target(
            _metric_query(
                "Microsoft.EventGrid/systemTopics", "egt-bookflowmj-keyvault",
                "DeliverySuccessCount", "Total", alias="전달 성공",
            )
        )
    )


# ── Key Vault ──────────────────────────────────────────────────────────
def _keyvault_api_hits() -> object:
    """Key Vault API 접근 수 — kv-bookflowmj.

    Microsoft.KeyVault/vaults 메트릭 ServiceApiHit (Count).
    """
    panel = pb.timeseries_panel(
        "Key Vault API 접근 수 (kv-bookflowmj)",
        unit="short",
        span=pb.SPAN_HALF,
        description="kv-bookflowmj · ServiceApiHit (Count · 1h grain).",
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _metric_query(
            "Microsoft.KeyVault/vaults", "kv-bookflowmj",
            "ServiceApiHit", "Count", alias="API 접근",
        )
    )


def _keyvault_availability() -> object:
    """Key Vault 가용성 (%) — kv-bookflowmj.

    Microsoft.KeyVault/vaults 메트릭 Availability (Average).
    NOTE: 시크릿 만료 임박(D-day)은 Azure Monitor 플랫폼 메트릭으로 노출되지
    않는다 — Event Grid SecretNearExpiry 이벤트(위 Event Grid 패널) 또는 Logic
    App secret-rotation 실행으로 추적. 본 패널은 Vault 가용성 SLO 만 본다.
    """
    panel = pb.gauge_panel(
        "Key Vault 가용성",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        minimum=90,
        maximum=100,
        span=pb.SPAN_QUARTER,
        decimals=2,
        description=(
            "kv-bookflowmj · Availability (Average). 시크릿 만료는 "
            "Event Grid SecretNearExpiry / secret-rotation Logic App 로 추적."
        ),
    )
    return panel.datasource(ds.ref(ds.AZURE_MONITOR)).with_target(
        _metric_query(
            "Microsoft.KeyVault/vaults", "kv-bookflowmj",
            "Availability", "Average", alias="가용성",
        )
    )


def dashboard() -> Dashboard:
    """Row 2 (Azure) 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 2 · Azure ──────────────────────────────────────────────
        .with_row(Row("Row 2 · Azure (Azure Monitor)"))
        # Entra OIDC 로그인
        .with_panel(_entra_signins())
        # Logic Apps ×6
        .with_panel(_logic_runs_timeseries())
        .with_panel(_logic_apps_table())
        .with_panel(_mail_success_rate())
        # Function App
        .with_panel(_function_executions())
        .with_panel(_function_errors())
        # Event Grid
        .with_panel(_event_grid())
        # Key Vault
        .with_panel(_keyvault_api_hits())
        .with_panel(_keyvault_availability())
    )
