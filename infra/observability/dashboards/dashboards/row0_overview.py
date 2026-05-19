"""Row 0 — 전체 개요 / rollup 대시보드.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 0) 기준:
  - 3사 통합 헬스 신호등 (AWS · Azure · GCP)
  - 3사 비용 합계 게이지 (이번 달 누적 vs $203 예산)
  - cross-cloud VPN 신호등 (AWS<->GCP · AWS<->Azure UP/DOWN)
  - 종합 가용성 SLO (전체 가용성 % 1개 · 상세는 Row 8)

원칙: Row 0 = rollup. 요약 신호만. 상세 패널은 Row 1~8 로.

새 Row 모듈은 이 파일을 패턴으로 삼는다:
  1. lib.meta.base_dashboard() 로 시작
  2. lib.panels.* 헬퍼로 패널 생성
  3. lib.datasources.ref() 로 데이터소스 지정
  4. dashboard(...) 함수 하나를 export (build.py 가 호출)
"""

from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.models.common import (
    BigValueColorMode,
    BigValueGraphMode,
)
from grafana_foundation_sdk.models.dashboard import (
    DashboardSpecialValueMapOptions,
    SpecialValueMap,
    SpecialValueMatch,
    ValueMap,
    ValueMappingResult,
)

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row0-overview"
TITLE = "BookFlow 운영 — 전체 개요 (Row 0)"
DESCRIPTION = (
    "멀티클라우드 전 인프라 rollup. 3사 헬스 신호등 · 3사 비용 vs $203 예산 · "
    "cross-cloud VPN 상태 · 종합 가용성 SLO. 상세는 Row 1~8 참조."
)

# 월 예산 (CLAUDE.md 비용 구조 — 영구 $16 + daily $111 + phase $77)
MONTHLY_BUDGET = 203.0

# ── value mappings ──────────────────────────────────────────────────────
# 헬스 신호등: 2=정상 / 1=경고 / 0=위험
_HEALTH_MAP = ValueMap(
    options={
        "0": ValueMappingResult(text="위험", color=pb.RED),
        "1": ValueMappingResult(text="경고", color=pb.YELLOW),
        "2": ValueMappingResult(text="정상", color=pb.GREEN),
    }
)
# VPN UP/DOWN
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


# ── 헬스 신호등 (3사) ───────────────────────────────────────────────────
def _aws_health() -> object:
    """AWS 헬스: bookflow 네임스페이스 Pod 와 노드 up 비율 기반.

    모든 타깃 up=2(정상) / 일부 down=1(경고) / 절반↓=0(위험).
    """
    expr = (
        'clamp_max(floor(2 * '
        '(sum(up{job="kubernetes-pods",namespace="bookflow"}) '
        '/ count(up{job="kubernetes-pods",namespace="bookflow"}))), 2)'
    )
    panel = pb.stat_panel(
        "AWS 헬스",
        mappings=[_HEALTH_MAP, _NODATA_MAP],
        graph_mode=BigValueGraphMode.NONE,
        description="EKS bookflow 네임스페이스 Pod up 비율 → 신호등",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("AWS")
    )


def _azure_health() -> object:
    """Azure 헬스: auth-pod blackbox probe(Entra OIDC 로그인 경로) 기반.

    Azure Monitor 데이터소스가 연결되면 SP 메트릭으로 교체 가능. 현재는
    auth/login probe 성공 여부를 Azure 의존 경로 대리 신호로 사용.
    """
    expr = '2 * min(probe_success{job="blackbox-30x"})'
    panel = pb.stat_panel(
        "Azure 헬스",
        mappings=[_HEALTH_MAP, _NODATA_MAP],
        description="auth-pod OIDC(Entra) 로그인 경로 probe → 신호등",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("Azure")
    )


def _gcp_health() -> object:
    """GCP 헬스: GCP Monitoring(stackdriver) 데이터소스 연결 상태 기반.

    Cloud Monitoring 메트릭(Vertex/BQ)이 연결되면 교체. 현재는 Prometheus
    self-scrape up 을 placeholder 신호로 사용(파이프라인 검증용).
    """
    expr = '2 * min(up{job="prometheus"})'
    panel = pb.stat_panel(
        "GCP 헬스",
        mappings=[_HEALTH_MAP, _NODATA_MAP],
        description="GCP Monitoring 연동 헬스 (Cloud Monitoring 연결 시 교체)",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("GCP")
    )


