"""Row 8 — 가용성 / SLO.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 8) 기준:
  - 8 Pod 가용성 % — avg_over_time(up[24h])
  - 외부 합성 모니터링 (blackbox) — 응답코드 · 응답시간 · 인증서 D-day · 가용성 %
  - 인프라 가용성 — RDS/Redis available · EKS 노드 Ready %
  - VPN 터널 가용성 % — AWS↔GCP · AWS↔Azure uptime
  - 종합 SLO 게이지

de-dup 원칙(Notion): 시간축 가용성 %는 전부 Row 8 에 통합. Row 1~3/6 은
순간 상태값만. VPN — Row 6 = 토폴로지/BGP, Row 8 = uptime %.

메트릭 출처 (실측 확인 2026-05-19):
  Prometheus
    up{job="kubernetes-pods",namespace="bookflow"}   service Pod 7개
    up{job="kubernetes-nodes"}                       EKS 노드 2개
    blackbox-exporter probe_* (job=blackbox/blackbox-30x)
      probe_success · probe_http_status_code · probe_duration_seconds ·
      probe_ssl_earliest_cert_expiry
      대상: bookflow.myosoon.store · auth/login · 외부 ALB /health
  CloudWatch
    AWS/RDS DatabaseConnections (bookflow-postgres)
    AWS/ElastiCache CurrConnections (bookflow-redis)

publisher-watcher(8번째 Pod)는 CronJob — 상주 up 타깃이 아니라 Pod 가용성
패널은 service Pod 7개 기준이다(하단 _pod_uptime 주석 참조).
VPN 터널 메트릭은 아직 미연결 — Row 0 와 동일하게 placeholder(주석 참조).
"""

