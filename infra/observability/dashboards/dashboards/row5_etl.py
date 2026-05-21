"""Row 5 — ETL / 데이터 파이프라인 대시보드 (CloudWatch).

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 5) 기준:
  - Glue: ETL job 실행상태(성공/실패/실행중)·마지막 성공·duration·DPU 사용량 ·
    job 에러+사유 · Crawler 실행·카탈로그 갱신 · Data Catalog DB/테이블/파티션
  - 흐름: POS → pos-ingestor Lambda → Kinesis → Glue ETL → S3 Mart →
    mart-to-gcs → GCS → BQ load → forecast → RDS forecast_cache
    — 단계별 입력레코드·산출물·지연·실패
  - 데이터 신선도: S3 Mart 최신 갱신 · BQ forecast 최신 · forecast_cache
    동기화 시각 · POS→BQ end-to-end 지연

데이터소스: 전부 CloudWatch (고정 UID `cloudwatch`). 리전 ap-northeast-1.
  - Glue job 메트릭은 커스텀 `Glue` 네임스페이스(JobName/JobRunId 차원).
  - 파이프라인 단계는 Lambda·Kinesis·S3 네이티브 메트릭을 단계별로 묶음.
  - GCS·BQ·forecast_cache 단계는 GCP/RDS 영역 → Row 3·Row 5 텍스트로만
    표기하고, AWS→GCP 경계인 mart-to-gcs Lambda 까지를 본 대시보드가 커버.

라이브 CloudWatch 실측 (2026-05-19 · 994878981869 — Grafana CloudWatch
datasource 가 가리키는 계정):
  - Glue jobs : bookflow-raw-pos-mart·raw-aladin-mart·raw-event-mart·
    raw-sns-mart·rds-inventory-mart·rds-inventory-seed·rds-locations-mart·
    rds-store-location-map-mart·sales-daily-agg·features-build (10개)
  - Glue 커스텀 ns `Glue`: glue.driver.aggregate.{recordsRead,bytesRead,
    elapsedTime,numCompletedTasks,numFailedTasks} — JobName/JobRunId 차원
  - AWS/Glue ResourceUsage: Type=count/gauge — DPU·잡 카운트
  - Crawler/Data Catalog: AWS/Glue 는 Crawler·Catalog 메트릭 네이티브 미발행
    → CloudWatch Logs(/aws-glue/jobs/error) 또는 카운트 패널로 대체
  - S3 : bookflow-mart-994878981869 · bookflow-raw-994878981869
    (BucketSizeBytes·NumberOfObjects — 일 1회 갱신. mart 버킷은 현재
    비어 있어 S3 메트릭 미게시 · raw 버킷은 데이터 게시)
  - Lambda 파이프라인: bookflow-pos-ingestor(POS→Kinesis) ·
    bookflow-mart-to-gcs(S3 Mart→GCS) · bookflow-forecast-trigger
  - Kinesis : bookflow-pos-events
"""

from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchLogsQuery as CWLogs,
    CloudWatchMetricsQuery as CWMetrics,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.models.cloudwatch import (
    CloudWatchQueryMode,
    MetricEditorMode,
    MetricQueryType,
)

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row5-etl"
TITLE = "BookFlow 운영 — ETL 데이터 파이프라인"
DESCRIPTION = (
    "POS → Kinesis → Glue ETL → S3 Mart → GCS → BQ → forecast 파이프라인 "
    "관측 (CloudWatch · ap-northeast-1). Glue job 상태·duration·에러 · 단계별 "
    "유입/산출/지연 · 데이터 신선도. GCS/BQ 단계는 Row 3(GCP) 참조."
)

REGION = "ap-northeast-1"
KINESIS_STREAM = "bookflow-pos-events"
# S3 버킷 — 계정 suffix 는 배포 계정마다 다름 (admin 994.. / deploy 354..).
# placeholder __AWS_ACCOUNT__ 를 _apply_grafana_dashboards 가 configmap 배포 시
# 현재 STS 계정 ID 로 치환 → admin/deploy 어느 계정이든 자동 정합.
S3_MART = "bookflow-mart-__AWS_ACCOUNT__"
S3_RAW = "bookflow-raw-__AWS_ACCOUNT__"
GLUE_ERROR_LOG_GROUP = "/aws-glue/jobs/error"
GLUE_OUTPUT_LOG_GROUP = "/aws-glue/jobs/output"

