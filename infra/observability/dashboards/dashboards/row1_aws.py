"""Row 1 — AWS 인프라 대시보드 (CloudWatch).

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 1) 기준:
  - EKS: 노드·8 Pod Ready/Restart · CronJob 성공/실패
  - ECS: 3 서비스 running/desired · task 헬스
  - RDS: 연결·CPU·스토리지·IOPS
  - ElastiCache Redis: 연결·메모리·evictions
  - Lambda ×6: 호출·에러·throttle·duration
  - Kinesis: incoming records·iterator age
  - ALB(External/Internal): 요청·5xx·target 헬스
  - CodePipeline ×3: 최근 실행
  - CloudTrail: API 활동·리소스 변경·비정상/실패 호출 (CloudWatch Logs)

데이터소스: 전부 CloudWatch (고정 UID `cloudwatch`). 리전 ap-northeast-1.

라이브 CloudWatch 실측 (2026-05-19 · 994878981869 — Grafana CloudWatch
datasource 가 가리키는 계정):
  - EKS 클러스터  : bookflow-eks  (AWS/EKS 컨트롤플레인 메트릭만 — Container
    Insights 미활성 → 노드/Pod 단위 메트릭 없음. 노드·Pod·CronJob 헬스는
    Prometheus(Row 4·Row 8)가 담당. 여기선 컨트롤플레인 메트릭으로 대체.)
  - ECS 클러스터  : bookflow-ecs  (서비스 inventory-api·online-sim·offline-sim
    · ECS/ContainerInsights 활성 → RunningTaskCount/DesiredTaskCount 사용 가능)
  - RDS          : bookflow-postgres (db.t3.micro)
  - ElastiCache  : bookflow-redis (node 0001)
  - Lambda       : bookflow-{pos-ingestor,spike-detect,aladin-sync,sns-gen,
                   event-sync,forecast-trigger} — 6개 (라이브 실재 함수)
  - Kinesis      : bookflow-pos-events
  - ALB          : bookflow-alb-external — app/bookflow-alb-external/57e62cdd02356761
                   (TargetGroup bookflow-inventory-api-tg)
  - CodePipeline : cp-eks·cp-ecs·publisher-bg — 메트릭 미발행(데일리 자원·미배포)
  - CloudTrail   : 트레일 미생성 → CloudWatch Logs 그룹 미존재
"""

from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchLogsQuery as CWLogs,
    CloudWatchMetricsQuery as CWMetrics,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.models.cloudwatch import (
    CloudWatchQueryMode,
    MetricEditorMode,
    MetricQueryType,
)

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row1-aws"
TITLE = "BookFlow 운영 — AWS 인프라"
DESCRIPTION = (
    "AWS 인프라 헬스 (CloudWatch · ap-northeast-1). EKS 컨트롤플레인 · ECS 3 "
    "서비스 · RDS · Redis · Lambda 7 · Kinesis · ALB · CodePipeline · "
    "CloudTrail. 노드/Pod 단위는 Row 4·Row 8(Prometheus)."
)

REGION = "ap-northeast-1"
EKS_CLUSTER = "bookflow-eks"
ECS_CLUSTER = "bookflow-ecs"
RDS_ID = "bookflow-postgres"
REDIS_ID = "bookflow-redis"
KINESIS_STREAM = "bookflow-pos-events"
# ALB ID — External ALB 는 데일리 destroy/create 라 식별자가 매일 회전한다.
# 2026-05-19 실측(elbv2 describe-load-balancers): 현재 활성 LB 가 데이터를
# 발행하는 식별자. 재배포 시 갱신 필요(IaC named LB / EventBridge 동적
# 주입이 항구 해법). HealthyHostCount 등 target 메트릭은 LoadBalancer +
# TargetGroup 2개 차원이 필요하다.
ALB_EXTERNAL = "app/bookflow-alb-external/57e62cdd02356761"
ALB_TARGET_GROUP = "targetgroup/bookflow-inventory-api-tg/45c463eca58f093b"

ECS_SERVICES = ["inventory-api", "online-sim", "offline-sim"]
LAMBDA_FUNCS = [
    "bookflow-pos-ingestor",
    "bookflow-spike-detect",
    "bookflow-aladin-sync",
    "bookflow-sns-gen",
    "bookflow-event-sync",
    "bookflow-forecast-trigger",
]
CODEPIPELINES = ["cp-eks", "cp-ecs", "publisher-bg"]
# CloudTrail → CloudWatch Logs 그룹 (트레일 CWLG 연동 시 부여 예정 명칭)
CLOUDTRAIL_LOG_GROUP = "/aws/cloudtrail/bookflow"


