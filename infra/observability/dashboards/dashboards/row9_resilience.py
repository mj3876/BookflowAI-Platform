"""Row 9 — 시연 시나리오 SCN-01~08 검증 대시보드.

사용자(메인)가 시연 시 8개 시나리오를 직관적으로 정상/장애 검증할 수 있도록
시나리오 하나당 row 헤더 + 시나리오 설명 텍스트 패널 + 3~6개 검증 패널로
구성한다. 보안 SCN-09/10 은 별도 — 본 row 에선 제외.

시연 시나리오 8개:
  SCN-01 기존도서 정상플로우 (정상/운영)
    POS 매출 시뮬레이터 → Kinesis → pos-ingestor → RDS → forecast-svc →
    decision-svc → notification 의 throughput 흐름. 각 stage invocation·error·latency.
  SCN-02 신간도서 정상플로우 (정상/운영)
    publisher-watcher CronJob → aladin-sync Lambda → new_book_requests INSERT →
    HQ 승인. CronJob 실행/Lambda 호출/요청 트래픽/승인량.
  SCN-03 EKS Node+Pod 오토스케일링 (장애/기능)
    HPA current/desired replicas · 노드 수(Karpenter/CA) · Pod CPU/메모리 · 스케일 이벤트.
  SCN-04 출판사 EC2 AutoScaling (장애/운영)
    ASG GroupDesiredCapacity/InServiceInstances/Min/Max · EC2 CPUUtilization · StatusCheckFailed.
  SCN-05 RDS 이중화 (장애/운영)
    Multi-AZ ReplicaLag · FreeableMemory · DBLoad · 연결 단절·복구.
    NOTE: admin 환경에서 Multi-AZ 미활성 가능 → ReplicaLag placeholder.
  SCN-06 VPN Active/Standby Failover (장애/네트워크)
    AWS↔GCP × 2 터널 + AWS↔Azure × 2 터널 합 4 터널 TunnelState · 트래픽.
  SCN-07 Logic App TIMEOUT (장애/기능)
    Azure Monitor Logic Apps RunsStarted/Succeeded/Failed/RunDuration · timeout 비율.
  SCN-08 GCP Cloud Function bookflow-bq-load 장애 (장애/기능)
    function/execution_count·execution_times · GCS staging 파일 수 · BQ load jobs.

데이터소스: Prometheus / CloudWatch / Azure Monitor / GCP Cloud Monitoring 혼합.
CloudWatch metrics 쿼리는 전부 metric_query_type=SEARCH + metric_editor_mode=BUILDER
(메인이 row1 라이브 검증으로 확정한 no-data 회피 필수 fix).

환경상 비활성 메트릭은 placeholder 패널 + description 에 명시 — 시각적으로
'무엇이 비는지' 보이게(사용자 요구 '안 보이는 곳 없이').
"""

from grafana_foundation_sdk.builders.azuremonitor import (
    AzureLogsQuery,
    AzureMetricQuery,
    AzureMonitorQuery,
    AzureMonitorResource,
)
from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchMetricsQuery as CWMetrics,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.googlecloudmonitoring import (
    CloudMonitoringQuery,
    TimeSeriesList,
)
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.builders.text import Panel as TextPanel
from grafana_foundation_sdk.models.azuremonitor import ResultFormat
from grafana_foundation_sdk.models.cloudwatch import (
    CloudWatchQueryMode,
    MetricEditorMode,
    MetricQueryType,
)
from grafana_foundation_sdk.models.common import BigValueGraphMode
from grafana_foundation_sdk.models.dashboard import (
    DashboardSpecialValueMapOptions,
    SpecialValueMap,
    SpecialValueMatch,
    ValueMap,
    ValueMappingResult,
)
from grafana_foundation_sdk.models.text import TextMode

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row9-resilience"
TITLE = "BookFlow 운영 — 시연 시나리오 (SCN-01~08)"
DESCRIPTION = (
    "시연 시나리오 8개 직관 검증. 각 시나리오마다 row 헤더 + 시나리오 설명 + "
    "3~6 검증 패널. SCN-01/02 정상플로우 · SCN-03~08 장애 시나리오. 보안(09/10)은 별도."
)

# ── 라이브 좌표 ─────────────────────────────────────────────────────────
AWS_REGION = "ap-northeast-1"
RDS_ID = "bookflow-postgres"
KINESIS_STREAM = "bookflow-pos-events"

# cross-cloud S2S VPN — VpnId (AWS/VPN dimension)
VPN_AWS_GCP = "vpn-0acce17f17cf493e7"
VPN_AWS_AZURE = "vpn-0c5c1f736a382cd41"

# Lambda 함수명 (실재 — row1 정의 동일)
LAMBDA_POS_INGESTOR = "bookflow-pos-ingestor"
LAMBDA_SPIKE_DETECT = "bookflow-spike-detect"
LAMBDA_ALADIN_SYNC = "bookflow-aladin-sync"
LAMBDA_SNS_GEN = "bookflow-sns-gen"
LAMBDA_EVENT_SYNC = "bookflow-event-sync"
LAMBDA_FORECAST_TRIGGER = "bookflow-forecast-trigger"

# Azure 좌표 (row2 정의 동일)
AZ_SUBSCRIPTION = "e98a94bb-7532-4e49-8a36-bc42e30d5a81"
AZ_RESOURCE_GROUP = "rg-bookflow"
LAW_RESOURCE_ID = (
    f"/subscriptions/{AZ_SUBSCRIPTION}/resourceGroups/{AZ_RESOURCE_GROUP}"
    f"/providers/Microsoft.OperationalInsights/workspaces/law-bookflowmj"
)
NOTIFICATION_WORKFLOW = "la-bookflowmj-notification"

# GCP 좌표 (row3 정의 동일)
GCP_PROJECT = "project-8ab6bf05-54d2-4f5d-b8d"
GCP_CF_BQLOAD = "bookflow-bq-load"

# Pod 셀렉터
BOOKFLOW_NS = 'namespace="bookflow"'
POD_SEL = 'job="kubernetes-pods",namespace="bookflow"'


# ── value mappings ──────────────────────────────────────────────────────
_UPDOWN_MAP = ValueMap(
    options={
        "0": ValueMappingResult(text="DOWN", color=pb.RED),
        "1": ValueMappingResult(text="UP", color=pb.GREEN),
    }
)
_NODATA_MAP = SpecialValueMap(
    options=DashboardSpecialValueMapOptions(
        match=SpecialValueMatch.NULL,
        result=ValueMappingResult(text="N/A", color=pb.YELLOW),
    )
)


# ── datasource ref 헬퍼 ─────────────────────────────────────────────────
def _prom():
    return ds.ref(ds.PROMETHEUS)


def _cw():
    return ds.ref(ds.CLOUDWATCH)


def _azure():
    return ds.ref(ds.AZURE_MONITOR)


def _gcp():
    return ds.ref(ds.GCP_MONITORING)