from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchMetricsQuery,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.models.cloudwatch import (
    CloudWatchQueryMode,
    MetricEditorMode,
    MetricQueryType,
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

UID = "bookflow-ops-row8-availability"
TITLE = "BookFlow 운영 — 가용성·SLO"
DESCRIPTION = (
    "시간축 가용성 전용 섹션. 7 service Pod 가용성 % · blackbox 외부 합성 "
    "모니터링(응답코드·응답시간·인증서 D-day) · RDS/Redis/EKS노드 인프라 "
    "가용성 · VPN 터널 uptime · 종합 SLO 게이지."
)

# Pod 가용성 셀렉터
_POD_SEL = 'job="kubernetes-pods",namespace="bookflow"'
# blackbox probe 셀렉터 (job=blackbox · blackbox-30x 둘 다 포함)
_PROBE_SEL = 'job=~"blackbox.*"'

# CloudWatch — 도쿄 리전 / RDS·ElastiCache 식별자
_CW_REGION = "ap-northeast-1"

# VPN 터널 uptime 목표 — 실 메트릭 미연결, placeholder
_VPN_TARGET = 99.5

# ── value mappings ──────────────────────────────────────────────────────
_UPDOWN_MAP = ValueMap(
    options={
        "0": ValueMappingResult(text="DOWN", color=pb.RED),
        "1": ValueMappingResult(text="UP", color=pb.GREEN),
    }
)
_HTTP_OK_MAP = ValueMap(
    options={"200": ValueMappingResult(text="200 OK", color=pb.GREEN)}
)
_NODATA_MAP = SpecialValueMap(
    options=DashboardSpecialValueMapOptions(
        match=SpecialValueMatch.NULL,
        result=ValueMappingResult(text="N/A", color=pb.YELLOW),
    )
)


# ── 7 Pod 가용성 % ──────────────────────────────────────────────────────
def _pod_uptime_panel() -> object:
    """service Pod 가용성 % — avg_over_time(up[24h]) · app 별.

    up 타깃은 service Pod 7개(auth-pod·dashboard-svc·decision-svc·forecast-svc·
    intervention-svc·inventory-svc·notification-svc). 24h 내 Pod 재기동이
    있으면 pod 라벨이 여러 개라 by(app) avg 로 중복을 제거한다. 8번째
    publisher-watcher 는 CronJob 이라 상주 up 타깃이 없다.
    """
    panel = pb.timeseries_panel(
        "Pod 가용성 % (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        span=pb.SPAN_HALF,
        description="service Pod 7개 가용성 — 100×avg by(app) avg_over_time(up[24h])",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(
            "100 * avg by (app) "
            f"(avg_over_time(up{{{_POD_SEL}}}[24h]))"
        )
        .legend_format("{{app}}")
    )


def _pod_uptime_stat() -> object:
    """전체 Pod 평균 가용성 % — 7 Pod 24h 평균 단일 숫자."""
    panel = pb.stat_panel(
        "Pod 평균 가용성 (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        span=pb.SPAN_QUARTER,
        decimals=3,
        description="bookflow service Pod 7개 24h 평균 가용성 %",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(f"100 * avg(avg_over_time(up{{{_POD_SEL}}}[24h]))")
        .instant()
        .legend_format("Pod 평균")
    )


# ── 외부 합성 모니터링 (blackbox) ───────────────────────────────────────
def _probe_uptime_panel() -> object:
    """외부 엔드포인트 가용성 % (24h) — blackbox probe_success 평균.

    대상: bookflow.myosoon.store(대시보드 URL) · auth/login(auth-pod HTTPS) ·
    외부 ALB /health(재고조회 API).
    """
    panel = pb.timeseries_panel(
        "외부 엔드포인트 가용성 % (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        span=pb.SPAN_HALF,
        description="blackbox probe_success — 100×avg_over_time(probe_success[24h]) · instance별",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(
            "100 * avg_over_time("
            f"probe_success{{{_PROBE_SEL}}}[24h])"
        )
        .legend_format("{{instance}}")
    )


def _probe_status_panel() -> object:
    """외부 엔드포인트 HTTP 응답코드 — probe_http_status_code 현재값."""
    panel = pb.stat_panel(
        "외부 엔드포인트 HTTP 응답코드",
        unit="short",
        thresholds=pb._thresholds([(None, pb.RED), (200, pb.GREEN), (400, pb.RED)]),
        mappings=[_HTTP_OK_MAP, _NODATA_MAP],
        span=pb.SPAN_HALF,
        description="blackbox probe_http_status_code — 대상 엔드포인트별 최신 HTTP 코드",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(f"probe_http_status_code{{{_PROBE_SEL}}}")
        .instant()
        .legend_format("{{instance}}")
    )


def _probe_duration_panel() -> object:
    """외부 엔드포인트 응답시간 — probe_duration_seconds 추세.

    blackbox 는 단발 probe 라 p50/p95 히스토그램이 없다. probe_duration_seconds
    원값을 추세로 본다(probe 주기 = scrape 주기).
    """
    panel = pb.timeseries_panel(
        "외부 엔드포인트 응답시간",
        unit="s",
        span=pb.SPAN_HALF,
        description="blackbox probe_duration_seconds — 외부 HTTP probe 응답시간 추세",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(f"probe_duration_seconds{{{_PROBE_SEL}}}")
        .legend_format("{{instance}}")
    )


def _cert_expiry_panel() -> object:
    """인증서 만료 D-day — (probe_ssl_earliest_cert_expiry - now) / 86400.

    30일↓ 경고 / 7일↓ 위험. Let's Encrypt 자동 갱신이 정상이면 ~60일 전후
    유지된다. 갱신 실패 조기 감지용.
    """
    panel = pb.stat_panel(
        "TLS 인증서 만료 D-day",
        unit="d",
        thresholds=pb._thresholds([(None, pb.RED), (7, pb.YELLOW), (30, pb.GREEN)]),
        span=pb.SPAN_HALF,
        decimals=0,
        description="probe_ssl_earliest_cert_expiry 기준 남은 일수 — 30일↓ 경고·7일↓ 위험",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr(
            f"(probe_ssl_earliest_cert_expiry{{{_PROBE_SEL}}} - time()) / 86400"
        )
        .instant()
        .legend_format("{{instance}}")
    )


# ── 인프라 가용성 ───────────────────────────────────────────────────────
def _node_ready_panel() -> object:
    """EKS 노드 Ready % (24h) — up{job=kubernetes-nodes} avg_over_time."""
    panel = pb.timeseries_panel(
        "EKS 노드 Ready % (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        span=pb.SPAN_HALF,
        description="EKS 워커 노드 가용성 — 100×avg_over_time(up{job=kubernetes-nodes}[24h])",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
        .expr('100 * avg_over_time(up{job="kubernetes-nodes"}[24h])')
        .legend_format("{{instance}}")
    )


def _rds_available_stat() -> object:
    """RDS 가용성 — CloudWatch DatabaseConnections 수신 여부로 available 판정.

    RDS 전용 'available' 메트릭은 없다. DatabaseConnections 가 정상 수신되면
    인스턴스가 살아 연결을 수락 중이라는 신호. matchExact=false 로 식별자
    무관 검색(SEARCH) — bookflow-postgres 단일 인스턴스.
    """
    panel = pb.stat_panel(
        "RDS 연결 수 (bookflow-postgres)",
        unit="short",
        thresholds=pb._thresholds([(None, pb.RED), (1, pb.GREEN)]),
        span=pb.SPAN_QUARTER,
        decimals=0,
        description="CloudWatch AWS/RDS DatabaseConnections — 연결 수신 = RDS available 신호",
    )
    return panel.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        CloudWatchMetricsQuery()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(_CW_REGION)
        .namespace("AWS/RDS")
        .metric_name("DatabaseConnections")
        .statistic("Average")
        .match_exact(False)
        .label("RDS 연결")
    )