# ── CloudWatch 쿼리 헬퍼 ────────────────────────────────────────────────
def _metric(ref_id, namespace, metric, dims, stat="Average", period="300", label=""):
    """CloudWatch metrics 쿼리 빌더."""
    q = (
        CWMetrics()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(REGION)
        .namespace(namespace)
        .metric_name(metric)
        .dimensions(dims)
        .statistic(stat)
        .period(period)
        .match_exact(True)
        .ref_id(ref_id)
    )
    if label:
        q = q.label(label)
    return q


def _logs(ref_id, log_group, expression):
    """CloudWatch Logs Insights 쿼리 빌더 (CloudTrail 등)."""
    return (
        CWLogs()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.LOGS)
        .region(REGION)
        .log_group_names([log_group])
        .expression(expression)
        .ref_id(ref_id)
    )


# ── EKS ─────────────────────────────────────────────────────────────────
def _prom(ref_id, expr, label=""):
    """Prometheus 쿼리 빌더 — EKS 컨트롤플레인 등 CloudWatch 에 없는 메트릭."""
    q = PromQuery().datasource(ds.ref(ds.PROMETHEUS)).expr(expr).ref_id(ref_id)
    if label:
        q = q.legend_format(label)
    return q


def _eks_apiserver_requests():
    """EKS API server 요청률 — kube-apiserver Prometheus 계측."""
    p = pb.timeseries_panel(
        "EKS · API server 요청률",
        unit="reqps",
        description=(
            "apiserver_request_total rate (Prometheus kubernetes-apiservers 잡). "
            "AWS/EKS CloudWatch 미발행 — 컨트롤플레인 /metrics 스크레이프."
        ),
    )
    return p.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        _prom("A", 'sum(rate(apiserver_request_total{job="kubernetes-apiservers"}[5m]))',
              label="API 요청"),
    )


def _eks_apiserver_errors():
    """EKS API server 4XX/5XX/429 — kube-apiserver Prometheus 계측."""
    p = pb.timeseries_panel(
        "EKS · API server 에러 (4XX/5XX/429)",
        unit="reqps",
        description="apiserver_request_total code 별 rate (Prometheus) — 컨트롤플레인 이상.",
    )
    return (
        p.datasource(ds.ref(ds.PROMETHEUS))
        .with_target(_prom("A", 'sum(rate(apiserver_request_total{job="kubernetes-apiservers",code=~"4.."}[5m]))', label="4XX"))
        .with_target(_prom("B", 'sum(rate(apiserver_request_total{job="kubernetes-apiservers",code=~"5.."}[5m]))', label="5XX"))
        .with_target(_prom("C", 'sum(rate(apiserver_request_total{job="kubernetes-apiservers",code="429"}[5m]))', label="429"))
    )


def _eks_nodes_ready():
    """EKS Ready 노드 수 — Prometheus kubernetes-nodes 잡 up."""
    p = pb.stat_panel(
        "EKS · 노드 Ready",
        unit="short",
        thresholds=pb._thresholds([(None, pb.RED), (1, pb.YELLOW), (2, pb.GREEN)]),
        description=(
            "Ready 상태 노드 수 (Prometheus kubernetes-nodes 잡 up 합). "
            "Pod 미스케줄/단위 헬스는 Row 4·8 — kube-state-metrics 미설치."
        ),
    )
    return p.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        _prom("A", 'sum(up{job="kubernetes-nodes"})', label="Ready 노드"),
    )