# ── 쿼리 빌더 헬퍼 — datasource 명시(패널 + 쿼리 양쪽에 · no-data 회피) ─
def _prom_q(expr: str, ref_id: str = "A", *, instant: bool = False, legend: str = ""):
    q = PromQuery().datasource(_prom()).expr(expr).ref_id(ref_id)
    if instant:
        q = q.instant()
    else:
        q = q.range()
    if legend:
        q = q.legend_format(legend)
    return q


def _cw_metric(ref_id, namespace, metric, dims, *, stat="Average",
               period="300", label="", match_exact=True):
    """CloudWatch 메트릭 쿼리 (정확한 dimension 매치).
    metric_query_type=SEARCH + metric_editor_mode=BUILDER 필수.
    """
    return (
        CWMetrics()
        .datasource(_cw())
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(AWS_REGION)
        .namespace(namespace)
        .metric_name(metric)
        .dimensions(dims)
        .statistic(stat)
        .period(period)
        .match_exact(match_exact)
        .label(label)
        .ref_id(ref_id)
    )


def _cw_search(ref_id, namespace, expression, *, stat="Average",
               period="300", label=""):
    """CloudWatch SEARCH 식 쿼리 — dimension 값이 회전해도 자동 매칭."""
    return (
        CWMetrics()
        .datasource(_cw())
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(AWS_REGION)
        .namespace(namespace)
        .expression(expression)
        .statistic(stat)
        .period(period)
        .label(label)
        .ref_id(ref_id)
    )


def _azure_metric(namespace, resource_name, metric, aggregation, *,
                  alias="", time_grain="PT5M"):
    """Azure Monitor 단일 리소스 메트릭 쿼리."""
    resource = (
        AzureMonitorResource()
        .subscription(AZ_SUBSCRIPTION)
        .resource_group(AZ_RESOURCE_GROUP)
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
        .subscription(AZ_SUBSCRIPTION)
        .azure_monitor(metric_q)
        .datasource(_azure())
    )


def _azure_logs(kql: str, result_format: ResultFormat) -> AzureMonitorQuery:
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
        .subscription(AZ_SUBSCRIPTION)
        .azure_log_analytics(logs)
        .datasource(_azure())
    )


def _gcp_ts(metric_type: str, *, aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=None, extra_filters=None, alias="",
            alignment_period="+300s") -> CloudMonitoringQuery:
    # Grafana 11.2 stackdriver datasource 는 `filters` 배열을 공백/AND 없이
    # concat 해 400 INVALID_ARGUMENT 를 유발 → 단일 문자열 ` AND ` join 로 회피.
    parts = [f'metric.type="{metric_type}"']
    if extra_filters:
        parts.extend(extra_filters)
    tsl = (
        TimeSeriesList()
        .project_name(GCP_PROJECT)
        .filters([" AND ".join(parts)])
        .per_series_aligner(aligner)
        .cross_series_reducer(reducer)
        .alignment_period(alignment_period)
    )
    if group_bys:
        tsl = tsl.group_bys(group_bys)
    q = (
        CloudMonitoringQuery()
        .query_type("timeSeriesList")
        .time_series_list(tsl)
        .datasource(_gcp())
    )
    if alias:
        q = q.alias_by(alias)
    return q


def _scn_intro(title: str, body: str, *, span: int = pb.SPAN_FULL,
               height: int = 4) -> TextPanel:
    """시나리오 설명 텍스트 패널 (markdown · row 첫 패널)."""
    return (
        TextPanel()
        .title(title)
        .span(span)
        .height(height)
        .mode(TextMode.MARKDOWN)
        .content(body)
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-01 — 기존도서 정상플로우 (POS → Kinesis → pos-ingestor → RDS →
#          forecast-svc → decision-svc → notification)
# ════════════════════════════════════════════════════════════════════════
def _scn01_intro():
    return _scn_intro(
        "SCN-01 · 기존도서 정상플로우 (정상/운영)",
        "POS 매출 시뮬레이터 → **Kinesis** (`bookflow-pos-events`) → "
        "**pos-ingestor Lambda** → **RDS** (`bookflow-postgres`) → "
        "**forecast-svc** → **decision-svc** → **notification-svc** 의 정상 "
        "throughput 흐름. 각 stage 의 invocation·error·latency 를 함께 본다. "
        "끊김 없이 다음 stage 로 흘러야 정상.",
    )


def _scn01_kinesis_in():
    """Kinesis 유입 레코드 — POS 시뮬레이터 입력."""
    p = pb.timeseries_panel(
        "Kinesis 유입 레코드 (POS 매출)",
        unit="short",
        span=pb.SPAN_HALF,
        description="AWS/Kinesis IncomingRecords — bookflow-pos-events. 시뮬레이터 트래픽.",
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/Kinesis", "IncomingRecords",
                   {"StreamName": KINESIS_STREAM}, stat="Sum", label="유입 레코드")
    )


def _scn01_kinesis_lag():
    """Kinesis iterator age — 소비 지연 (pos-ingestor 가 못 따라오면 증가)."""
    p = pb.timeseries_panel(
        "Kinesis Iterator Age (pos-ingestor 소비 지연)",
        unit="ms",
        span=pb.SPAN_HALF,
        description=(
            "AWS/Kinesis GetRecords.IteratorAgeMilliseconds — pos-ingestor "
            "소비 지연. 값이 크면 백프레셔 발생."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds",
                   {"StreamName": KINESIS_STREAM}, stat="Maximum", label="iterator age")
    )


def _scn01_ingestor_invocations():
    """pos-ingestor Lambda 호출/에러/throttle."""
    p = pb.timeseries_panel(
        "pos-ingestor Lambda · 호출 / 에러 / Throttle",
        unit="short",
        span=pb.SPAN_HALF,
        description="AWS/Lambda Invocations·Errors·Throttles — bookflow-pos-ingestor.",
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_metric("A", "AWS/Lambda", "Invocations",
                                {"FunctionName": LAMBDA_POS_INGESTOR},
                                stat="Sum", label="호출"))
        .with_target(_cw_metric("B", "AWS/Lambda", "Errors",
                                {"FunctionName": LAMBDA_POS_INGESTOR},
                                stat="Sum", label="에러"))
        .with_target(_cw_metric("C", "AWS/Lambda", "Throttles",
                                {"FunctionName": LAMBDA_POS_INGESTOR},
                                stat="Sum", label="throttle"))
    )


def _scn01_ingestor_duration():
    """pos-ingestor Lambda 실행시간 (latency)."""
    p = pb.timeseries_panel(
        "pos-ingestor Lambda · 실행시간",
        unit="ms",
        span=pb.SPAN_HALF,
        description="AWS/Lambda Duration(Average/Maximum) — bookflow-pos-ingestor.",
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_metric("A", "AWS/Lambda", "Duration",
                                {"FunctionName": LAMBDA_POS_INGESTOR},
                                stat="Average", label="avg"))
        .with_target(_cw_metric("B", "AWS/Lambda", "Duration",
                                {"FunctionName": LAMBDA_POS_INGESTOR},
                                stat="Maximum", label="max"))
    )