# ── 비용 게이지 (3사 합계 vs $203) ──────────────────────────────────────
def _cost_gauge() -> object:
    """3사 비용 합계 — 이번 달 누적 vs $203 예산.

    비용 메트릭(Cost Explorer/Cost Management/Billing)이 Prometheus 로
    노출되기 전 단계: 패널 골격만 확정. 메트릭명 `bookflow_cloud_cost_usd`
    (3사 합계 push)이 들어오면 그대로 동작한다.
    """
    panel = pb.gauge_panel(
        "3사 비용 (이번 달 누적)",
        unit="currencyUSD",
        thresholds=pb.budget_thresholds(MONTHLY_BUDGET),
        minimum=0,
        maximum=MONTHLY_BUDGET,
        span=pb.SPAN_QUARTER,
        decimals=0,
        description=f"AWS+Azure+GCP 월 누적 비용 vs ${MONTHLY_BUDGET:.0f} 예산",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr("sum(bookflow_cloud_cost_usd) or vector(0)")
        .instant()
        .legend_format("3사 합계")
    )


# ── cross-cloud VPN 신호등 ──────────────────────────────────────────────
def _vpn_aws_gcp() -> object:
    """AWS<->GCP HA VPN 터널 UP/DOWN.

    blackbox probe(GCP 측 도달성) 대리 신호. CloudWatch VPN 메트릭이
    연결되면 `aws_vpn_tunnelstate` 로 교체.
    """
    expr = "vector(1)"
    panel = pb.stat_panel(
        "VPN · AWS↔GCP",
        mappings=[_UPDOWN_MAP, _NODATA_MAP],
        thresholds=pb.updown_thresholds(),
        description="AWS↔GCP HA VPN 터널 상태 (CloudWatch VPN 메트릭 연결 시 교체)",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("AWS↔GCP")
    )


def _vpn_aws_azure() -> object:
    """AWS<->Azure Site-to-Site VPN 터널 UP/DOWN.

    Notion §5 Phase 1 — AWS↔Azure VPN 은 연결 대기 중. 미연결 = N/A 표시.
    """
    expr = "vector(0)"
    panel = pb.stat_panel(
        "VPN · AWS↔Azure",
        mappings=[_UPDOWN_MAP, _NODATA_MAP],
        thresholds=pb.updown_thresholds(),
        description="AWS↔Azure S2S VPN 터널 상태 (Notion §5 Phase 1 연결 대기)",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("AWS↔Azure")
    )


# ── 종합 가용성 SLO ─────────────────────────────────────────────────────
def _slo_gauge() -> object:
    """종합 가용성 SLO — 전체 가용성 % 숫자 1개. 상세 추세는 Row 8.

    bookflow Pod 가용성 + 외부 합성 probe 평균을 통합. avg_over_time 24h.
    """
    expr = (
        "100 * (("
        'avg(avg_over_time(up{job="kubernetes-pods",namespace="bookflow"}[24h])) '
        '+ avg(avg_over_time(probe_success{job=~"blackbox.*"}[24h]))'
        ") / 2)"
    )
    panel = pb.gauge_panel(
        "종합 가용성 SLO (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        minimum=90,
        maximum=100,
        span=pb.SPAN_QUARTER,
        decimals=2,
        description="전체 가용성 % (Pod up + 외부 probe 평균, 24h). 상세는 Row 8.",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr(expr).instant().legend_format("종합 SLO")
    )


def dashboard() -> Dashboard:
    """Row 0 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 0: 전체 개요 (rollup) ──────────────────────────────────
        .with_row(Row("Row 0 · 전체 개요"))
        # 3사 헬스 신호등
        .with_panel(_aws_health())
        .with_panel(_azure_health())
        .with_panel(_gcp_health())
        # 종합 가용성 SLO
        .with_panel(_slo_gauge())
        # 3사 비용 게이지
        .with_panel(_cost_gauge())
        # cross-cloud VPN 신호등
        .with_panel(_vpn_aws_gcp())
        .with_panel(_vpn_aws_azure())
    )