# ── ECS ─────────────────────────────────────────────────────────────────
def _ecs_running_tasks():
    """ECS 3 서비스 running task 수 — desired 대비."""
    p = pb.timeseries_panel(
        "ECS · 서비스별 Running Task",
        unit="short",
        description="ECS/ContainerInsights RunningTaskCount — 3 서비스. desired 대비.",
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, svc in enumerate(ECS_SERVICES):
        p = p.with_target(_metric(
            f"R{i}", "ECS/ContainerInsights", "RunningTaskCount",
            {"ClusterName": ECS_CLUSTER, "ServiceName": svc},
            stat="Average", label=f"{svc} running"))
        p = p.with_target(_metric(
            f"D{i}", "ECS/ContainerInsights", "DesiredTaskCount",
            {"ClusterName": ECS_CLUSTER, "ServiceName": svc},
            stat="Average", label=f"{svc} desired"))
    return p


def _ecs_cpu_mem():
    """ECS CPU/메모리 사용률 — task 리소스 헬스."""
    p = pb.timeseries_panel(
        "ECS · CPU / 메모리 사용률",
        unit="percent",
        description="ECS/ContainerInsights CpuUtilized·MemoryUtilized — 3 서비스.",
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, svc in enumerate(ECS_SERVICES):
        p = p.with_target(_metric(
            f"C{i}", "ECS/ContainerInsights", "CpuUtilized",
            {"ClusterName": ECS_CLUSTER, "ServiceName": svc},
            stat="Average", label=f"{svc} CPU"))
        p = p.with_target(_metric(
            f"M{i}", "ECS/ContainerInsights", "MemoryUtilized",
            {"ClusterName": ECS_CLUSTER, "ServiceName": svc},
            stat="Average", label=f"{svc} Mem"))
    return p


# ── RDS ─────────────────────────────────────────────────────────────────
def _rds_connections():
    """RDS DB 연결 수."""
    p = pb.timeseries_panel(
        "RDS · DB 연결 수",
        unit="short",
        description="AWS/RDS DatabaseConnections — bookflow-postgres.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/RDS", "DatabaseConnections",
                {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="연결"),
    )


def _rds_cpu():
    """RDS CPU 사용률."""
    p = pb.timeseries_panel(
        "RDS · CPU 사용률",
        unit="percent",
        description="AWS/RDS CPUUtilization — bookflow-postgres.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/RDS", "CPUUtilization",
                {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="CPU"),
    )


def _rds_storage():
    """RDS 가용 스토리지."""
    p = pb.stat_panel(
        "RDS · 가용 스토리지",
        unit="bytes",
        color_mode=pb.BigValueColorMode.VALUE,
        thresholds=pb._thresholds([(None, pb.RED), (2e9, pb.YELLOW), (5e9, pb.GREEN)]),
        description="AWS/RDS FreeStorageSpace — bookflow-postgres.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/RDS", "FreeStorageSpace",
                {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="가용 스토리지"),
    )


def _rds_iops():
    """RDS IOPS — read/write."""
    p = pb.timeseries_panel(
        "RDS · IOPS (Read/Write)",
        unit="iops",
        description="AWS/RDS ReadIOPS·WriteIOPS — bookflow-postgres.",
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/RDS", "ReadIOPS",
                             {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="Read"))
        .with_target(_metric("B", "AWS/RDS", "WriteIOPS",
                             {"DBInstanceIdentifier": RDS_ID}, stat="Average", label="Write"))
    )


# ── ElastiCache Redis ───────────────────────────────────────────────────
def _redis_connections():
    """Redis 연결 수."""
    p = pb.timeseries_panel(
        "Redis · 연결 수",
        unit="short",
        description="AWS/ElastiCache CurrConnections — bookflow-redis.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/ElastiCache", "CurrConnections",
                {"CacheClusterId": REDIS_ID}, stat="Average", label="연결"),
    )


def _redis_memory():
    """Redis 메모리 사용률."""
    p = pb.timeseries_panel(
        "Redis · 메모리 사용률",
        unit="percent",
        description="AWS/ElastiCache DatabaseMemoryUsagePercentage — bookflow-redis.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/ElastiCache", "DatabaseMemoryUsagePercentage",
                {"CacheClusterId": REDIS_ID}, stat="Average", label="메모리%"),
    )


def _redis_evictions():
    """Redis evictions — 메모리 압박 신호."""
    p = pb.stat_panel(
        "Redis · Evictions",
        unit="short",
        thresholds=pb._thresholds([(None, pb.GREEN), (1, pb.YELLOW), (100, pb.RED)]),
        description="AWS/ElastiCache Evictions — bookflow-redis. >0 이면 메모리 압박.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/ElastiCache", "Evictions",
                {"CacheClusterId": REDIS_ID}, stat="Sum", label="evictions"),
    )