# 파이프라인 핵심 Glue ETL job (POS→Mart 흐름 + 집계/피처)
GLUE_PIPELINE_JOBS = [
    "bookflow-raw-pos-mart",
    "bookflow-raw-aladin-mart",
    "bookflow-raw-event-mart",
    "bookflow-raw-sns-mart",
    "bookflow-sales-daily-agg",
    "bookflow-features-build",
]
# 파이프라인 단계 Lambda
LAMBDA_POS_INGESTOR = "bookflow-pos-ingestor"   # POS → Kinesis
LAMBDA_MART_TO_GCS = "bookflow-mart-to-gcs"     # S3 Mart → GCS
LAMBDA_FORECAST_TRIGGER = "bookflow-forecast-trigger"  # forecast 트리거


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
    """CloudWatch Logs Insights 쿼리 빌더."""
    return (
        CWLogs()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.LOGS)
        .region(REGION)
        .log_group_names([log_group])
        .expression(expression)
        .ref_id(ref_id)
    )


# ── Glue — job 상태 / 처리량 / DPU ──────────────────────────────────────
def _glue_records_read():
    """Glue ETL job 별 읽은 레코드 수 — 처리량 추세."""
    p = pb.timeseries_panel(
        "Glue · job 별 처리 레코드",
        unit="short",
        description=(
            "커스텀 ns `Glue` glue.driver.aggregate.recordsRead — JobName 차원. "
            "각 ETL job 이 처리한 입력 레코드 수."
        ),
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, job in enumerate(GLUE_PIPELINE_JOBS):
        p = p.with_target(_metric(
            f"R{i}", "Glue", "glue.driver.aggregate.recordsRead",
            {"JobName": job, "JobRunId": "ALL", "Type": "count"},
            stat="Sum", label=job.replace("bookflow-", "")))
    return p


def _glue_duration():
    """Glue ETL job 별 실행시간 (elapsedTime)."""
    p = pb.timeseries_panel(
        "Glue · job 별 실행시간",
        unit="ms",
        description=(
            "커스텀 ns `Glue` glue.driver.aggregate.elapsedTime — JobName 차원. "
            "각 ETL job duration. 급증 시 데이터량/리소스 점검."
        ),
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, job in enumerate(GLUE_PIPELINE_JOBS):
        p = p.with_target(_metric(
            f"D{i}", "Glue", "glue.driver.aggregate.elapsedTime",
            {"JobName": job, "JobRunId": "ALL", "Type": "count"},
            stat="Maximum", label=job.replace("bookflow-", "")))
    return p


def _glue_failed_tasks():
    """Glue ETL job 별 실패 task 수 — job 에러 신호."""
    p = pb.stat_panel(
        "Glue · 실패 Task 합계",
        unit="short",
        thresholds=pb._thresholds([(None, pb.GREEN), (1, pb.YELLOW), (10, pb.RED)]),
        description=(
            "커스텀 ns `Glue` glue.driver.aggregate.numFailedTasks 전 job 합계. "
            ">0 이면 ETL job 내 task 실패 발생 — 에러 사유는 우측 패널."
        ),
    )
    p = p.datasource(ds.ref(ds.CLOUDWATCH))
    for i, job in enumerate(GLUE_PIPELINE_JOBS):
        p = p.with_target(_metric(
            f"F{i}", "Glue", "glue.driver.aggregate.numFailedTasks",
            {"JobName": job, "JobRunId": "ALL", "Type": "count"},
            stat="Sum", label=job.replace("bookflow-", "")))
    return p


def _glue_dpu():
    """Glue DPU 사용량 — ResourceUsage(JobRun)."""
    p = pb.timeseries_panel(
        "Glue · DPU 사용량 (JobRun)",
        unit="short",
        description=(
            "AWS/Glue ResourceUsage — Type=Resource·Resource=JobRun. "
            "ETL job run 의 DPU·시간 리소스 소비."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/Glue", "ResourceUsage",
                {"Type": "Resource", "Resource": "JobRun", "Service": "Glue",
                 "Class": "None"},
                stat="Sum", label="DPU·JobRun"),
    )


def _glue_errors_table():
    """Glue job 에러 + 사유 — /aws-glue/jobs/error 로그."""
    p = pb.table_panel(
        "Glue · job 에러 / 사유",
        span=pb.SPAN_HALF,
        description=(
            f"CloudWatch Logs ({GLUE_ERROR_LOG_GROUP}). ERROR/Exception/Traceback "
            "로그 라인 — 어느 job 이 왜 실패했나."
        ),
    )
    expr = (
        "fields @timestamp, @logStream, @message "
        "| filter @message like /(?i)(error|exception|traceback|failed)/ "
        "| sort @timestamp desc | limit 50"
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _logs("A", GLUE_ERROR_LOG_GROUP, expr),
    )


def _glue_run_volume():
    """Glue job 실행 빈도 — output 로그 기준 시간대별 run 수.

    Crawler 실행·Data Catalog DB/테이블/파티션 수는 AWS/Glue 가 네이티브
    메트릭을 발행하지 않는다 → 본 패널은 job run 활동량으로 대체하고,
    카탈로그 통계는 Glue GetCrawlerMetrics API(EventBridge 커스텀 메트릭)
    연결 시 추가한다.
    """
    p = pb.timeseries_panel(
        "Glue · job run 활동량",
        unit="short",
        description=(
            f"CloudWatch Logs ({GLUE_OUTPUT_LOG_GROUP}) 시간대별 로그스트림 수. "
            "Crawler·Data Catalog 통계는 GetCrawlerMetrics 커스텀 메트릭 필요."
        ),
    )
    # Logs Insights 파서는 ASCII 컬럼명만 허용 — 한글 alias(run수) 시
    # MalformedQueryException 400 (라이브 검증). ASCII 로 작성.
    expr = (
        "fields @logStream | stats count_distinct(@logStream) as runs by bin(1h)"
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _logs("A", GLUE_OUTPUT_LOG_GROUP, expr),
    )


# ── 파이프라인 단계별 — POS → Kinesis → Glue → S3 Mart ──────────────────
def _stage_pos_ingestor():
    """① POS → pos-ingestor Lambda — 호출/에러."""
    p = pb.timeseries_panel(
        "① POS → pos-ingestor (Lambda)",
        unit="short",
        description="AWS/Lambda Invocations·Errors — bookflow-pos-ingestor. POS 수집 단계.",
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/Lambda", "Invocations",
                             {"FunctionName": LAMBDA_POS_INGESTOR}, stat="Sum", label="호출"))
        .with_target(_metric("B", "AWS/Lambda", "Errors",
                             {"FunctionName": LAMBDA_POS_INGESTOR}, stat="Sum", label="에러"))
    )


