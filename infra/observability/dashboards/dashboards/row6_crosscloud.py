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

from lib import datasources as ds
from lib import panels as pb

from lib.meta import base_dashboard

UID = "bookflow-ops-row6-crosscloud"
TITLE = "BookFlow 운영 — Cross-cloud 연결"
DESCRIPTION = (
    "멀티클라우드 연결 토폴로지. AWS↔GCP·AWS↔Azure S2S VPN 터널·BGP · "
    "TGW attachment 상태·라우트 전파. 순간 상태값 전용 — 가용성 %·다운 이력은 Row 8."
)

# 리전 — VPN/TGW 는 도쿄 ap-northeast-1
AWS_REGION = "ap-northeast-1"

# site-to-site VPN 2개 — cross-cloud 데이터 연결용. (Client VPN 은 Row 8 별개)
# VpnId 는 매일 destroy/recreate 로 회전하므로 하드코딩 불가 — apply 시점에
# _apply_grafana_dashboards() 가 Name 태그(bookflow-vpn-gcp/azure)로 현재 VpnId 를
# 조회해 아래 placeholder 를 치환한다 (__AWS_ACCOUNT__ 와 동일 패턴).
VPN_AWS_GCP = "__VPN_GCP_ID__"
VPN_AWS_AZURE = "__VPN_AZURE_ID__"

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
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
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
        PromQuery().datasource(ds.ref(ds.PROMETHEUS))
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
                .query_mode(CloudWatchQueryMode.METRICS)
                .metric_query_type(MetricQueryType.SEARCH)
                .metric_editor_mode(MetricEditorMode.BUILDER)
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
# TGW / attachment ID 는 매일 destroy/recreate 로 회전 + 계정별로 다르므로
# 하드코딩 불가 — apply 시점에 _apply_grafana_dashboards() 가 Name 태그
# (bookflow-tgw-attach-<vpc>)로 현재 ID 를 조회해 아래 placeholder 를 치환한다
# (__AWS_ACCOUNT__ · __VPN_*_ID__ 와 동일 패턴). 미치환 시 패널 no-data(정상).
TGW_ID = "__TGW_ID__"
ATTACHMENT_TO_VPC = [
    ("__TGW_ATTACH_BOOKFLOW_AI__", "bookflow-ai"),  # EKS Pod
    ("__TGW_ATTACH_ANSIBLE__", "ansible"),
    ("__TGW_ATTACH_SALES_DATA__", "sales-data"),
    ("__TGW_ATTACH_DATA__", "data"),                # RDS / Redis
    ("__TGW_ATTACH_EGRESS__", "egress"),            # DMZ / NAT
    ("__TGW_ATTACH_VPN_1__", "vpn-1"),              # cross-cloud VPN
    ("__TGW_ATTACH_VPN_2__", "vpn-2"),
]


# 기존 패널은 dimension 없는 plain metric builder 라 attachment 별 시리즈가
# 안 잡혔다. SEARCH 식은 metricEditorMode=CODE 가 필수 — Builder 모드에서
# expression 만 박으면 CloudWatch 가 metricName 필수 에러 (라이브 검증 2026-05-20).
def _tgw_search(metric: str, stat: str = "Sum") -> str:
    """attachment 별 시리즈 자동 매칭 SEARCH 식.

    `{namespace,Dim1,Dim2,...}` 패턴은 해당 dimension 조합이 정확히 존재하는
    메트릭만 매칭 — TGW 의 경우 attachment 별 시리즈 ((TransitGateway,
    TransitGatewayAttachment)) 만 추출돼 cross-AZ aggregate 중복 제거.
    """
    return (
        f"SEARCH('{{AWS/TransitGateway,TransitGateway,TransitGatewayAttachment}} "
        f"MetricName=\"{metric}\"', '{stat}', 300)"
    )