def _scn01_svc_rps():
    """forecast/decision/notification Pod RPS — http_requests_total rate."""
    p = pb.timeseries_panel(
        "forecast/decision/notification-svc · RPS",
        unit="reqps",
        span=pb.SPAN_HALF,
        description=(
            "Pod별 RPS — sum by(app) rate(http_requests_total[5m]). "
            "forecast → decision → notification 흐름 throughput."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            "sum by (app) (rate(http_requests_total{"
            f"{POD_SEL},"
            'app=~"forecast-svc|decision-svc|notification-svc"}[5m]))',
            legend="{{app}}",
        )
    )


def _scn01_svc_p95():
    """forecast/decision/notification Pod p95 지연."""
    p = pb.timeseries_panel(
        "forecast/decision/notification-svc · p95 지연",
        unit="s",
        span=pb.SPAN_HALF,
        description=(
            "histogram_quantile(0.95, http_request_duration_highr_seconds_bucket) — "
            "stage 별 p95 지연. 늘면 다음 stage 도 지연."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            "histogram_quantile(0.95, sum by (app, le) "
            f"(rate(http_request_duration_highr_seconds_bucket{{{POD_SEL},"
            'app=~"forecast-svc|decision-svc|notification-svc"}[5m])))',
            legend="{{app}}",
        )
    )


def _scn01_svc_errors():
    """forecast/decision/notification 에러율 (%)."""
    p = pb.timeseries_panel(
        "forecast/decision/notification-svc · 에러율 (5xx %)",
        unit="percent",
        span=pb.SPAN_HALF,
        description="5xx 비율 — stage 별 실패율. 0% 이어야 정상.",
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            "100 * ((sum by (app) (rate(http_requests_total{"
            f"{POD_SEL},"
            'app=~"forecast-svc|decision-svc|notification-svc",status="5xx"}[5m]))'
            " or (sum by (app) (rate(http_requests_total{"
            f"{POD_SEL},"
            'app=~"forecast-svc|decision-svc|notification-svc"}[5m])) * 0))'
            " / clamp_min(sum by (app) (rate(http_requests_total{"
            f"{POD_SEL},"
            'app=~"forecast-svc|decision-svc|notification-svc"}[5m])), 0.001))',
            legend="{{app}}",
        )
    )


def _scn01_rds_connections():
    """RDS 연결 수 — pos-ingestor + svc 가 정상 연결돼 있어야."""
    p = pb.timeseries_panel(
        "RDS 연결 수 (Pod·Lambda 정상 연결)",
        unit="short",
        span=pb.SPAN_HALF,
        description="AWS/RDS DatabaseConnections — bookflow-postgres. 흐름 단절 시 감소.",
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "DatabaseConnections",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="연결")
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-02 — 신간도서 정상플로우 (publisher-watcher CronJob → aladin-sync →
#          new_book_requests → HQ 승인)
# ════════════════════════════════════════════════════════════════════════
def _scn02_intro():
    return _scn_intro(
        "SCN-02 · 신간도서 정상플로우 (정상/운영)",
        "**publisher-watcher CronJob** (15분 주기) → **aladin-sync Lambda** "
        "(알라딘 OpenAPI 호출) → **new_book_requests** INSERT → "
        "**dashboard-svc** `/dashboard/new-book-requests` 조회 → **HQ 승인**. "
        "publisher-watcher 는 CronJob 이라 RPS 없음 → aladin-sync Lambda "
        "호출 + 조회 트래픽 + 승인 처리량으로 흐름 추적.",
    )


def _scn02_aladin_invocations():
    """aladin-sync Lambda 호출/에러 — 신간 검색 트래픽."""
    p = pb.timeseries_panel(
        "aladin-sync Lambda · 호출 / 에러",
        unit="short",
        span=pb.SPAN_HALF,
        description="AWS/Lambda Invocations·Errors — bookflow-aladin-sync 신간 검색.",
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_metric("A", "AWS/Lambda", "Invocations",
                                {"FunctionName": LAMBDA_ALADIN_SYNC},
                                stat="Sum", label="호출"))
        .with_target(_cw_metric("B", "AWS/Lambda", "Errors",
                                {"FunctionName": LAMBDA_ALADIN_SYNC},
                                stat="Sum", label="에러"))
    )


def _scn02_aladin_duration():
    """aladin-sync Lambda 실행시간 — 알라딘 OpenAPI 응답 지연 감지."""
    p = pb.timeseries_panel(
        "aladin-sync Lambda · 실행시간 (알라딘 OpenAPI 응답)",
        unit="ms",
        span=pb.SPAN_HALF,
        description="AWS/Lambda Duration — aladin-sync. 외부 API 지연 신호.",
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_metric("A", "AWS/Lambda", "Duration",
                                {"FunctionName": LAMBDA_ALADIN_SYNC},
                                stat="Average", label="avg"))
        .with_target(_cw_metric("B", "AWS/Lambda", "Duration",
                                {"FunctionName": LAMBDA_ALADIN_SYNC},
                                stat="Maximum", label="max"))
    )


def _scn02_newbook_traffic():
    """dashboard-svc 신간 요청 조회/승인 트래픽."""
    p = pb.timeseries_panel(
        "신간 요청 조회 + 승인 트래픽 (dashboard-svc)",
        unit="reqps",
        span=pb.SPAN_HALF,
        description=(
            "dashboard-svc /dashboard/new-book-requests 조회·승인 핸들러 호출량. "
            "publisher-watcher CronJob 으로 INSERT 된 신간을 HQ 가 확인/승인."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            "sum by (handler) (rate(http_requests_total{"
            f"{POD_SEL},"
            'app="dashboard-svc",'
            'handler=~"/dashboard/new-book-requests.*"}[5m]))',
            legend="{{handler}}",
        )
    )


def _scn02_publisher_cronjob():
    """publisher-watcher CronJob 실행 — kube_cronjob_status_last_schedule_time.

    kube-state-metrics 미설치 라이브 환경 — cAdvisor container_start_time_seconds
    의 publisher-watcher pod 시작 시각 변화로 실행 트리거를 대리 추적한다.
    kube-state-metrics 설치 시 kube_cronjob_status_last_successful_time 등으로
    교체.
    """
    p = pb.timeseries_panel(
        "publisher-watcher CronJob 실행 (15m 주기)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "changes(container_start_time_seconds{pod=~publisher-watcher.*}[1h]) — "
            "kube-state-metrics 미설치 → cAdvisor pod 시작 변화로 CronJob 트리거 대리. "
            "주기적(15m) 막대가 보이면 정상."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            f'changes(container_start_time_seconds{{{BOOKFLOW_NS},'
            'pod=~"publisher-watcher.*"}[1h])',
            legend="{{pod}}",
        )
    )