def _stage_kinesis():
    """② Kinesis — 유입 레코드 + iterator age(지연)."""
    p = pb.timeseries_panel(
        "② Kinesis 유입 / 소비 지연",
        unit="short",
        description=(
            "AWS/Kinesis IncomingRecords·GetRecords.IteratorAgeMilliseconds — "
            "bookflow-pos-events. iterator age 가 크면 Glue 소비 지연."
        ),
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/Kinesis", "IncomingRecords",
                             {"StreamName": KINESIS_STREAM}, stat="Sum", label="유입 레코드"))
        .with_target(_metric("B", "AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds",
                             {"StreamName": KINESIS_STREAM}, stat="Maximum",
                             label="iterator age(ms)"))
    )


def _stage_glue_throughput():
    """③ Glue ETL — POS mart job 처리 레코드/실패."""
    p = pb.timeseries_panel(
        "③ Glue ETL (raw-pos-mart) 처리/실패",
        unit="short",
        description=(
            "커스텀 ns `Glue` recordsRead·numFailedTasks — bookflow-raw-pos-mart. "
            "Kinesis→S3 Mart 변환 단계의 산출/실패."
        ),
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "Glue", "glue.driver.aggregate.recordsRead",
                             {"JobName": "bookflow-raw-pos-mart", "JobRunId": "ALL",
                              "Type": "count"}, stat="Sum", label="처리 레코드"))
        .with_target(_metric("B", "Glue", "glue.driver.aggregate.numFailedTasks",
                             {"JobName": "bookflow-raw-pos-mart", "JobRunId": "ALL",
                              "Type": "count"}, stat="Sum", label="실패 task"))
    )


def _stage_mart_to_gcs():
    """④ S3 Mart → mart-to-gcs Lambda — GCS 전송 단계 (AWS→GCP 경계)."""
    p = pb.timeseries_panel(
        "④ S3 Mart → GCS (mart-to-gcs Lambda)",
        unit="short",
        description=(
            "AWS/Lambda Invocations·Errors·Duration — bookflow-mart-to-gcs. "
            "AWS→GCP 경계. 이후 BQ load·forecast 는 Row 3(GCP)."
        ),
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/Lambda", "Invocations",
                             {"FunctionName": LAMBDA_MART_TO_GCS}, stat="Sum", label="호출"))
        .with_target(_metric("B", "AWS/Lambda", "Errors",
                             {"FunctionName": LAMBDA_MART_TO_GCS}, stat="Sum", label="에러"))
        .with_target(_metric("C", "AWS/Lambda", "Duration",
                             {"FunctionName": LAMBDA_MART_TO_GCS}, stat="Average",
                             label="duration(ms)"))
    )


def _stage_forecast_trigger():
    """⑤ forecast 트리거 Lambda — BQ forecast → RDS forecast_cache 단계."""
    p = pb.timeseries_panel(
        "⑤ forecast-trigger (Lambda)",
        unit="short",
        description=(
            "AWS/Lambda Invocations·Errors — bookflow-forecast-trigger. "
            "forecast 산출 → RDS forecast_cache 동기화 트리거."
        ),
    )
    return (
        p.datasource(ds.ref(ds.CLOUDWATCH))
        .with_target(_metric("A", "AWS/Lambda", "Invocations",
                             {"FunctionName": LAMBDA_FORECAST_TRIGGER}, stat="Sum",
                             label="호출"))
        .with_target(_metric("B", "AWS/Lambda", "Errors",
                             {"FunctionName": LAMBDA_FORECAST_TRIGGER}, stat="Sum",
                             label="에러"))
    )


