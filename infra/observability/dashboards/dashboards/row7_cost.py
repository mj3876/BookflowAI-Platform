"""Row 7 — 멀티클라우드 비용 대시보드.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 7) 기준:
  - AWS·Azure·GCP 비용 상세 (서비스별)
  - $203/월 예산 추적 (영구 $16 · daily $111 · phase $77 분해)

데이터소스 (Notion 명세 + 본 task 지시):
  - AWS 비용: CloudWatch AWS/Billing EstimatedCharges (us-east-1 · USD).
    AWS Billing 메트릭은 us-east-1 글로벌 엔드포인트에만 게시된다.
  - Azure/GCP 비용: 비용 메트릭(`bookflow_cloud_cost_usd`)이 아직 파이프라인
    미연결 (Row 0 레퍼런스에서 확인). 패널 골격은 명세대로 완성하되 expr 은
    placeholder vector(0) — exporter 연결 시 그대로 동작.

미연결 항목 명시:
  - Azure Cost Management / GCP Billing → Prometheus push 미연결.
  - AWS Billing EstimatedCharges 의 ServiceName dimension 값이 라이브에서
    빈 배열 (2026-05-19 실측) — billing alert/per-service 게시 미설정.
    Currency=USD 총액은 게시되면 동작. 서비스별 분해는 exporter 보강 필요.
  - 예산 3분해(영구$16·daily$111·phase$77)는 라벨링된 cost 메트릭이 필요 —
    `bookflow_cloud_cost_usd{bucket=...}` push 시 동작.
"""

from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchMetricsQuery as CWQuery,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.models.common import BigValueGraphMode

from lib import datasources as ds
from lib import panels as pb

from lib.meta import base_dashboard

UID = "bookflow-ops-row7-cost"
TITLE = "BookFlow 운영 — 멀티클라우드 비용 (Row 7)"
DESCRIPTION = (
    "AWS·Azure·GCP 비용 상세 + $203/월 예산 추적 "
    "(영구 $16 · daily $111 · phase $77 분해). "
    "AWS 는 CloudWatch AWS/Billing · Azure/GCP 는 cost exporter 연결 대기."
)

# 월 예산 (CLAUDE.md 비용 구조)
MONTHLY_BUDGET = 203.0
BUDGET_PERMANENT = 16.0   # 🔒 영구 (S3·ECR·Secrets·KMS·ACM·CloudTrail·Route53)
BUDGET_DAILY = 111.0      # ⏰ 매일 destroy/create (VPC·EKS·ECS·RDS·Redis 등)
BUDGET_PHASE = 77.0       # 📆 Phase 기반 (TGW·VPN·Client VPN·WAF·Glue)

# AWS Billing 메트릭은 us-east-1 글로벌 엔드포인트에만 게시
BILLING_REGION = "us-east-1"


def _cw():
    """CloudWatch datasource ref (패널 + 쿼리 공용)."""
    return ds.ref(ds.CLOUDWATCH)


# ── AWS 비용 — CloudWatch AWS/Billing ───────────────────────────────────
def _aws_billing_query(label: str):
    """AWS/Billing EstimatedCharges (Currency=USD · Maximum) 쿼리.

    EstimatedCharges 는 월 누적 추정 비용 — Maximum 통계로 최신 누적값.
    """
    return (
        CWQuery()
        .datasource(_cw())
        .region(BILLING_REGION)
        .namespace("AWS/Billing")
        .metric_name("EstimatedCharges")
        .dimensions({"Currency": "USD"})
        .statistic("Maximum")
        .period("21600")  # 6h — Billing 메트릭은 ~수 시간 주기 게시
        .label(label)
    )


def _aws_cost_stat():
    """AWS 이번 달 누적 비용 — CloudWatch AWS/Billing EstimatedCharges."""
    panel = pb.stat_panel(
        "AWS 비용 (이번 달 누적)",
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(MONTHLY_BUDGET),
        graph_mode=BigValueGraphMode.AREA,
        decimals=2,
        span=pb.SPAN_QUARTER,
        description=(
            "AWS/Billing EstimatedCharges (us-east-1 · Currency=USD · Maximum). "
            "billing 메트릭 게시 시 동작 — 미게시 시 N/A."
        ),
    )
    return panel.datasource(_cw()).with_target(_aws_billing_query("AWS"))