def _redis_available_stat() -> object:
    """Redis 가용성 — CloudWatch CurrConnections 수신 여부로 available 판정.

    ElastiCache 도 전용 available 메트릭이 없어 CurrConnections 정상 수신을
    살아있음 신호로 사용한다. bookflow-redis 단일 노드.
    """
    panel = pb.stat_panel(
        "Redis 연결 수 (bookflow-redis)",
        unit="short",
        thresholds=pb._thresholds([(None, pb.RED), (1, pb.GREEN)]),
        span=pb.SPAN_QUARTER,
        decimals=0,
        description="CloudWatch AWS/ElastiCache CurrConnections — 연결 수신 = Redis available 신호",
    )
    return panel.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        CloudWatchMetricsQuery()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(_CW_REGION)
        .namespace("AWS/ElastiCache")
        .metric_name("CurrConnections")
        .statistic("Average")
        .match_exact(False)
        .label("Redis 연결")
    )


# ── VPN 터널 가용성 % ───────────────────────────────────────────────────
def _vpn_aws_gcp_uptime() -> object:
    """AWS↔GCP HA VPN 터널 uptime % (실 CloudWatch TunnelState avg × 100)."""
    panel = pb.stat_panel(
        "VPN uptime · AWS↔GCP (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        mappings=[_NODATA_MAP],
        span=pb.SPAN_QUARTER,
        decimals=2,
        description="AWS/VPN TunnelState avg × 100 (bookflow-vpn-gcp · 0=DOWN, 1=UP)",
    )
    return panel.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        CloudWatchMetricsQuery()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.CODE)
        .region(_CW_REGION)
        # CODE-mode SEARCH 는 namespace + statistic 필수 — 누락 시 plugin 500 (라이브 검증).
        # VpnId 는 매일 destroy/recreate 로 회전 → apply 시점 치환 placeholder.
        .namespace("AWS/VPN")
        .expression("SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelState\" VpnId=\"__VPN_GCP_ID__\"', 'Average', 300) * 100")
        .statistic("Average")
        .ref_id("A")
        .label("AWS↔GCP")
    )