# ── Lambda ──────────────────────────────────────────────────────────────
def _lambda_invocations():
    """Lambda 7종 호출 수."""
    p = pb.timeseries_panel(
        "Lambda · 호출 수",
        unit="short",
        description="AWS/Lambda Invocations — bookflow Lambda 7종.",
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, fn in enumerate(LAMBDA_FUNCS):
        p = p.with_target(_metric(
            f"I{i}", "AWS/Lambda", "Invocations",
            {"FunctionName": fn}, stat="Sum",
            label=fn.replace("bookflow-", "")))
    return p


def _lambda_errors():
    """Lambda 7종 에러 + throttle."""
    p = pb.timeseries_panel(
        "Lambda · 에러 / Throttle",
        unit="short",
        description="AWS/Lambda Errors·Throttles — bookflow Lambda 7종.",
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, fn in enumerate(LAMBDA_FUNCS):
        short = fn.replace("bookflow-", "")
        p = p.with_target(_metric(
            f"E{i}", "AWS/Lambda", "Errors",
            {"FunctionName": fn}, stat="Sum", label=f"{short} err"))
        p = p.with_target(_metric(
            f"T{i}", "AWS/Lambda", "Throttles",
            {"FunctionName": fn}, stat="Sum", label=f"{short} throttle"))
    return p


def _lambda_duration():
    """Lambda 7종 평균 실행시간."""
    p = pb.timeseries_panel(
        "Lambda · 실행시간 (avg)",
        unit="ms",
        description="AWS/Lambda Duration(Average) — bookflow Lambda 7종.",
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, fn in enumerate(LAMBDA_FUNCS):
        p = p.with_target(_metric(
            f"D{i}", "AWS/Lambda", "Duration",
            {"FunctionName": fn}, stat="Average",
            label=fn.replace("bookflow-", "")))
    return p


# ── Kinesis ─────────────────────────────────────────────────────────────
def _kinesis_records():
    """Kinesis incoming records — POS 이벤트 유입."""
    p = pb.timeseries_panel(
        "Kinesis · 유입 레코드",
        unit="short",
        description="AWS/Kinesis IncomingRecords — bookflow-pos-events.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/Kinesis", "IncomingRecords",
                {"StreamName": KINESIS_STREAM}, stat="Sum", label="유입 레코드"),
    )


def _kinesis_iterator_age():
    """Kinesis iterator age — 소비 지연 신호."""
    p = pb.timeseries_panel(
        "Kinesis · Iterator Age (소비 지연)",
        unit="ms",
        description=(
            "AWS/Kinesis GetRecords.IteratorAgeMilliseconds — 소비자 지연. "
            "값이 크면 Glue/소비 Lambda 가 따라가지 못함."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds",
                {"StreamName": KINESIS_STREAM}, stat="Maximum", label="iterator age"),
    )


# ── ALB ─────────────────────────────────────────────────────────────────
def _alb_requests():
    """External ALB 요청 수."""
    p = pb.timeseries_panel(
        "ALB · 요청 수 (External)",
        unit="short",
        description=(
            "AWS/ApplicationELB RequestCount — bookflow-alb-external. "
            "Internal ALB 는 ingress-nginx NLB → Row 4(Prometheus)."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/ApplicationELB", "RequestCount",
                {"LoadBalancer": ALB_EXTERNAL}, stat="Sum", label="요청"),
    )


def _alb_5xx():
    """External ALB 5xx 에러."""
    p = pb.timeseries_panel(
        "ALB · 5xx 에러 (External)",
        unit="short",
        description="AWS/ApplicationELB HTTPCode_ELB_5XX_Count·HTTPCode_Target_5XX_Count.",
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/ApplicationELB", "HTTPCode_ELB_5XX_Count",
                             {"LoadBalancer": ALB_EXTERNAL}, stat="Sum", label="ELB 5xx"))
        .with_target(_metric("B", "AWS/ApplicationELB", "HTTPCode_Target_5XX_Count",
                             {"LoadBalancer": ALB_EXTERNAL}, stat="Sum", label="Target 5xx"))
    )


def _alb_targets():
    """External ALB healthy/unhealthy target 수."""
    p = pb.stat_panel(
        "ALB · Healthy Target",
        unit="short",
        color_mode=pb.BigValueColorMode.VALUE,
        thresholds=pb._thresholds([(None, pb.RED), (1, pb.GREEN)]),
        description=(
            "AWS/ApplicationELB HealthyHostCount — bookflow-alb-external · "
            "TargetGroup bookflow-inventory-api-tg. target 메트릭은 "
            "LoadBalancer+TargetGroup 2개 차원 필요."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/ApplicationELB", "HealthyHostCount",
                {"LoadBalancer": ALB_EXTERNAL, "TargetGroup": ALB_TARGET_GROUP},
                stat="Average", label="healthy"),
    )