def _scn02_rds_writeio():
    """RDS Write IOPS — new_book_requests INSERT 신호."""
    p = pb.timeseries_panel(
        "RDS Write IOPS (신간 INSERT)",
        unit="iops",
        span=pb.SPAN_HALF,
        description=(
            "AWS/RDS WriteIOPS — bookflow-postgres. new_book_requests INSERT 시 "
            "스파이크. publisher-watcher 실행 직후 증가."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "WriteIOPS",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="Write")
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-03 — EKS Node+Pod 오토스케일링 (HPA + 노드수 + Pod CPU/MEM + 스케일 이벤트)
# ════════════════════════════════════════════════════════════════════════
def _scn03_intro():
    return _scn_intro(
        "SCN-03 · EKS Node+Pod 오토스케일링 (장애/기능)",
        "**HPA** current vs desired replicas · **노드 수** (Karpenter/Cluster "
        "Autoscaler) · **Pod CPU/메모리** 압박 · **스케일 이벤트**. "
        "부하 발생 → HPA 가 desired 증가 → Pod 스케줄 → 노드 부족이면 Karpenter "
        "가 노드 추가. HPA 가 정상 동작하면 current 가 desired 를 따라간다.\n\n"
        "NOTE: kube-state-metrics 미설치 라이브 — `kube_horizontalpodautoscaler_*` "
        "메트릭은 placeholder(KSM 설치 시 즉시 동작).",
    )


def _scn03_hpa_replicas():
    """HPA current vs desired replicas — kube-state-metrics.

    placeholder: kube-state-metrics 미설치 라이브 환경(2026-05-19 실측). KSM
    배포 시 kube_horizontalpodautoscaler_status_current_replicas /
    _status_desired_replicas 가 발행돼 즉시 동작.
    """
    p = pb.timeseries_panel(
        "HPA · current vs desired replicas",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "kube_horizontalpodautoscaler_status_{current,desired}_replicas — "
            "HPA 동작 추적. placeholder — kube-state-metrics 미설치."
        ),
    )
    return (
        p.datasource(_prom())
        .with_target(_prom_q(
            "sum by (horizontalpodautoscaler) "
            "(kube_horizontalpodautoscaler_status_current_replicas{namespace=\"bookflow\"})",
            ref_id="A", legend="{{horizontalpodautoscaler}} current"))
        .with_target(_prom_q(
            "sum by (horizontalpodautoscaler) "
            "(kube_horizontalpodautoscaler_status_desired_replicas{namespace=\"bookflow\"})",
            ref_id="B", legend="{{horizontalpodautoscaler}} desired"))
    )


def _scn03_node_count():
    """Ready 노드 수 — Karpenter / Cluster Autoscaler 노드 추가."""
    p = pb.timeseries_panel(
        "Ready 노드 수 (Karpenter/CA scale-up)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "count(up{job=\"kubernetes-nodes\"}==1) — 노드 부족 시 Karpenter/CA "
            "가 노드 추가, 그래프가 계단식으로 증가."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q('count(up{job="kubernetes-nodes"} == 1)', legend="Ready 노드")
    )


def _scn03_node_capacity():
    """노드 CPU capacity — kube_node_status_capacity_cpu_cores.

    placeholder: kube-state-metrics 미설치. 대체로 cAdvisor 의 노드별 컨테이너
    수로 노드 부하 가시화.
    """
    p = pb.timeseries_panel(
        "노드별 컨테이너 분포 (재배치/스케일-아웃 가시화)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "노드별 실행 컨테이너 수 (cAdvisor). 스케일-아웃 시 신규 노드에 "
            "Pod 가 분산되는 게 보인다. kube_node_status_capacity_cpu_cores 는 "
            "kube-state-metrics 설치 시 사용 가능."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            f'count(container_start_time_seconds{{{BOOKFLOW_NS},pod!=""}}) by (instance)',
            legend="{{instance}}",
        )
    )


def _scn03_pod_cpu():
    """Pod CPU 사용 — container_cpu_usage_seconds_total rate."""
    p = pb.timeseries_panel(
        "Pod CPU 사용 (압박)",
        unit="cores",
        span=pb.SPAN_HALF,
        description=(
            "sum by(pod) rate(container_cpu_usage_seconds_total[5m]) — "
            "cAdvisor. CPU 압박 시 HPA 스케일-아웃 트리거."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            'sum by (pod) (rate(container_cpu_usage_seconds_total{'
            f'{BOOKFLOW_NS},pod!="",container!=""}}[5m]))',
            legend="{{pod}}",
        )
    )


def _scn03_pod_memory():
    """Pod 메모리 사용 — container_memory_working_set_bytes."""
    p = pb.timeseries_panel(
        "Pod 메모리 사용 (압박)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "sum by(pod) container_memory_working_set_bytes — cAdvisor. "
            "메모리 압박 시 HPA(메모리 정책 설정 시) 또는 OOM."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            'sum by (pod) (container_memory_working_set_bytes{'
            f'{BOOKFLOW_NS},pod!="",container!=""}})',
            legend="{{pod}}",
        )
    )


def _scn03_pod_restarts():
    """Pod 재시작 감지 — 스케일 이벤트(재배치) 부산물."""
    p = pb.timeseries_panel(
        "Pod 재시작/재배치 감지",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "changes(container_start_time_seconds[1h]) — 스케일-아웃/스케일-인 "
            "시 Pod 재시작·재배치 이벤트가 막대로."
        ),
    )
    return p.datasource(_prom()).with_target(
        _prom_q(
            f'changes(container_start_time_seconds{{{BOOKFLOW_NS},pod!=""}}[1h])',
            legend="{{pod}}",
        )
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-04 — 출판사 EC2 AutoScaling (ASG · EC2 CPU · StatusCheck)
# ════════════════════════════════════════════════════════════════════════
def _scn04_intro():
    return _scn_intro(
        "SCN-04 · 출판사 EC2 AutoScaling (장애/운영)",
        "출판사 ASG **GroupDesiredCapacity/InServiceInstances/Min/Max** "
        "(`AWS/AutoScaling`) · 스케일 이벤트 · 출판사 EC2 **CPUUtilization** · "
        "**StatusCheckFailed** (`AWS/EC2`). TargetTracking CPU 정책 트리거 시 "
        "Desired 2 → 3 자동 스케일-아웃. SEARCH 식으로 ASG 식별자(blue/green "
        "회전) 자동 매칭.\n\n"
        "NOTE: Publisher CodeDeploy ASG 는 데일리 자원 — 미배포 시 N/A.",
    )


def _scn04_asg_capacity():
    """ASG Desired / InService / Min / Max — SEARCH 식 publisher 매칭."""
    p = pb.timeseries_panel(
        "출판사 ASG · Desired / InService / Min / Max",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "AWS/AutoScaling Group{Desired,InService,Min,Max} · publisher SEARCH. "
            "Desired 추세가 InService 와 일치해야 정상."
        ),
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_search(
            "A", "AWS/AutoScaling",
            "SEARCH('{AWS/AutoScaling,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"GroupDesiredCapacity\"', 'Average', 300)",
            stat="Average", label="Desired"))
        .with_target(_cw_search(
            "B", "AWS/AutoScaling",
            "SEARCH('{AWS/AutoScaling,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"GroupInServiceInstances\"', 'Average', 300)",
            stat="Average", label="InService"))
        .with_target(_cw_search(
            "C", "AWS/AutoScaling",
            "SEARCH('{AWS/AutoScaling,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"GroupMinSize\"', 'Average', 300)",
            stat="Average", label="Min"))
        .with_target(_cw_search(
            "D", "AWS/AutoScaling",
            "SEARCH('{AWS/AutoScaling,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"GroupMaxSize\"', 'Average', 300)",
            stat="Average", label="Max"))
    )


