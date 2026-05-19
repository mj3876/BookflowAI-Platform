"""Row 3 — GCP (Cloud Monitoring) 대시보드.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 3) 기준:
  - BigQuery: 쿼리·슬롯 · row 수·최신 적재
  - Vertex AI/BQML: 모델 상태·마지막 배치예측 시각
  - Cloud Functions ×3 (bq-load·feature-assemble·vertex-invoke): 호출·에러
  - Workflows: 실행 상태
  - GCS: 버킷 사용량

데이터소스: GCP Cloud Monitoring / stackdriver (고정 UID `gcp-monitoring`).
프로젝트는 라이브 GCP datasource(2026-05-19 실측)에서 확인:
  - defaultProject: project-8ab6bf05-54d2-4f5d-b8d

메트릭은 라이브 Cloud Monitoring 을 Grafana datasource(/api/ds/query)로
탐색해 실재·데이터 반환을 확인했다. 확인 결과는 각 패널 docstring 참조.

새 Row 모듈 패턴(README §"새 Row 추가 패턴")을 따른다:
  1. lib.meta.base_dashboard() 로 시작
  2. lib.panels.* 헬퍼로 패널 생성
  3. lib.datasources.ref() 로 데이터소스 지정
  4. dashboard() 함수 하나를 export (build.py 가 호출)
"""

from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.googlecloudmonitoring import (
    CloudMonitoringQuery,
    TimeSeriesList,
)

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row3-gcp"
TITLE = "BookFlow 운영 — GCP (Row 3)"
DESCRIPTION = (
    "GCP Cloud Monitoring 기반 GCP 리소스 운영 현황. BigQuery 쿼리/슬롯/row/적재 · "
    "Vertex AI 예측 · Cloud Functions 3개 호출·에러 · Workflows 실행 · GCS 버킷. "
    "project-8ab6bf05-54d2-4f5d-b8d."
)

# ── 라이브 GCP 좌표 (2026-05-19 datasource jsonData 실측) ────────────────
PROJECT = "project-8ab6bf05-54d2-4f5d-b8d"


# ── Cloud Monitoring 쿼리 헬퍼 ──────────────────────────────────────────
def _ts_query(
    metric_type: str, *,
    aligner: str = "ALIGN_RATE",
    reducer: str = "REDUCE_SUM",
    group_bys: list[str] | None = None,
    extra_filters: list[str] | None = None,
    alias: str = "",
    alignment_period: str = "+300s",
) -> CloudMonitoringQuery:
    """단일 Cloud Monitoring 메트릭의 timeSeriesList 쿼리 빌더."""
    filters = [f'metric.type="{metric_type}"']
    if extra_filters:
        filters.extend(extra_filters)
    tsl = (
        TimeSeriesList()
        .project_name(PROJECT)
        .filters(filters)
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
        .datasource(ds.ref(ds.GCP_MONITORING))
    )
    if alias:
        q = q.alias_by(alias)
    return q


# ── BigQuery ───────────────────────────────────────────────────────────
def _bq_queries() -> object:
    """BigQuery 쿼리 실행 수 추세.

    메트릭: bigquery.googleapis.com/query/count (GAUGE/INT64 · delta count).
    라이브 확인: OK · 데이터 반환 (2 series · 28 pts / 24h).
    GAUGE delta 라 ALIGN_SUM 으로 구간 합산한다.
    """
    panel = pb.timeseries_panel(
        "BigQuery 쿼리 실행 수",
        unit="short",
        span=pb.SPAN_HALF,
        description="bigquery query/count · ALIGN_SUM (구간 쿼리 건수 · priority 별).",
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "bigquery.googleapis.com/query/count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["metric.label.priority"],
            alias="{{metric.label.priority}}",
        )
    )


def _bq_slots() -> object:
    """BigQuery 프로젝트 할당 슬롯 추세.

    메트릭: bigquery.googleapis.com/slots/allocated_for_project (GAUGE).
    라이브 확인: OK · 데이터 반환 (1 series · 8 pts / 24h).
    """
    panel = pb.timeseries_panel(
        "BigQuery 할당 슬롯",
        unit="short",
        span=pb.SPAN_HALF,
        description="bigquery slots/allocated_for_project · ALIGN_MEAN.",
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "bigquery.googleapis.com/slots/allocated_for_project",
            aligner="ALIGN_MEAN", reducer="REDUCE_MEAN",
            alias="할당 슬롯",
        )
    )