def _tgw_search_query(metric: str, label: str, stat: str = "Sum") -> CWQuery:
    """SEARCH 식 공용 CWQuery (CODE mode · metricName 비움).

    Builder mode 로는 SEARCH 식이 안 먹힌다 (라이브 검증 — metricName empty 에러).
    CODE mode 로 박으면 attachment 별 7 series 자동 추출.
    """
    return (
        CWQuery()
        .datasource(_cw())
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.CODE)
        .region(AWS_REGION)
        .namespace("AWS/TransitGateway")
        .expression(_tgw_search(metric, stat))
        .statistic(stat)
        .period("300")
        .label(label)
    )


def _tgw_per_vpc_query(metric: str, attachment_id: str, vpc_name: str, stat: str = "Sum") -> CWQuery:
    """attachment-ID 별 Builder 쿼리 — VPC 이름을 legend 로 사용."""
    return (
        CWQuery()
        .datasource(_cw())
        .query_mode(CloudWatchQueryMode.METRICS)
        .metric_query_type(MetricQueryType.SEARCH)
        .metric_editor_mode(MetricEditorMode.BUILDER)
        .region(AWS_REGION)
        .namespace("AWS/TransitGateway")
        .metric_name(metric)
        .dimensions({
            "TransitGateway": TGW_ID,
            "TransitGatewayAttachment": attachment_id,
        })
        .match_exact(True)
        .statistic(stat)
        .period("300")
        .label(vpc_name)
    )