def _scn04_asg_inservice_stat():
    """현재 InService 수 stat — 빠른 확인."""
    p = pb.stat_panel(
        "출판사 ASG · 현재 InService",
        unit="short",
        color_mode=pb.BigValueColorMode.VALUE,
        thresholds=pb._thresholds([(None, pb.RED), (1, pb.YELLOW), (2, pb.GREEN)]),
        mappings=[_NODATA_MAP],
        description="AWS/AutoScaling GroupInServiceInstances · publisher SEARCH.",
    )
    return p.datasource(_cw()).with_target(
        _cw_search(
            "A", "AWS/AutoScaling",
            "SEARCH('{AWS/AutoScaling,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"GroupInServiceInstances\"', 'Average', 300)",
            stat="Average", label="InService",
        )
    )


def _scn04_ec2_cpu():
    """출판사 EC2 CPUUtilization — TargetTracking 정책 입력 신호."""
    p = pb.timeseries_panel(
        "출판사 EC2 · CPUUtilization (TargetTracking 입력)",
        unit="percent",
        span=pb.SPAN_HALF,
        description=(
            "AWS/EC2 CPUUtilization · AutoScalingGroupName=CodeDeploy_bookflow-publisher "
            "SEARCH. CPU > 임계 시 스케일-아웃 트리거."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_search(
            "A", "AWS/EC2",
            "SEARCH('{AWS/EC2,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"CPUUtilization\"', 'Average', 300)",
            stat="Average", label="{{InstanceId}} CPU",
        )
    )


def _scn04_ec2_statuscheck():
    """출판사 EC2 StatusCheckFailed — 장애 인스턴스 자동 교체."""
    p = pb.timeseries_panel(
        "출판사 EC2 · StatusCheckFailed (장애 인스턴스)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "AWS/EC2 StatusCheckFailed{,_Instance,_System} · publisher SEARCH. "
            "> 0 이면 인스턴스 unhealthy → ASG 자동 교체."
        ),
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_search(
            "A", "AWS/EC2",
            "SEARCH('{AWS/EC2,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"StatusCheckFailed\"', 'Maximum', 300)",
            stat="Maximum", label="StatusCheckFailed"))
        .with_target(_cw_search(
            "B", "AWS/EC2",
            "SEARCH('{AWS/EC2,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"StatusCheckFailed_Instance\"', 'Maximum', 300)",
            stat="Maximum", label="Instance"))
        .with_target(_cw_search(
            "C", "AWS/EC2",
            "SEARCH('{AWS/EC2,AutoScalingGroupName} "
            "AutoScalingGroupName=\"CodeDeploy_bookflow-publisher\" "
            "MetricName=\"StatusCheckFailed_System\"', 'Maximum', 300)",
            stat="Maximum", label="System"))
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-05 — RDS 이중화 (Multi-AZ · ReplicaLag · FreeableMemory · DBLoad)
# ════════════════════════════════════════════════════════════════════════
def _scn05_intro():
    return _scn_intro(
        "SCN-05 · RDS 이중화 (장애/운영)",
        "**Multi-AZ** failover 시나리오. **ReplicaLag** · **FreeableMemory** · "
        "**DBLoad** · 연결 단절·복구. failover 트리거 시 30초~2분 내 standby → "
        "primary 승격, 연결 일시 단절 후 자동 재연결.\n\n"
        "NOTE: 현 admin 환경에서 Multi-AZ 미활성 가능 — `ReplicaLag` 빈 시리즈. "
        "Multi-AZ 활성 시 즉시 동작. Multi-AZ 켜져 있는지 보려면 "
        "`aws rds describe-db-instances --query 'DBInstances[*].MultiAZ'`.",
    )


def _scn05_rds_connections_timeline():
    """RDS DatabaseConnections 타임라인 — failover blip."""
    p = pb.timeseries_panel(
        "RDS 연결 수 (failover blip)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "AWS/RDS DatabaseConnections — bookflow-postgres. failover 시 "
            "순간 0 으로 단절 후 30초~2분 내 재연결."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "DatabaseConnections",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="연결")
    )


def _scn05_rds_cpu():
    """RDS CPU 사용률."""
    p = pb.timeseries_panel(
        "RDS CPU 사용률",
        unit="percent",
        span=pb.SPAN_HALF,
        description="AWS/RDS CPUUtilization — bookflow-postgres.",
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "CPUUtilization",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="CPU")
    )


def _scn05_rds_freeable_memory():
    """RDS FreeableMemory — 메모리 압박 신호."""
    p = pb.timeseries_panel(
        "RDS FreeableMemory (메모리 압박)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "AWS/RDS FreeableMemory — bookflow-postgres. 낮을수록 메모리 압박. "
            "지속 0 근처면 failover 위험 신호."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "FreeableMemory",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="Freeable")
    )


def _scn05_rds_replica_lag():
    """RDS ReplicaLag — Multi-AZ read replica 지연.

    placeholder 가능: Multi-AZ 미활성 / read replica 없으면 빈 시리즈.
    Multi-AZ + read replica 활성 시 자동 동작.
    """
    p = pb.timeseries_panel(
        "RDS ReplicaLag (Multi-AZ replica 지연)",
        unit="s",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "AWS/RDS ReplicaLag — Multi-AZ replica 지연(초). > 60s 면 위험. "
            "Multi-AZ 미활성 시 빈 시리즈(N/A) — describe-db-instances "
            "MultiAZ 확인."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "ReplicaLag",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="ReplicaLag")
    )


def _scn05_rds_dbload():
    """RDS DBLoad — Performance Insights 의 average active sessions."""
    p = pb.timeseries_panel(
        "RDS DBLoad (Active Sessions)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "AWS/RDS DBLoad — Performance Insights average active sessions. "
            "vCPU 수보다 높으면 부하 과부하."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/RDS", "DBLoad",
                   {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="DBLoad")
    )


