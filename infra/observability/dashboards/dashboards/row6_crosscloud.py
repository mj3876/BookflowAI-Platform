"""Row 6 — cross-cloud 연결 대시보드.

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 6) 기준:
  - AWS↔GCP VPN 터널·BGP peer · AWS↔Azure VPN 터널·BGP
  - TGW attachment 상태 · 라우트 전파

데이터소스: AWS CloudWatch (VPN/TGW 메트릭은 AWS 측에서만 관측 가능).
  - AWS/VPN          TunnelState (1=UP / 0=DOWN) · TunnelDataIn/Out
  - AWS/TransitGateway  BytesIn/Out · PacketDropCountNoRoute / Blackhole

de-dup 원칙 (Notion §4 말미): Row 6 = 연결 토폴로지·BGP·라우트(순간 상태값) ·
Row 8 = VPN uptime %·다운 이력. → 여기서는 % 패널을 두지 않는다.

라이브 실측 (2026-05-19 CloudWatch datasource):
  - AWS/VPN: vpn-0acce17f17cf493e7 · vpn-0c5c1f736a382cd41 (S2S VPN 2개)
  - AWS/TransitGateway: tgw-attach × 7 (VPC 4 + VPN 2 + 1)
미연결 항목 — BGP peer 상태는 CloudWatch 표준 메트릭에 없어 placeholder.
"""

from grafana_foundation_sdk.builders.cloudwatch import (
    CloudWatchMetricsQuery as CWQuery,
)
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from grafana_foundation_sdk.models.common import BigValueGraphMode
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

UID = "bookflow-ops-row6-crosscloud"
TITLE = "BookFlow 운영 — cross-cloud 연결 (Row 6)"
DESCRIPTION = (
    "멀티클라우드 연결 토폴로지. AWS↔GCP·AWS↔Azure S2S VPN 터널·BGP · "
    "TGW attachment 상태·라우트 전파. 순간 상태값 전용 — 가용성 %·다운 이력은 Row 8."
)

# 리전 — VPN/TGW 는 도쿄 ap-northeast-1
AWS_REGION = "ap-northeast-1"

# 라이브 실측 VPN 연결 ID (2026-05-19 · AWS/VPN dimension VpnId)
#   site-to-site VPN 2개 — cross-cloud 데이터 연결용. (Client VPN 은 Row 8 별개)
VPN_AWS_GCP = "vpn-0acce17f17cf493e7"
VPN_AWS_AZURE = "vpn-0c5c1f736a382cd41"

# ── value mappings ──────────────────────────────────────────────────────
# VPN 터널 UP/DOWN — AWS/VPN TunnelState: 1=UP / 0=DOWN
_UPDOWN_MAP = ValueMap(
    options={
        "0": ValueMappingResult(text="DOWN", color=pb.RED),
        "1": ValueMappingResult(text="UP", color=pb.GREEN),
    }
)
# BGP peer 상태 신호등 — 2=established / 1=경고 / 0=down
_BGP_MAP = ValueMap(
    options={
        "0": ValueMappingResult(text="DOWN", color=pb.RED),
        "1": ValueMappingResult(text="경고", color=pb.YELLOW),
        "2": ValueMappingResult(text="established", color=pb.GREEN),
    }
)
_NODATA_MAP = SpecialValueMap(
    options=DashboardSpecialValueMapOptions(
        match=SpecialValueMatch.NULL,
        result=ValueMappingResult(text="N/A", color=pb.YELLOW),
    )
)


def _cw():
    """CloudWatch datasource ref (패널 + 쿼리 공용)."""
    return ds.ref(ds.CLOUDWATCH)