def _bq_table_rows() -> object:
    """BigQuery 데이터셋 테이블 row 수 (sales_fact / forecast_results 등).

    메트릭: bigquery.googleapis.com/storage/table_count (GAUGE).
    라이브 확인: OK · 데이터 반환 (22 pts / 24h).
    개별 테이블 row 수는 Cloud Monitoring 플랫폼 메트릭으로 노출되지 않아
    (table 단위 row 메트릭 없음) 데이터셋 테이블 수로 적재 현황을 대신 본다.
    Notion 명세의 'row 수'는 BQML/INFORMATION_SCHEMA 쿼리 영역 — Cloud
    Monitoring 한계로 테이블 수 카운트로 조정.
    """
    panel = pb.stat_panel(
        "BigQuery 테이블 수 (적재 현황)",
        unit="short",
        thresholds=pb._thresholds([(None, pb.YELLOW), (1, pb.GREEN)]),
        span=pb.SPAN_QUARTER,
        description=(
            "bigquery storage/table_count · 데이터셋 내 테이블 수. "
            "테이블별 row 수 플랫폼 메트릭 부재 → 테이블 수로 적재 현황 대체."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "bigquery.googleapis.com/storage/table_count",
            aligner="ALIGN_MEAN", reducer="REDUCE_SUM",
            alias="테이블 수",
        )
    )


def _bq_uploaded_rows() -> object:
    """BigQuery 적재 row 수 추세 — 최신 적재 확인용.

    메트릭: bigquery.googleapis.com/storage/uploaded_row_count (DELTA).
    라이브 확인: OK · 데이터 반환 (3 pts / 7d · streaming/load insert 시점만).
    추세상 마지막 데이터 포인트 시각 = 최신 적재 시각.
    """
    panel = pb.timeseries_panel(
        "BigQuery 적재 row 수 (최신 적재)",
        unit="short",
        span=pb.SPAN_QUARTER,
        fill_opacity=20,
        description=(
            "bigquery storage/uploaded_row_count · ALIGN_SUM. "
            "마지막 포인트 시각이 최신 적재 시각."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "bigquery.googleapis.com/storage/uploaded_row_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            alias="적재 row",
        )
    )


# ── Vertex AI / BQML ───────────────────────────────────────────────────
def _vertex_predictions() -> object:
    """Vertex AI 온라인 예측 호출 수 — 신간 실시간 추론.

    메트릭: aiplatform.googleapis.com/prediction/online/prediction_count.
    라이브 확인: 메트릭은 유효(쿼리 통과)하나 데이터 0 — 현재 Vertex 온라인
    Endpoint 가 트래픽을 받지 않은 상태(배치예측 위주 운영). Endpoint 가
    추론을 받기 시작하면 본 패널이 즉시 동작한다.

    NOTE: Notion 명세의 '배치예측 시각'에 해당하는
    aiplatform.googleapis.com/prediction/batch/* 메트릭은 라이브에서 404
    (존재하지 않음) — Vertex 배치예측 전용 Cloud Monitoring 메트릭이 없다.
    배치예측 추적은 Workflows 실행(아래 패널) / Cloud Function vertex-invoke
    호출(아래 패널)로 대체한다.
    """
    panel = pb.timeseries_panel(
        "Vertex AI 온라인 예측 호출 수",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "aiplatform prediction/online/prediction_count · ALIGN_SUM. "
            "온라인 Endpoint 추론 트래픽 (현재 데이터 0 · 배치 위주 운영)."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "aiplatform.googleapis.com/prediction/online/prediction_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            alias="예측 호출",
        )
    )


# ── Cloud Functions ×3 ─────────────────────────────────────────────────
def _cf_executions() -> object:
    """Cloud Functions 3개 호출 수 — bq-load·feature-assemble·vertex-invoke.

    메트릭: cloudfunctions.googleapis.com/function/execution_count (DELTA).
    라이브 확인: OK · 데이터 반환 (9 pts / 24h).
    function_name 라벨로 3개 함수를 시리즈 분리.
    """
    panel = pb.timeseries_panel(
        "Cloud Functions 호출 수 (3개)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "cloudfunctions function/execution_count · ALIGN_SUM. "
            "bq-load·feature-assemble·vertex-invoke 함수별."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "cloudfunctions.googleapis.com/function/execution_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["resource.label.function_name"],
            alias="{{resource.label.function_name}}",
        )
    )