def _scn05_rds_iops():
    """RDS IOPS Read/Write — failover 후 write 트래픽 재개."""
    p = pb.timeseries_panel(
        "RDS IOPS (Read/Write · failover 후 재개)",
        unit="iops",
        span=pb.SPAN_HALF,
        description=(
            "AWS/RDS ReadIOPS·WriteIOPS — failover 시 write 일시 단절 후 "
            "standby 승격 완료 시점에 write 재개."
        ),
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_metric("A", "AWS/RDS", "ReadIOPS",
                                {"DBInstanceIdentifier": RDS_ID},
                                stat="Average", label="Read"))
        .with_target(_cw_metric("B", "AWS/RDS", "WriteIOPS",
                                {"DBInstanceIdentifier": RDS_ID},
                                stat="Average", label="Write"))
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-06 — VPN Active/Standby Failover (AWS↔GCP×2 + AWS↔Azure×2 합 4 터널)
# ════════════════════════════════════════════════════════════════════════
def _scn06_intro():
    return _scn_intro(
        "SCN-06 · VPN Active/Standby Failover (장애/네트워크)",
        "cross-cloud VPN 의 모든 터널 **TunnelState** (1=UP/0=DOWN) · 트래픽 · "
        "BGP 세션 상태 · failover event. AWS↔GCP HA VPN 2 터널 + AWS↔Azure "
        "S2S VPN 2 터널 = 합 4 터널. Active 터널 다운 시 standby 가 즉시 인수.\n\n"
        "NOTE: AWS/VPN TunnelState 는 VpnId(연결) 기준 — 터널별로 직접 dimension "
        "분리하려면 TunnelIpAddress 필요. 본 row 는 연결 단위 Max(터널 중 하나 "
        "이상 UP) 으로 헬스 신호 사용.",
    )


def _scn06_vpn_state_table():
    """4 터널 상태 stat — UP/DOWN 신호등 4개."""
    p = pb.stat_panel(
        "VPN 터널 · AWS↔GCP",
        mappings=[_UPDOWN_MAP, _NODATA_MAP],
        thresholds=pb.updown_thresholds(),
        graph_mode=BigValueGraphMode.NONE,
        span=pb.SPAN_QUARTER,
        description="AWS/VPN TunnelState · Maximum — AWS↔GCP HA VPN.",
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/VPN", "TunnelState",
                   {"VpnId": VPN_AWS_GCP}, stat="Maximum", label="AWS↔GCP")
    )


def _scn06_vpn_state_azure():
    p = pb.stat_panel(
        "VPN 터널 · AWS↔Azure",
        mappings=[_UPDOWN_MAP, _NODATA_MAP],
        thresholds=pb.updown_thresholds(),
        graph_mode=BigValueGraphMode.NONE,
        span=pb.SPAN_QUARTER,
        description="AWS/VPN TunnelState · Maximum — AWS↔Azure S2S VPN.",
    )
    return p.datasource(_cw()).with_target(
        _cw_metric("A", "AWS/VPN", "TunnelState",
                   {"VpnId": VPN_AWS_AZURE}, stat="Maximum", label="AWS↔Azure")
    )


def _scn06_vpn_tunnels_all():
    """4 터널 timeline — SEARCH 식으로 전체 터널 자동 매칭."""
    p = pb.timeseries_panel(
        "VPN 터널 상태 (4 터널 · 끊김→복구 타임라인)",
        unit="short",
        span=pb.SPAN_HALF,
        thresholds=pb.updown_thresholds(),
        description=(
            "AWS/VPN TunnelState SEARCH — 모든 cross-cloud VPN 터널 상태. "
            "1=UP/0=DOWN. failover 시 active 터널 1→0 후 standby 가 0→1."
        ),
    )
    return p.datasource(_cw()).with_target(
        _cw_search(
            "A", "AWS/VPN",
            "SEARCH('{AWS/VPN,VpnId,TunnelIpAddress} "
            "MetricName=\"TunnelState\"', 'Maximum', 300)",
            stat="Maximum", label="{{VpnId}} {{TunnelIpAddress}}",
        )
    )


def _scn06_vpn_traffic():
    """4 터널 트래픽 In/Out."""
    p = pb.timeseries_panel(
        "VPN 터널 트래픽 (In/Out · 4 터널)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description="AWS/VPN TunnelDataIn/Out SEARCH — 4 터널 트래픽 흐름.",
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_search(
            "A", "AWS/VPN",
            "SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelDataIn\"', 'Sum', 300)",
            stat="Sum", label="{{VpnId}} In"))
        .with_target(_cw_search(
            "B", "AWS/VPN",
            "SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelDataOut\"', 'Sum', 300)",
            stat="Sum", label="{{VpnId}} Out"))
    )


def _scn06_vpn_failover_events():
    """VPN failover 이벤트 추적 — TunnelState 변화 빈도.

    AWS 는 VPN failover 전용 이벤트 메트릭이 없다 — TunnelState 의 시간 미분
    (양수 증가 + 음수 감소) 누적을 failover 빈도 대리 신호로.
    """
    p = pb.timeseries_panel(
        "VPN failover 이벤트 빈도 (TunnelState 변화)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "AWS/VPN TunnelState SEARCH · Maximum-Minimum. 동일 period 내 "
            "MIN<MAX 이면 그 구간에 UP↔DOWN 전이 발생(failover 시그널). "
            "실제 ‘이벤트’ 메트릭은 부재 — 전이 빈도로 대리 추적."
        ),
    )
    return (
        p.datasource(_cw())
        .with_target(_cw_search(
            "A", "AWS/VPN",
            "SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelState\"', 'Maximum', 300)",
            stat="Maximum", label="{{VpnId}} max"))
        .with_target(_cw_search(
            "B", "AWS/VPN",
            "SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelState\"', 'Minimum', 300)",
            stat="Minimum", label="{{VpnId}} min"))
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-07 — Logic App TIMEOUT (Azure Monitor RunsStarted/Succeeded/Failed/Duration)
# ════════════════════════════════════════════════════════════════════════
def _scn07_intro():
    return _scn_intro(
        "SCN-07 · Logic App TIMEOUT (장애/기능)",
        "Azure Logic Apps **RunsStarted** / **RunsSucceeded** / **RunsFailed** / "
        "**RunDuration** (Azure Monitor metrics). Timeout 비율 = (timeout failures "
        "/ total) · 평균 duration · p95 duration. notification 워크플로 timeout "
        "시 14건 동시 실패(2026-05-19 실사건) 재현 검증.",
    )


def _scn07_runs_started_completed():
    """Logic App 전체 실행 시작/완료/실패 (KQL — 12 워크플로 통합)."""
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        "| where TimeGenerated > ago(6h) "
        "| summarize 시작=count(), "
        "성공=countif(status_s=='Succeeded'), "
        "실패=countif(status_s=='Failed') "
        "by bin(TimeGenerated, 5m) "
        "| order by TimeGenerated asc"
    )
    p = pb.timeseries_panel(
        "Logic App 실행 시작/성공/실패 (12 워크플로 합계)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description="AzureDiagnostics WorkflowRuntime 5m 집계 — 12개 워크플로 합산.",
    )
    return p.datasource(_azure()).with_target(
        _azure_logs(kql, ResultFormat.TIME_SERIES)
    )