# ── VPN 터널 상태 (UP/DOWN) ─────────────────────────────────────────────
def _vpn_tunnel_state(title: str, vpn_id: str, legend: str, desc: str):
    """site-to-site VPN 터널 상태 stat.

    AWS/VPN TunnelState 는 터널별(연결당 2 터널) 0/1 값. 연결 단위 헬스로
    보기 위해 Maximum(터널 중 하나라도 UP 이면 1) 통계 사용.
    """
    panel = pb.stat_panel(
        title,
        mappings=[_UPDOWN_MAP, _NODATA_MAP],
        thresholds=pb.updown_thresholds(),
        graph_mode=BigValueGraphMode.NONE,
        span=pb.SPAN_QUARTER,
        description=desc,
    )
    query = (
        CWQuery()
        .datasource(_cw())
        .region(AWS_REGION)
        .namespace("AWS/VPN")
        .metric_name("TunnelState")
        .dimensions({"VpnId": vpn_id})
        .statistic("Maximum")
        .period("300")
        .label(legend)
    )
    return panel.datasource(_cw()).with_target(query)


def _vpn_aws_gcp_tunnel():
    return _vpn_tunnel_state(
        "VPN 터널 · AWS↔GCP",
        VPN_AWS_GCP,
        "AWS↔GCP",
        "AWS↔GCP HA VPN 터널 상태 (AWS/VPN TunnelState · Maximum). "
        "GCP HA VPN 은 연결 완료 (Notion §5).",
    )


def _vpn_aws_azure_tunnel():
    return _vpn_tunnel_state(
        "VPN 터널 · AWS↔Azure",
        VPN_AWS_AZURE,
        "AWS↔Azure",
        "AWS↔Azure S2S VPN 터널 상태 (AWS/VPN TunnelState · Maximum). "
        "Notion §5 Phase 1 — AWS↔Azure VPN 연결 항목.",
    )


# ── BGP peer 상태 ───────────────────────────────────────────────────────
def _bgp_peer(title: str, legend: str, desc: str):
    """cross-cloud BGP peer 세션 상태 신호등.

    미연결: CloudWatch 표준 메트릭에는 BGP 세션 상태 메트릭이 없다
    (TunnelState 는 IPsec 터널 상태이지 BGP 세션 상태가 아님). BGP 세션
    상태를 별도 exporter 로 Prometheus 에 push 하면 메트릭명
    `bookflow_vpn_bgp_state` 로 교체. 현재는 placeholder vector(2).
    """
    panel = pb.stat_panel(
        title,
        mappings=[_BGP_MAP, _NODATA_MAP],
        graph_mode=BigValueGraphMode.NONE,
        span=pb.SPAN_QUARTER,
        description=desc,
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(f'max(bookflow_vpn_bgp_state{{link="{legend}"}}) or vector(2)')
        .instant()
        .legend_format(legend)
    )


def _bgp_aws_gcp():
    return _bgp_peer(
        "BGP peer · AWS↔GCP",
        "aws-gcp",
        "AWS↔GCP BGP 세션 상태 (placeholder — BGP exporter 미연결, "
        "`bookflow_vpn_bgp_state` push 시 교체).",
    )


def _bgp_aws_azure():
    return _bgp_peer(
        "BGP peer · AWS↔Azure",
        "aws-azure",
        "AWS↔Azure BGP 세션 상태 (placeholder — BGP exporter 미연결, "
        "`bookflow_vpn_bgp_state` push 시 교체).",
    )


# ── VPN 터널 트래픽 추세 ────────────────────────────────────────────────
def _vpn_traffic():
    """양 VPN 연결의 터널 In/Out 데이터량 추세 — 데이터 흐름 가시화."""
    panel = pb.timeseries_panel(
        "VPN 터널 트래픽 (In/Out)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description="cross-cloud S2S VPN 터널 데이터 In/Out (AWS/VPN · Sum).",
    )
    panel = panel.datasource(_cw())
    for vpn_id, link in (
        (VPN_AWS_GCP, "AWS↔GCP"),
        (VPN_AWS_AZURE, "AWS↔Azure"),
    ):
        for metric, direction in (
            ("TunnelDataIn", "In"),
            ("TunnelDataOut", "Out"),
        ):
            panel = panel.with_target(
                CWQuery()
                .datasource(_cw())
                .region(AWS_REGION)
                .namespace("AWS/VPN")
                .metric_name(metric)
                .dimensions({"VpnId": vpn_id})
                .statistic("Sum")
                .period("300")
                .label(f"{link} {direction}")
            )
    return panel