def _cf_errors() -> object:
    """Cloud Functions 에러 호출 수 — status 라벨이 'ok' 아닌 호출.

    메트릭: cloudfunctions.googleapis.com/function/execution_count.
    동일 메트릭을 status!=ok 필터로 좁혀 에러만 집계.
    """
    panel = pb.timeseries_panel(
        "Cloud Functions 에러 호출 (3개)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "cloudfunctions function/execution_count · status!=ok 필터. "
            "함수별 실패 호출 수."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "cloudfunctions.googleapis.com/function/execution_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["resource.label.function_name"],
            extra_filters=['metric.label.status!="ok"'],
            alias="{{resource.label.function_name}}",
        )
    )


# ── Workflows ──────────────────────────────────────────────────────────
def _workflows_executions() -> object:
    """Workflows 실행 완료 수 — 상태별 (SUCCEEDED/FAILED 등).

    메트릭: workflows.googleapis.com/finished_execution_count (DELTA).
    라이브 확인: OK · 데이터 반환 (24 pts / 24h).
    status 라벨로 성공/실패 분리. POS→BQ→forecast 파이프라인 오케스트레이션
    및 Vertex 배치예측 트리거 추적의 핵심 신호.
    """
    panel = pb.timeseries_panel(
        "Workflows 실행 완료 (상태별)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "workflows finished_execution_count · ALIGN_SUM · status 별. "
            "데이터 파이프라인/배치예측 오케스트레이션 실행 결과."
        ),
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "workflows.googleapis.com/finished_execution_count",
            aligner="ALIGN_SUM", reducer="REDUCE_SUM",
            group_bys=["metric.label.status"],
            alias="{{metric.label.status}}",
        )
    )


# ── GCS ────────────────────────────────────────────────────────────────
def _gcs_bucket_bytes() -> object:
    """GCS 버킷 사용량 (총 바이트) — 버킷별.

    메트릭: storage.googleapis.com/storage/total_bytes (GAUGE).
    라이브 확인: OK · 데이터 반환 (24 pts / 24h).
    """
    panel = pb.timeseries_panel(
        "GCS 버킷 사용량",
        unit="bytes",
        span=pb.SPAN_HALF,
        description="storage storage/total_bytes · ALIGN_MEAN · 버킷별.",
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "storage.googleapis.com/storage/total_bytes",
            aligner="ALIGN_MEAN", reducer="REDUCE_SUM",
            group_bys=["resource.label.bucket_name"],
            alias="{{resource.label.bucket_name}}",
            alignment_period="+3600s",
        )
    )


def _gcs_object_count() -> object:
    """GCS 버킷 객체 수.

    메트릭: storage.googleapis.com/storage/object_count (GAUGE).
    라이브 확인: OK · 데이터 반환 (24 pts / 24h).
    """
    panel = pb.stat_panel(
        "GCS 객체 수 (전체 버킷)",
        unit="short",
        thresholds=pb._thresholds([(None, pb.YELLOW), (1, pb.GREEN)]),
        span=pb.SPAN_QUARTER,
        description="storage storage/object_count · 전체 버킷 합계.",
    )
    return panel.datasource(ds.ref(ds.GCP_MONITORING)).with_target(
        _ts_query(
            "storage.googleapis.com/storage/object_count",
            aligner="ALIGN_MEAN", reducer="REDUCE_SUM",
            alias="객체 수",
            alignment_period="+3600s",
        )
    )


def dashboard() -> Dashboard:
    """Row 3 (GCP) 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 3 · GCP ────────────────────────────────────────────────
        .with_row(Row("Row 3 · GCP (Cloud Monitoring)"))
        # BigQuery
        .with_panel(_bq_queries())
        .with_panel(_bq_slots())
        .with_panel(_bq_table_rows())
        .with_panel(_bq_uploaded_rows())
        # Vertex AI / BQML
        .with_panel(_vertex_predictions())
        # Cloud Functions ×3
        .with_panel(_cf_executions())
        .with_panel(_cf_errors())
        # Workflows
        .with_panel(_workflows_executions())
        # GCS
        .with_panel(_gcs_bucket_bytes())
        .with_panel(_gcs_object_count())
    )