def _azure_cost_stat():
    """Azure 이번 달 누적 비용 — cost exporter 미연결 placeholder.

    Azure Cost Management → Prometheus push 미연결. exporter 연결 시
    `bookflow_cloud_cost_usd{cloud="azure"}` 로 동작.
    """
    panel = pb.stat_panel(
        "Azure 비용 (이번 달 누적)",
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(MONTHLY_BUDGET),
        graph_mode=BigValueGraphMode.NONE,
        decimals=2,
        span=pb.SPAN_QUARTER,
        description=(
            "Azure Cost Management 비용 (placeholder — cost exporter 미연결, "
            "`bookflow_cloud_cost_usd{cloud=\"azure\"}` push 시 교체)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr('sum(bookflow_cloud_cost_usd{cloud="azure"}) or vector(0)')
        .instant()
        .legend_format("Azure")
    )


def _gcp_cost_stat():
    """GCP 이번 달 누적 비용 — cost exporter 미연결 placeholder.

    GCP Billing → Prometheus push 미연결. exporter 연결 시
    `bookflow_cloud_cost_usd{cloud="gcp"}` 로 동작.
    """
    panel = pb.stat_panel(
        "GCP 비용 (이번 달 누적)",
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(MONTHLY_BUDGET),
        graph_mode=BigValueGraphMode.NONE,
        decimals=2,
        span=pb.SPAN_QUARTER,
        description=(
            "GCP Billing 비용 (placeholder — cost exporter 미연결, "
            "`bookflow_cloud_cost_usd{cloud=\"gcp\"}` push 시 교체)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr('sum(bookflow_cloud_cost_usd{cloud="gcp"}) or vector(0)')
        .instant()
        .legend_format("GCP")
    )


# ── 3사 합계 vs $203 예산 게이지 ────────────────────────────────────────
def _total_budget_gauge():
    """3사 비용 합계 — 이번 달 누적 vs $203 예산 게이지.

    AWS 는 CloudWatch Billing · Azure/GCP 는 cost exporter 연결 시 합산.
    혼합 데이터소스를 한 게이지로 합치기는 어렵다 — exporter 가 3사 비용을
    `bookflow_cloud_cost_usd` 로 통합 push 하면 그 합계를 쓴다.
    현재는 해당 메트릭 미연결 → placeholder vector(0).
    """
    panel = pb.gauge_panel(
        "3사 비용 합계 (이번 달 누적)",
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(MONTHLY_BUDGET),
        minimum=0,
        maximum=MONTHLY_BUDGET,
        decimals=0,
        span=pb.SPAN_QUARTER,
        description=(
            f"AWS+Azure+GCP 월 누적 vs ${MONTHLY_BUDGET:.0f} 예산 "
            "(placeholder — `bookflow_cloud_cost_usd` 통합 push 시 교체)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr("sum(bookflow_cloud_cost_usd) or vector(0)")
        .instant()
        .legend_format("3사 합계")
    )


# ── 예산 3분해 게이지 (영구 / daily / phase) ────────────────────────────
def _budget_bucket_gauge(title: str, bucket: str, budget: float, desc: str):
    """예산 버킷별 게이지 — 영구/daily/phase 각 분해.

    미연결: 비용 버킷 라벨링이 필요 — `bookflow_cloud_cost_usd{bucket=...}`.
    exporter 가 리소스를 영구/daily/phase 로 태깅해 push 하면 동작.
    현재는 placeholder vector(0).
    """
    panel = pb.gauge_panel(
        title,
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(budget),
        minimum=0,
        maximum=budget,
        decimals=0,
        span=pb.SPAN_THIRD,
        description=desc,
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(f'sum(bookflow_cloud_cost_usd{{bucket="{bucket}"}}) or vector(0)')
        .instant()
        .legend_format(bucket)
    )


def _budget_permanent():
    return _budget_bucket_gauge(
        "예산 · 🔒 영구 ($16)",
        "permanent",
        BUDGET_PERMANENT,
        "영구 자원 비용 vs $16 (S3·ECR·Secrets·KMS·ACM·CloudTrail·Route53). "
        "placeholder — `bookflow_cloud_cost_usd{bucket=\"permanent\"}` 미연결.",
    )


def _budget_daily():
    return _budget_bucket_gauge(
        "예산 · ⏰ daily ($111)",
        "daily",
        BUDGET_DAILY,
        "매일 destroy/create 자원 비용 vs $111 (VPC·EKS·ECS·RDS·Redis 등). "
        "placeholder — `bookflow_cloud_cost_usd{bucket=\"daily\"}` 미연결.",
    )


def _budget_phase():
    return _budget_bucket_gauge(
        "예산 · 📆 phase ($77)",
        "phase",
        BUDGET_PHASE,
        "Phase 기반 자원 비용 vs $77 (TGW·VPN·Client VPN·WAF·Glue). "
        "placeholder — `bookflow_cloud_cost_usd{bucket=\"phase\"}` 미연결.",
    )


# ── AWS 비용 추세 ───────────────────────────────────────────────────────
def _aws_cost_trend():
    """AWS 월 누적 비용 추세 — CloudWatch AWS/Billing EstimatedCharges.

    실제 게시되는 유일한 비용 메트릭. EstimatedCharges 는 월 누적이라
    월초 리셋되는 톱니 형태 추세를 보인다.
    """
    panel = pb.timeseries_panel(
        "AWS 비용 추세 (월 누적)",
        unit="currencyUSD",
        span=pb.SPAN_HALF,
        description=(
            "AWS/Billing EstimatedCharges 추세 (us-east-1 · USD · Maximum). "
            "월 누적 — 월초 리셋."
        ),
    )
    return panel.datasource(_cw()).with_target(_aws_billing_query("AWS 누적"))


# ── 3사 비용 추세 (서비스별 상세) ───────────────────────────────────────
def _cloud_cost_trend():
    """3사 비용 추세 — cloud 별 누적 비교.

    미연결: Azure/GCP 비용 push 미연결. AWS 만 CloudWatch 로 실측 가능 ·
    위 _aws_cost_trend 패널이 담당. 이 패널은 통합 메트릭
    `bookflow_cloud_cost_usd` 가 들어오면 3사 cloud 라벨로 분리 추세.
    """
    panel = pb.timeseries_panel(
        "3사 비용 추세 (cloud 별)",
        unit="currencyUSD",
        span=pb.SPAN_HALF,
        description=(
            "AWS·Azure·GCP cloud 별 누적 비용 추세 "
            "(placeholder — `bookflow_cloud_cost_usd` 통합 push 시 동작)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr("sum by (cloud) (bookflow_cloud_cost_usd) or vector(0)")
        .legend_format("{{cloud}}")
    )


# ── 서비스별 비용 표 ────────────────────────────────────────────────────
def _service_cost_table():
    """서비스별 비용 상세 표 — cloud × service 분해.

    미연결: 서비스별 비용 분해는 라벨링된 cost 메트릭 필요.
    AWS/Billing 의 ServiceName dimension 은 라이브에서 빈 값 (per-service
    billing 게시 미설정 · 2026-05-19 실측). exporter 가
    `bookflow_cloud_cost_usd{cloud=, service=}` 로 push 하면 표가 채워진다.
    """
    panel = pb.table_panel(
        "서비스별 비용 상세",
        span=pb.SPAN_FULL,
        description=(
            "cloud × service 비용 분해 표 "
            "(placeholder — `bookflow_cloud_cost_usd{cloud=,service=}` 미연결. "
            "AWS/Billing ServiceName dimension 라이브 빈 값)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr("sum by (cloud, service) (bookflow_cloud_cost_usd) or vector(0)")
        .instant()
        .format("table")
    )


def dashboard() -> Dashboard:
    """Row 7 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 7: 멀티클라우드 비용 ───────────────────────────────────
        .with_row(Row("Row 7 · 멀티클라우드 비용"))
        # 3사 비용 stat (AWS=실측 · Azure/GCP=placeholder)
        .with_panel(_aws_cost_stat())
        .with_panel(_azure_cost_stat())
        .with_panel(_gcp_cost_stat())
        # 3사 합계 vs $203 예산 게이지
        .with_panel(_total_budget_gauge())
        # 예산 3분해 게이지 (영구 / daily / phase)
        .with_panel(_budget_permanent())
        .with_panel(_budget_daily())
        .with_panel(_budget_phase())
        # 비용 추세
        .with_panel(_aws_cost_trend())
        .with_panel(_cloud_cost_trend())
        # 서비스별 비용 표
        .with_panel(_service_cost_table())
    )