def _scn07_notification_runs_metric():
    """notification 워크플로 RunsSucceeded / RunsFailed (Azure Monitor metric)."""
    p = pb.timeseries_panel(
        "notification 워크플로 · RunsSucceeded / RunsFailed",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            f"Microsoft.Logic/workflows {NOTIFICATION_WORKFLOW} · "
            "RunsSucceeded·RunsFailed (Total · 5m grain)."
        ),
    )
    return (
        p.datasource(_azure())
        .with_target(_azure_metric(
            "Microsoft.Logic/workflows", NOTIFICATION_WORKFLOW,
            "RunsSucceeded", "Total", alias="성공", time_grain="PT5M"))
        .with_target(_azure_metric(
            "Microsoft.Logic/workflows", NOTIFICATION_WORKFLOW,
            "RunsFailed", "Total", alias="실패", time_grain="PT5M"))
        .with_target(_azure_metric(
            "Microsoft.Logic/workflows", NOTIFICATION_WORKFLOW,
            "RunsStarted", "Total", alias="시작", time_grain="PT5M"))
    )


def _scn07_run_duration():
    """notification 워크플로 RunLatency — 평균·최대."""
    p = pb.timeseries_panel(
        "notification 워크플로 · RunLatency (평균·최대)",
        unit="s",
        span=pb.SPAN_HALF,
        description=(
            f"Microsoft.Logic/workflows {NOTIFICATION_WORKFLOW} · "
            "RunLatency (Average/Maximum). timeout 임박 시 Maximum 급등."
        ),
    )
    return (
        p.datasource(_azure())
        .with_target(_azure_metric(
            "Microsoft.Logic/workflows", NOTIFICATION_WORKFLOW,
            "RunLatency", "Average", alias="평균", time_grain="PT5M"))
        .with_target(_azure_metric(
            "Microsoft.Logic/workflows", NOTIFICATION_WORKFLOW,
            "RunLatency", "Maximum", alias="최대", time_grain="PT5M"))
    )


def _scn07_timeout_ratio():
    """Timeout 비율 — KQL 로 ResultDescription 에 timeout 포함된 실패 / 전체."""
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        "| where TimeGenerated > ago(6h) "
        "| where status_s in ('Succeeded','Failed') "
        "| summarize 전체=count(), "
        "timeout실패=countif(status_s=='Failed' and "
        "(error_message_s contains 'timeout' or error_code_s contains 'Timeout')) "
        "| extend timeout비율 = iff(전체==0, real(null), "
        "round(100.0*timeout실패/전체, 2)) "
        "| project timeout비율"
    )
    p = pb.gauge_panel(
        "Logic App Timeout 비율 (%)",
        unit="percent",
        thresholds=pb._thresholds([(None, pb.GREEN), (1, pb.YELLOW), (5, pb.RED)]),
        minimum=0,
        maximum=100,
        span=pb.SPAN_QUARTER,
        decimals=2,
        description=(
            "AzureDiagnostics 6h — error_message/error_code 에 'timeout' 포함된 "
            "실패 / 전체 비율. > 5% 면 위험."
        ),
    )
    return p.datasource(_azure()).with_target(
        _azure_logs(kql, ResultFormat.TABLE)
    )


def _scn07_workflow_table():
    """워크플로별 실행 / 성공 / 실패 / timeout 상세."""
    kql = (
        "AzureDiagnostics "
        "| where ResourceProvider == 'MICROSOFT.LOGIC' "
        "| where TimeGenerated > ago(6h) "
        "| summarize 실행=count(), "
        "성공=countif(status_s=='Succeeded'), "
        "실패=countif(status_s=='Failed'), "
        "timeout=countif(status_s=='Failed' and "
        "(error_message_s contains 'timeout' or error_code_s contains 'Timeout')) "
        "by 워크플로=resource_workflowName_s "
        "| where 실행 > 0 "
        "| order by timeout desc, 실패 desc"
    )
    p = pb.table_panel(
        "워크플로별 실행/성공/실패/timeout (6h)",
        span=pb.SPAN_HALF,
        description="AzureDiagnostics — 워크플로별 timeout 상세.",
    )
    return p.datasource(_azure()).with_target(
        _azure_logs(kql, ResultFormat.TABLE)
    )


# ════════════════════════════════════════════════════════════════════════
# SCN-08 — GCP Cloud Function bookflow-bq-load 장애 (executions/errors +
#          GCS staging + BigQuery load jobs)
# ════════════════════════════════════════════════════════════════════════
def _scn08_intro():
    return _scn_intro(
        "SCN-08 · GCP Cloud Function `bookflow-bq-load` 장애 (장애/기능)",
        "Cloud Function **execution_count** · **execution_times** · status 별 "
        "에러 · **GCS staging 버킷** 파일 수 (대기열) · **BigQuery load job** "
        "성공/실패. bookflow-bq-load 함수 장애 시 status!=ok 스파이크 + GCS "
        "스테이징 파일 누적 + BQ 적재 row 정체.",
    )


def _scn08_bqload_executions():
    """bookflow-bq-load 호출 수 — status 별 분리."""
    p = pb.timeseries_panel(
        "bookflow-bq-load · 호출 수 (status 별)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=10,
        description=(
            "cloudfunctions function/execution_count · function_name="
            f"{GCP_CF_BQLOAD} · status 별 ALIGN_SUM. ok/error/timeout 등."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "cloudfunctions.googleapis.com/function/execution_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["metric.label.status"],
            extra_filters=[f'resource.label.function_name="{GCP_CF_BQLOAD}"'],
            alias="{{metric.label.status}}",
        )
    )


def _scn08_bqload_errors_stat():
    """bookflow-bq-load 에러 호출 stat."""
    p = pb.stat_panel(
        "bookflow-bq-load · 에러 호출",
        unit="short",
        color_mode=pb.BigValueColorMode.VALUE,
        thresholds=pb._thresholds([(None, pb.GREEN), (1, pb.YELLOW), (5, pb.RED)]),
        mappings=[_NODATA_MAP],
        description=(
            f"cloudfunctions execution_count · function_name={GCP_CF_BQLOAD} · "
            "status!=ok 합계. 0=정상."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "cloudfunctions.googleapis.com/function/execution_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            extra_filters=[
                f'resource.label.function_name="{GCP_CF_BQLOAD}"',
                'metric.label.status!="ok"',
            ],
            alias="에러 호출",
        )
    )