# ── CodePipeline ────────────────────────────────────────────────────────
def _codepipeline_status():
    """CodePipeline 3종 실행 상태.

    CodePipeline 은 CloudWatch 메트릭을 네이티브 발행하지 않는다(EventBridge
    경유 커스텀 메트릭 필요). 데일리 자원이라 현재 미배포 → 데이터 없음.
    파이프라인 배포 시 SucceededPipeline/FailedPipeline 커스텀 메트릭으로 동작.
    """
    p = pb.stat_panel(
        "CodePipeline · 최근 실행 결과",
        unit="short",
        mappings=[],
        thresholds=pb.health_thresholds(),
        description=(
            "cp-eks·cp-ecs·publisher-bg. CodePipeline 은 CW 메트릭 네이티브 "
            "미발행 → EventBridge 커스텀 메트릭 필요. 데일리 자원·현재 미배포."
        ),
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, pl in enumerate(CODEPIPELINES):
        p = p.with_target(_metric(
            f"P{i}", "BookFlow/CodePipeline", "SucceededPipeline",
            {"PipelineName": pl}, stat="Maximum", label=pl))
    return p


# ── CloudTrail (CloudWatch Logs) ────────────────────────────────────────
def _cloudtrail_activity():
    """CloudTrail API 활동량 — 시간대별 호출 수.

    CloudTrail 트레일이 CloudWatch Logs 로 전달되도록 구성되면 동작한다.
    현재 deploy 계정에 트레일 미생성 → 로그 그룹 미존재(데이터 없음).
    """
    p = pb.timeseries_panel(
        "CloudTrail · API 활동량",
        unit="short",
        description=(
            f"CloudWatch Logs ({CLOUDTRAIL_LOG_GROUP}). 5분 bin API 호출 수. "
            "트레일→CWLogs 연동 시 동작 · 현재 트레일 미생성."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _logs("A", CLOUDTRAIL_LOG_GROUP,
              "fields @timestamp | stats count(*) as 호출수 by bin(5m)"),
    )


def _cloudtrail_changes():
    """CloudTrail 리소스 변경 이력 — 쓰기성 API 이벤트 목록."""
    p = pb.table_panel(
        "CloudTrail · 리소스 변경 이력",
        span=pb.SPAN_HALF,
        description=(
            f"CloudWatch Logs ({CLOUDTRAIL_LOG_GROUP}). Create*/Update*/Delete*/"
            "Put*/Modify* 이벤트 — 누가 무엇을 변경했나."
        ),
    )
    expr = (
        "fields @timestamp, eventName, eventSource, userIdentity.arn as actor "
        "| filter eventName like /^(Create|Update|Delete|Put|Modify|Run|Terminate)/ "
        "| sort @timestamp desc | limit 50"
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _logs("A", CLOUDTRAIL_LOG_GROUP, expr),
    )


def _cloudtrail_errors():
    """CloudTrail 비정상/실패 API 호출 — errorCode 존재 이벤트."""
    p = pb.table_panel(
        "CloudTrail · 실패 / 비정상 API 호출",
        span=pb.SPAN_HALF,
        description=(
            f"CloudWatch Logs ({CLOUDTRAIL_LOG_GROUP}). errorCode 존재 이벤트 "
            "(AccessDenied·UnauthorizedOperation 등) — 보안·감사."
        ),
    )
    expr = (
        "fields @timestamp, errorCode, eventName, eventSource, "
        "userIdentity.arn as actor, sourceIPAddress "
        "| filter ispresent(errorCode) "
        "| sort @timestamp desc | limit 50"
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _logs("A", CLOUDTRAIL_LOG_GROUP, expr),
    )


def dashboard() -> Dashboard:
    """Row 1 (AWS) 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── EKS ────────────────────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — EKS (컨트롤플레인)"))
        .with_panel(_eks_apiserver_requests())
        .with_panel(_eks_apiserver_errors())
        .with_panel(_eks_nodes_ready())
        # ── ECS ────────────────────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — ECS"))
        .with_panel(_ecs_running_tasks())
        .with_panel(_ecs_cpu_mem())
        # ── RDS / Redis ────────────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — RDS / ElastiCache"))
        .with_panel(_rds_connections())
        .with_panel(_rds_cpu())
        .with_panel(_rds_iops())
        .with_panel(_rds_storage())
        .with_panel(_redis_connections())
        .with_panel(_redis_memory())
        .with_panel(_redis_evictions())
        # ── Lambda / Kinesis ───────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — Lambda / Kinesis"))
        .with_panel(_lambda_invocations())
        .with_panel(_lambda_errors())
        .with_panel(_lambda_duration())
        .with_panel(_kinesis_records())
        .with_panel(_kinesis_iterator_age())
        # ── ALB / CodePipeline ─────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — ALB / CodePipeline"))
        .with_panel(_alb_requests())
        .with_panel(_alb_5xx())
        .with_panel(_alb_targets())
        .with_panel(_codepipeline_status())
        # ── CloudTrail ─────────────────────────────────────────────────
        .with_row(Row("Row 1 · AWS — CloudTrail (감사)"))
        .with_panel(_cloudtrail_activity())
        .with_panel(_cloudtrail_changes())
        .with_panel(_cloudtrail_errors())
    )