# ── 데이터 신선도 ───────────────────────────────────────────────────────
def _freshness_mart_objects():
    """S3 Mart 객체 수 — 산출물 누적 (신선도 대리 신호)."""
    p = pb.timeseries_panel(
        "데이터 신선도 · S3 Mart 객체 수",
        unit="short",
        description=(
            "AWS/S3 NumberOfObjects — bookflow-mart 버킷. ETL 산출물 누적. "
            "일 1회 갱신 메트릭 — 증가가 멈추면 ETL 정체 신호."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/S3", "NumberOfObjects",
                {"BucketName": S3_MART, "StorageType": "AllStorageTypes"},
                stat="Average", period="86400", label="Mart 객체 수"),
    )


def _freshness_mart_size():
    """S3 Mart 버킷 크기 — 산출물 누적 용량."""
    p = pb.timeseries_panel(
        "데이터 신선도 · S3 Mart 크기",
        unit="bytes",
        description="AWS/S3 BucketSizeBytes — bookflow-mart. 일 1회 갱신.",
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/S3", "BucketSizeBytes",
                {"BucketName": S3_MART, "StorageType": "StandardStorage"},
                stat="Average", period="86400", label="Mart 크기"),
    )


def _freshness_raw_objects():
    """S3 Raw 객체 수 — 원천 적재량 (POS/aladin/event/sns)."""
    p = pb.timeseries_panel(
        "데이터 신선도 · S3 Raw 객체 수",
        unit="short",
        description=(
            "AWS/S3 NumberOfObjects — bookflow-raw. POS/aladin/event/sns 원천 "
            "적재량. 증가 정체 시 수집 단계(Lambda/Firehose) 점검."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/S3", "NumberOfObjects",
                {"BucketName": S3_RAW, "StorageType": "AllStorageTypes"},
                stat="Average", period="86400", label="Raw 객체 수"),
    )


def _freshness_pipeline_lag():
    """end-to-end 지연 신호 — Kinesis iterator age (POS→소비 지연 대리).

    POS→BQ end-to-end 지연의 완전한 측정은 GCP 측(BQ load·forecast 시각)
    메트릭이 필요하다 → Row 3 가 BQ 적재일·forecast 최신을 담당.
    AWS 구간 지연은 Kinesis iterator age 가 가장 강한 단일 신호이므로
    여기 신선도 섹션에 stat 으로 재노출한다.
    """
    p = pb.stat_panel(
        "데이터 신선도 · 파이프라인 지연 (Kinesis lag)",
        unit="ms",
        color_mode=pb.BigValueColorMode.VALUE,
        thresholds=pb._thresholds([(None, pb.GREEN), (60000, pb.YELLOW),
                                   (300000, pb.RED)]),
        description=(
            "AWS/Kinesis GetRecords.IteratorAgeMilliseconds — AWS 구간 소비 "
            "지연. POS→BQ 전구간 지연은 Row 3(BQ 적재일)과 함께 판단."
        ),
    )
    return p.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        _metric("A", "AWS/Kinesis", "GetRecords.IteratorAgeMilliseconds",
                {"StreamName": KINESIS_STREAM}, stat="Maximum", label="지연(ms)"),
    )


def dashboard() -> Dashboard:
    """Row 5 (ETL/데이터 파이프라인) 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Glue ───────────────────────────────────────────────────────
        .with_row(Row("Row 5 · ETL — Glue (job 상태 / 처리량 / DPU)"))
        .with_panel(_glue_records_read())
        .with_panel(_glue_duration())
        .with_panel(_glue_dpu())
        .with_panel(_glue_failed_tasks())
        .with_panel(_glue_run_volume())
        .with_panel(_glue_errors_table())
        # ── 파이프라인 단계별 ──────────────────────────────────────────
        .with_row(Row("Row 5 · ETL — 파이프라인 단계 (POS→Kinesis→Glue→Mart→GCS)"))
        .with_panel(_stage_pos_ingestor())
        .with_panel(_stage_kinesis())
        .with_panel(_stage_glue_throughput())
        .with_panel(_stage_mart_to_gcs())
        .with_panel(_stage_forecast_trigger())
        # ── 데이터 신선도 ──────────────────────────────────────────────
        .with_row(Row("Row 5 · ETL — 데이터 신선도"))
        .with_panel(_freshness_mart_objects())
        .with_panel(_freshness_mart_size())
        .with_panel(_freshness_raw_objects())
        .with_panel(_freshness_pipeline_lag())
    )