def _scn08_bqload_execution_times():
    """bookflow-bq-load 실행 시간 — execution_times."""
    p = pb.timeseries_panel(
        "bookflow-bq-load · 실행 시간 (분포)",
        unit="ns",
        span=pb.SPAN_HALF,
        description=(
            f"cloudfunctions function/execution_times · function_name={GCP_CF_BQLOAD}. "
            "timeout 임박 시 분포 상단 급등."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "cloudfunctions.googleapis.com/function/execution_times",
            aligner="ALIGN_PERCENTILE_95", reducer="REDUCE_MEAN",
            extra_filters=[f'resource.label.function_name="{GCP_CF_BQLOAD}"'],
            alias="p95 실행시간",
        )
    )


def _scn08_gcs_object_count():
    """GCS staging 버킷 객체 수 — 대기열 누적 신호."""
    p = pb.timeseries_panel(
        "GCS 버킷 객체 수 (staging 대기열)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "storage storage/object_count · 버킷별 객체 수. "
            "bq-load 장애 시 staging 객체 누적·BQ 적재 정체."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "storage.googleapis.com/storage/object_count",
            aligner="ALIGN_MEAN", reducer="REDUCE_SUM",
            group_bys=["resource.label.bucket_name"],
            alias="{{resource.label.bucket_name}}",
            alignment_period="+3600s",
        )
    )


def _scn08_bq_uploaded_rows():
    """BigQuery 적재 row 수 — bq-load 성공 결과."""
    p = pb.timeseries_panel(
        "BigQuery 적재 row 수 (bq-load 결과)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "bigquery storage/uploaded_row_count · ALIGN_SUM. bq-load 정상 "
            "동작 시 주기적 적재 — 정체 시 0 지속."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "bigquery.googleapis.com/storage/uploaded_row_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            alias="적재 row",
        )
    )


def _scn08_bq_queries():
    """BigQuery 쿼리 실행 수 — 적재 후 forecast 가 읽는 쿼리 흐름 추적."""
    p = pb.timeseries_panel(
        "BigQuery 쿼리 실행 수 (priority 별)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "bigquery query/count · priority 별. bq-load 후 forecast 쿼리 "
            "흐름. 적재 정체 시 쿼리도 0 으로 떨어진다."
        ),
    )
    return p.datasource(_gcp()).with_target(
        _gcp_ts(
            "bigquery.googleapis.com/query/count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["metric.label.priority"],
            alias="{{metric.label.priority}}",
        )
    )


# ════════════════════════════════════════════════════════════════════════
def dashboard() -> Dashboard:
    """Row 9 (시연 시나리오 SCN-01~08) 대시보드 빌더를 반환."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── SCN-01 기존도서 정상플로우 ────────────────────────────────
        .with_row(Row(
            "Row 9 · SCN-01 — 기존도서 정상플로우 (POS → Kinesis → "
            "pos-ingestor → RDS → forecast/decision/notification)"
        ))
        .with_panel(_scn01_intro())
        .with_panel(_scn01_kinesis_in())
        .with_panel(_scn01_kinesis_lag())
        .with_panel(_scn01_ingestor_invocations())
        .with_panel(_scn01_ingestor_duration())
        .with_panel(_scn01_svc_rps())
        .with_panel(_scn01_svc_p95())
        .with_panel(_scn01_svc_errors())
        .with_panel(_scn01_rds_connections())
        # ── SCN-02 신간도서 정상플로우 ────────────────────────────────
        .with_row(Row(
            "Row 9 · SCN-02 — 신간도서 정상플로우 (publisher-watcher → "
            "aladin-sync → new_book_requests → HQ 승인)"
        ))
        .with_panel(_scn02_intro())
        .with_panel(_scn02_aladin_invocations())
        .with_panel(_scn02_aladin_duration())
        .with_panel(_scn02_publisher_cronjob())
        .with_panel(_scn02_newbook_traffic())
        .with_panel(_scn02_rds_writeio())
        # ── SCN-03 EKS Node+Pod 오토스케일링 ──────────────────────────
        .with_row(Row(
            "Row 9 · SCN-03 — EKS Node+Pod 오토스케일링 (장애 시나리오)"
        ))
        .with_panel(_scn03_intro())
        .with_panel(_scn03_hpa_replicas())
        .with_panel(_scn03_node_count())
        .with_panel(_scn03_node_capacity())
        .with_panel(_scn03_pod_cpu())
        .with_panel(_scn03_pod_memory())
        .with_panel(_scn03_pod_restarts())
        # ── SCN-04 출판사 EC2 AutoScaling ─────────────────────────────
        .with_row(Row(
            "Row 9 · SCN-04 — 출판사 EC2 AutoScaling (장애 시나리오)"
        ))
        .with_panel(_scn04_intro())
        .with_panel(_scn04_asg_inservice_stat())
        .with_panel(_scn04_asg_capacity())
        .with_panel(_scn04_ec2_cpu())
        .with_panel(_scn04_ec2_statuscheck())
        # ── SCN-05 RDS 이중화 ─────────────────────────────────────────
        .with_row(Row(
            "Row 9 · SCN-05 — RDS 이중화 (장애 시나리오 · Multi-AZ failover)"
        ))
        .with_panel(_scn05_intro())
        .with_panel(_scn05_rds_connections_timeline())
        .with_panel(_scn05_rds_cpu())
        .with_panel(_scn05_rds_freeable_memory())
        .with_panel(_scn05_rds_replica_lag())
        .with_panel(_scn05_rds_dbload())
        .with_panel(_scn05_rds_iops())
        # ── SCN-06 VPN Active/Standby Failover ────────────────────────
        .with_row(Row(
            "Row 9 · SCN-06 — VPN Active/Standby Failover "
            "(장애 시나리오 · 4 터널)"
        ))
        .with_panel(_scn06_intro())
        .with_panel(_scn06_vpn_state_table())
        .with_panel(_scn06_vpn_state_azure())
        .with_panel(_scn06_vpn_tunnels_all())
        .with_panel(_scn06_vpn_traffic())
        .with_panel(_scn06_vpn_failover_events())
        # ── SCN-07 Logic App TIMEOUT ──────────────────────────────────
        .with_row(Row(
            "Row 9 · SCN-07 — Azure Logic App TIMEOUT (장애 시나리오)"
        ))
        .with_panel(_scn07_intro())
        .with_panel(_scn07_runs_started_completed())
        .with_panel(_scn07_notification_runs_metric())
        .with_panel(_scn07_run_duration())
        .with_panel(_scn07_timeout_ratio())
        .with_panel(_scn07_workflow_table())
        # ── SCN-08 GCP Cloud Function bookflow-bq-load 장애 ───────────
        .with_row(Row(
            "Row 9 · SCN-08 — GCP Cloud Function `bookflow-bq-load` 장애 "
            "(장애 시나리오)"
        ))
        .with_panel(_scn08_intro())
        .with_panel(_scn08_bqload_errors_stat())
        .with_panel(_scn08_bqload_executions())
        .with_panel(_scn08_bqload_execution_times())
        .with_panel(_scn08_gcs_object_count())
        .with_panel(_scn08_bq_uploaded_rows())
        .with_panel(_scn08_bq_queries())
    )