# ── TGW attachment 상태 / 라우트 전파 ───────────────────────────────────
def _tgw_attachment_traffic():
    """TGW attachment 별 트래픽 — attachment 활성·연결 상태 대리 신호.

    AWS/TransitGateway 에 attachment '상태'(available/pending) enum 메트릭은
    없다. attachment 별 BytesIn/Out > 0 으로 활성·라우팅 정상 여부를 본다.
    """
    panel = pb.timeseries_panel(
        "TGW attachment 트래픽",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "TGW attachment 별 BytesIn/Out (AWS/TransitGateway · Sum). "
            "attachment 활성·라우팅 정상 여부 대리 신호 — 라이브 7 attachment."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, direction in (("BytesIn", "In"), ("BytesOut", "Out")):
        panel = panel.with_target(
            CWQuery()
            .datasource(_cw())
            .region(AWS_REGION)
            .namespace("AWS/TransitGateway")
            .metric_name(metric)
            .statistic("Sum")
            .period("300")
            .label(f"{{{{TransitGatewayAttachment}}}} {direction}")
        )
    return panel


def _tgw_route_drops():
    """TGW 라우트 전파 헬스 — NoRoute / Blackhole 패킷 드랍.

    라우트 전파가 정상이면 NoRoute 드랍은 0 이어야 한다. > 0 이면 라우트
    누락(전파 실패). Blackhole 은 명시 차단 — 함께 추세로 본다.
    """
    panel = pb.timeseries_panel(
        "TGW 라우트 드랍 (NoRoute / Blackhole)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "TGW PacketDropCountNoRoute(라우트 전파 누락) · "
            "PacketDropCountBlackhole(명시 차단). NoRoute>0 = 라우트 전파 실패."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, label in (
        ("PacketDropCountNoRoute", "NoRoute"),
        ("PacketDropCountBlackhole", "Blackhole"),
    ):
        panel = panel.with_target(
            CWQuery()
            .datasource(_cw())
            .region(AWS_REGION)
            .namespace("AWS/TransitGateway")
            .metric_name(metric)
            .statistic("Sum")
            .period("300")
            .label(label)
        )
    return panel


def _tgw_attachment_count():
    """활성 TGW attachment 수 — 트래픽이 관측된 attachment 수.

    SEARCH 식으로 BytesIn 메트릭이 존재하는 attachment 를 카운트.
    설계상 6개(VPC 4 + VPN 2) · Phase 3+ 에서만 활성.
    """
    panel = pb.stat_panel(
        "TGW attachment 수 (관측)",
        unit="short",
        graph_mode=BigValueGraphMode.AREA,
        thresholds=pb.updown_thresholds(),
        span=pb.SPAN_QUARTER,
        description=(
            "트래픽이 관측된 TGW attachment 수 (SEARCH). "
            "설계 6개 — VPC 4 + VPN 2 · Phase 3+ 활성."
        ),
    )
    search = (
        "SEARCH('{AWS/TransitGateway,TransitGatewayAttachment} "
        "MetricName=\"BytesIn\"', 'Sum', 300)"
    )
    query = (
        CWQuery()
        .datasource(_cw())
        .region(AWS_REGION)
        .namespace("AWS/TransitGateway")
        .expression(search)
        .statistic("Sum")
        .period("300")
        .label("attachments")
    )
    return panel.datasource(_cw()).with_target(query)


def dashboard() -> Dashboard:
    """Row 6 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 6: cross-cloud 연결 ────────────────────────────────────
        .with_row(Row("Row 6 · cross-cloud 연결"))
        # VPN 터널 UP/DOWN (2 연결)
        .with_panel(_vpn_aws_gcp_tunnel())
        .with_panel(_vpn_aws_azure_tunnel())
        # BGP peer 상태 (2 연결)
        .with_panel(_bgp_aws_gcp())
        .with_panel(_bgp_aws_azure())
        # VPN 터널 트래픽 추세
        .with_panel(_vpn_traffic())
        # TGW attachment 트래픽
        .with_panel(_tgw_attachment_traffic())
        # TGW 라우트 드랍
        .with_panel(_tgw_route_drops())
        # TGW attachment 수
        .with_panel(_tgw_attachment_count())
    )