def _tgw_attachment_traffic():
    """TGW attachment 별 트래픽 — attachment 활성·연결 상태 대리 신호.

    AWS/TransitGateway 에 attachment '상태'(available/pending) enum 메트릭은
    없다. attachment 별 BytesIn/Out > 0 으로 활성·라우팅 정상 여부를 본다.
    """
    panel = pb.timeseries_panel(
        "TGW attachment 트래픽 (Bytes In/Out · SEARCH)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "TGW attachment 별 BytesIn/Out (AWS/TransitGateway · Sum · SEARCH). "
            "attachment 활성·라우팅 정상 여부 대리 신호 — 라이브 7 attachment "
            "(VPC 5 + VPN 2). attachment-ID → VPC: bookflow-ai/ansible/sales-data/"
            "data/egress + vpn-1/vpn-2."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, direction in (("BytesIn", "In"), ("BytesOut", "Out")):
        panel = panel.with_target(
            _tgw_search_query(metric, f"{{{{TransitGatewayAttachment}}}} {direction}")
        )
    return panel


def _tgw_attachment_packets():
    """TGW attachment 별 패킷 In/Out — 트래픽 율 가시화 (낮은 byte 변동 보강)."""
    panel = pb.timeseries_panel(
        "TGW attachment 패킷 (In/Out · SEARCH)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "TGW attachment 별 PacketsIn/Out (AWS/TransitGateway · Sum · SEARCH). "
            "bytes 가 낮을 때도 packet rate 로 흐름 확인."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, direction in (("PacketsIn", "In"), ("PacketsOut", "Out")):
        panel = panel.with_target(
            _tgw_search_query(metric, f"{{{{TransitGatewayAttachment}}}} {direction}")
        )
    return panel


def _tgw_route_drops():
    """TGW 라우트 전파 헬스 — NoRoute / Blackhole 패킷 드랍.

    라우트 전파가 정상이면 NoRoute 드랍은 0 이어야 한다. > 0 이면 라우트
    누락(전파 실패). Blackhole 은 명시 차단 — 함께 추세로 본다.
    """
    panel = pb.timeseries_panel(
        "TGW 라우트 드랍 (NoRoute / Blackhole · attachment 별)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "TGW PacketDropCountNoRoute(라우트 전파 누락) · "
            "PacketDropCountBlackhole(명시 차단) · attachment 별 SEARCH. "
            "NoRoute>0 = 라우트 전파 실패."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, label in (
        ("PacketDropCountNoRoute", "NoRoute"),
        ("PacketDropCountBlackhole", "Blackhole"),
    ):
        panel = panel.with_target(
            _tgw_search_query(metric, f"{{{{TransitGatewayAttachment}}}} {label}")
        )
    return panel


def _tgw_byte_drops():
    """TGW 바이트 드랍 — BytesDropCount{NoRoute,Blackhole} (byte scale 영향)."""
    panel = pb.timeseries_panel(
        "TGW 바이트 드랍 (NoRoute / Blackhole · attachment 별)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "TGW BytesDropCountNoRoute · BytesDropCountBlackhole · attachment 별 "
            "SEARCH. 드랍 트래픽 규모(bytes) 가시화."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, label in (
        ("BytesDropCountNoRoute", "NoRoute"),
        ("BytesDropCountBlackhole", "Blackhole"),
    ):
        panel = panel.with_target(
            _tgw_search_query(metric, f"{{{{TransitGatewayAttachment}}}} {label}")
        )
    return panel


def _tgw_ttl_drops():
    """TGW PacketDropCountTTLExpired — 라우팅 루프 신호.

    TTL 만료 드랍 > 0 이면 라우팅 루프 가능성. attachment 별 시리즈.
    """
    panel = pb.timeseries_panel(
        "TGW TTL 만료 드랍 (라우팅 루프 신호)",
        unit="short",
        span=pb.SPAN_HALF,
        fill_opacity=20,
        description=(
            "AWS/TransitGateway PacketDropCountTTLExpired · attachment 별 "
            "SEARCH. > 0 지속 시 라우팅 루프 의심."
        ),
    )
    panel = panel.datasource(_cw())
    panel = panel.with_target(
        _tgw_search_query("PacketDropCountTTLExpired", "{{TransitGatewayAttachment}} TTL")
    )
    return panel


def _tgw_attachment_count():
    """활성 TGW attachment 수 — 트래픽이 관측된 attachment 수.

    SEARCH 식으로 BytesIn 메트릭이 존재하는 attachment 를 카운트.
    설계상 7개(VPC 5 + VPN 2) · Phase 3+ 에서만 활성.
    """
    panel = pb.stat_panel(
        "TGW attachment 수 (관측)",
        unit="short",
        graph_mode=BigValueGraphMode.AREA,
        thresholds=pb.updown_thresholds(),
        span=pb.SPAN_QUARTER,
        description=(
            "트래픽이 관측된 TGW attachment 수 (SEARCH). "
            "라이브 7개 — VPC 5 (bookflow-ai/ansible/sales-data/data/egress) + "
            "VPN 2 · Phase 3+ 활성."
        ),
    )
    return panel.datasource(_cw()).with_target(
        _tgw_search_query("BytesIn", "attachments")
    )


def _tgw_total_traffic():
    """TGW 전체 BytesIn/Out — 허브 단위 합계 (cross-AZ aggregate)."""
    panel = pb.timeseries_panel(
        "TGW 전체 트래픽 (허브 단위 · cross-AZ)",
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            "AWS/TransitGateway BytesIn/Out · dimension={TransitGateway} 단일. "
            "허브 전체 통과 트래픽 합계."
        ),
    )
    panel = panel.datasource(_cw())
    for metric, direction in (("BytesIn", "In"), ("BytesOut", "Out")):
        panel = panel.with_target(
            CWQuery()
            .datasource(_cw())
            .query_mode(CloudWatchQueryMode.METRICS)
            .metric_query_type(MetricQueryType.SEARCH)
            .metric_editor_mode(MetricEditorMode.BUILDER)
            .region(AWS_REGION)
            .namespace("AWS/TransitGateway")
            .metric_name(metric)
            .dimensions({"TransitGateway": TGW_ID})
            .match_exact(True)
            .statistic("Sum")
            .period("300")
            .label(f"hub {direction}")
        )
    return panel


# ── TGW · VPC 별 트래픽 (attachment-ID alias → VPC name) ────────────────
# 어느 VPC 가 가장 활발한지 한눈에. attachment-ID 별 builder 쿼리로 series
# 이름을 VPC 명(bookflow-ai/data/egress 등)으로 고정 — 그래프 가독성 향상.
def _tgw_vpc_traffic(metric: str, title: str, direction: str):
    """VPC 별 BytesIn 또는 BytesOut timeseries."""
    panel = pb.timeseries_panel(
        title,
        unit="bytes",
        span=pb.SPAN_HALF,
        description=(
            f"TGW attachment 별 {metric} (Sum · 300s) · attachment-ID 를 VPC "
            "이름으로 alias. 어느 VPC 가 cross-VPC 트래픽의 주인공인지 "
            "한눈에 — bookflow-ai (EKS) / data (RDS·Redis) / egress (DMZ) / "
            "sales-data / ansible / vpn-1 / vpn-2."
        ),
        stack=True,
    )
    panel = panel.datasource(_cw())
    for att_id, vpc_name in ATTACHMENT_TO_VPC:
        panel = panel.with_target(
            _tgw_per_vpc_query(metric, att_id, vpc_name)
        )
    return panel


def _tgw_vpc_traffic_in():
    return _tgw_vpc_traffic(
        "BytesIn",
        "TGW · VPC 별 트래픽 IN (Sum · stacked)",
        "In",
    )


def _tgw_vpc_traffic_out():
    return _tgw_vpc_traffic(
        "BytesOut",
        "TGW · VPC 별 트래픽 OUT (Sum · stacked)",
        "Out",
    )


def _tgw_vpc_packets_in():
    """VPC 별 PacketsIn — 패킷 율로 활동도 비교."""
    panel = pb.timeseries_panel(
        "TGW · VPC 별 PacketsIn (Sum · stacked)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "TGW attachment 별 PacketsIn · attachment-ID 를 VPC 이름으로 alias. "
            "bytes 가 낮을 때도 packet 율로 흐름 정상 여부 확인."
        ),
        stack=True,
    )
    panel = panel.datasource(_cw())
    for att_id, vpc_name in ATTACHMENT_TO_VPC:
        panel = panel.with_target(
            _tgw_per_vpc_query("PacketsIn", att_id, vpc_name)
        )
    return panel


def _tgw_vpc_total_table():
    """24h VPC 별 총 트래픽 stat — 가장 활발한 VPC 순위 (BytesIn 기준).

    timeseries 가 stack 으로 분포 보여줘도 절대값 비교는 stat 가 직관적.
    각 VPC 별로 stat 패널 1개씩, SPAN_QUARTER × 7 ≈ 두 줄.
    """
    panels = []
    for att_id, vpc_name in ATTACHMENT_TO_VPC:
        p = pb.stat_panel(
            f"VPC · {vpc_name} (BytesIn)",
            unit="bytes",
            thresholds=pb.updown_thresholds(),
            graph_mode=BigValueGraphMode.AREA,
            span=pb.SPAN_QUARTER,
            description=(
                f"attachment {att_id} (VPC {vpc_name}) 의 BytesIn lastNotNull. "
                "trend area 로 24h 활동 패턴."
            ),
        )
        p = p.datasource(_cw()).with_target(
            _tgw_per_vpc_query("BytesIn", att_id, vpc_name)
        )
        panels.append(p)
    return panels


def dashboard() -> Dashboard:
    """Row 6 대시보드 빌더를 반환. build.py 가 호출."""
    d = (
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
        # TGW attachment 수 (stat) + TGW 전체 트래픽 (timeseries)
        .with_panel(_tgw_attachment_count())
        .with_panel(_tgw_total_traffic())
        # ── VPC 별 트래픽 (attachment-ID → VPC name alias) ─────────────
        .with_panel(_tgw_vpc_traffic_in())
        .with_panel(_tgw_vpc_traffic_out())
        .with_panel(_tgw_vpc_packets_in())
    )
    # VPC 별 stat 7개 (BytesIn lastNotNull · 어느 VPC 가 활발한지 순위)
    for p in _tgw_vpc_total_table():
        d = d.with_panel(p)
    return (
        d
        # TGW attachment 별 트래픽 (Bytes + Packets · SEARCH attachment-ID raw)
        .with_panel(_tgw_attachment_traffic())
        .with_panel(_tgw_attachment_packets())
        # TGW 라우트 드랍 (packets + bytes)
        .with_panel(_tgw_route_drops())
        .with_panel(_tgw_byte_drops())
        # TGW TTL 만료 (라우팅 루프 신호)
        .with_panel(_tgw_ttl_drops())
    )