def _vpn_aws_azure_uptime() -> object:
    """AWS↔Azure S2S VPN 터널 uptime % (실 CloudWatch TunnelState avg × 100)."""
    panel = pb.stat_panel(
        "VPN uptime · AWS↔Azure (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        mappings=[_NODATA_MAP],
        span=pb.SPAN_QUARTER,
        decimals=2,
        description="AWS/VPN TunnelState avg × 100 (bookflow-vpn-azure · 0=DOWN, 1=UP)",
    )
    return panel.datasource(ds.ref(ds.CLOUDWATCH)).with_target(
        CloudWatchMetricsQuery()
        .datasource(ds.ref(ds.CLOUDWATCH))
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.CODE)
        .region(_CW_REGION)
        # CODE-mode SEARCH 는 namespace + statistic 필수 — 누락 시 plugin 500 (라이브 검증).
        # VpnId 는 매일 destroy/recreate 로 회전 → apply 시점 치환 placeholder.
        .namespace("AWS/VPN")
        .expression("SEARCH('{AWS/VPN,VpnId} MetricName=\"TunnelState\" VpnId=\"__VPN_AZURE_ID__\"', 'Average', 300) * 100")
        .statistic("Average")
        .ref_id("A")
        .label("AWS↔Azure")
    )


# ── 종합 SLO 게이지 ─────────────────────────────────────────────────────
def _slo_gauge() -> object:
    """종합 SLO 게이지 — Pod 가용성 + 외부 probe 평균 (24h). 목표선 99.9%.

    Row 0 의 종합 SLO 와 동일 산식 — Row 8 은 게이지 + 추세 상세 섹션.
    """
    expr = (
        "100 * (("
        f"avg(avg_over_time(up{{{_POD_SEL}}}[24h])) "
        f'+ avg(avg_over_time(probe_success{{{_PROBE_SEL}}}[24h]))'
        ") / 2)"
    )
    panel = pb.gauge_panel(
        "종합 가용성 SLO (24h)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        minimum=90,
        maximum=100,
        span=pb.SPAN_HALF,
        decimals=3,
        description="전체 가용성 % — Pod up + 외부 probe 평균(24h) vs 99.9% 목표",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS)).expr(expr).instant().legend_format("종합 SLO")
    )


def _slo_trend_panel() -> object:
    """종합 SLO 추세 — 1h rolling 가용성 % 시계열. 게이지의 시간축 보강."""
    expr = (
        "100 * (("
        f"avg(avg_over_time(up{{{_POD_SEL}}}[1h])) "
        f'+ avg(avg_over_time(probe_success{{{_PROBE_SEL}}}[1h]))'
        ") / 2)"
    )
    panel = pb.timeseries_panel(
        "종합 SLO 추세 (1h rolling)",
        unit="percent",
        thresholds=pb.availability_thresholds(),
        span=pb.SPAN_HALF,
        description="종합 가용성 % 시계열 — 1h rolling. 게이지의 시간축 보강.",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().datasource(ds.ref(ds.PROMETHEUS)).expr(expr).legend_format("종합 SLO")
    )


def dashboard() -> Dashboard:
    """Row 8 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 8: 가용성 / SLO ────────────────────────────────────────
        .with_row(Row("Row 8 · 가용성 / SLO"))
        # 종합 SLO
        .with_panel(_slo_gauge())
        .with_panel(_slo_trend_panel())
        # Pod 가용성
        .with_panel(_pod_uptime_stat())
        .with_panel(_pod_uptime_panel())
        # 외부 합성 모니터링 (blackbox)
        .with_panel(_probe_uptime_panel())
        .with_panel(_probe_status_panel())
        .with_panel(_probe_duration_panel())
        .with_panel(_cert_expiry_panel())
        # 인프라 가용성
        .with_panel(_node_ready_panel())
        .with_panel(_rds_available_stat())
        .with_panel(_redis_available_stat())
        # VPN 터널 가용성
        .with_panel(_vpn_aws_gcp_uptime())
        .with_panel(_vpn_aws_azure_uptime())
    )
