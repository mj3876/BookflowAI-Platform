"""Row 4 — 애플리케이션 (Prometheus 계측).

Notion 설계 (365b4343-5916-81e3-82e1-f49ed2951cbb · §4 Row 4) 기준:
  - 8 Pod별 RPS · p50/p95 지연 · 에러율
  - 비즈니스 흐름: cascade 발의/처리량 · notification 발송 · publisher-watcher 신간 감지
  - WebSocket 연결 수

메트릭 출처 — prometheus-fastapi-instrumentator (실측 확인 2026-05-19):
  - http_requests_total{app,handler,method,status}      RPS / 에러율
  - http_request_duration_seconds_*{app,handler}        per-handler 지연 (버킷 0.1/0.5/1.0)
  - http_request_duration_highr_seconds_bucket{app,le}  app-level 정밀 지연 (p50/p95용)

계측 대상 service Pod 7개 (job=kubernetes-pods · namespace=bookflow):
  auth-pod · dashboard-svc · decision-svc · forecast-svc · intervention-svc ·
  inventory-svc · notification-svc.
8번째 Pod publisher-watcher 는 CronJob — 상주 HTTP 서버가 아니라 instrumentator
/metrics 가 없다. 신간 감지 패널은 dashboard-svc 의 new-book-requests 핸들러
트래픽으로 대리 관측한다 (하단 _newbook_panel 주석 참조).

비즈니스 흐름 패널은 별도 커스텀 메트릭이 아직 없어, instrumentator 표준
http_requests_total 의 handler 라벨로 도메인 엔드포인트 호출량을 집계한다:
  - cascade 발의/처리      → intervention-svc /intervention/orders/* (approve·dispatch·receive)
  - notification 발송      → notification-svc /notification/send (status 별)
"""

from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery

from lib import datasources as ds
from lib import panels as pb
from lib.meta import base_dashboard

UID = "bookflow-ops-row4-application"
TITLE = "BookFlow 운영 — 애플리케이션 (Row 4)"
DESCRIPTION = (
    "EKS bookflow 7 service Pod 의 prometheus-fastapi-instrumentator 계측. "
    "Pod별 RPS·p50/p95 지연·에러율 · cascade 발의/처리 · notification 발송 · "
    "신간 감지 트래픽. publisher-watcher 는 CronJob 이라 /metrics 미계측."
)

# instrumentator 계측 대상 7 service Pod 셀렉터 (job/namespace 고정)
_POD_SEL = 'job="kubernetes-pods",namespace="bookflow"'


# ── Pod별 RPS ───────────────────────────────────────────────────────────
def _rps_panel() -> object:
    """8(7) Pod별 초당 요청수(RPS). http_requests_total rate, app 별 집계.

    health probe(/health) 트래픽이 RPS 를 지배하지 않도록 핸들러는 포함하되
    app 단위로 합산 — 운영자는 Pod별 총 처리량을 본다.
    """
    panel = pb.timeseries_panel(
        "Pod별 RPS",
        unit="reqps",
        span=pb.SPAN_HALF,
        description="Pod(app)별 초당 요청수 — sum by(app) rate(http_requests_total[5m])",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(f'sum by (app) (rate(http_requests_total{{{_POD_SEL}}}[5m]))')
        .legend_format("{{app}}")
    )


# ── p50 / p95 지연 ──────────────────────────────────────────────────────
def _p50_panel() -> object:
    """Pod별 p50 지연. highr 히스토그램(정밀 버킷)으로 quantile 계산."""
    panel = pb.timeseries_panel(
        "Pod별 p50 지연",
        unit="s",
        span=pb.SPAN_HALF,
        description="histogram_quantile(0.50, http_request_duration_highr_seconds_bucket) — app별",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "histogram_quantile(0.50, sum by (app, le) "
            f"(rate(http_request_duration_highr_seconds_bucket{{{_POD_SEL}}}[5m])))"
        )
        .legend_format("{{app}}")
    )


def _p95_panel() -> object:
    """Pod별 p95 지연. highr 히스토그램으로 quantile 계산."""
    panel = pb.timeseries_panel(
        "Pod별 p95 지연",
        unit="s",
        span=pb.SPAN_HALF,
        description="histogram_quantile(0.95, http_request_duration_highr_seconds_bucket) — app별",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "histogram_quantile(0.95, sum by (app, le) "
            f"(rate(http_request_duration_highr_seconds_bucket{{{_POD_SEL}}}[5m])))"
        )
        .legend_format("{{app}}")
    )


# ── 에러율 ──────────────────────────────────────────────────────────────
def _error_rate_panel() -> object:
    """Pod별 5xx 에러율(%). 5xx 응답 비율. instrumentator status 라벨 활용."""
    panel = pb.timeseries_panel(
        "Pod별 에러율 (5xx %)",
        unit="percent",
        span=pb.SPAN_HALF,
        description="5xx 응답 비율(%) — sum by(app) status=5xx / 전체 ×100",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "100 * (sum by (app) "
            f'(rate(http_requests_total{{{_POD_SEL},status="5xx"}}[5m])) '
            "/ clamp_min(sum by (app) "
            f'(rate(http_requests_total{{{_POD_SEL}}}[5m])), 0.001))'
        )
        .legend_format("{{app}}")
    )


# ── 전체 RPS / 에러 stat ────────────────────────────────────────────────
def _total_rps_stat() -> object:
    """전체 RPS 합계 — 7 Pod 총 초당 요청수 단일 숫자."""
    panel = pb.stat_panel(
        "전체 RPS",
        unit="reqps",
        thresholds=pb.health_thresholds(),
        span=pb.SPAN_QUARTER,
        decimals=2,
        description="bookflow 7 service Pod 초당 요청수 합계",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(f'sum(rate(http_requests_total{{{_POD_SEL}}}[5m]))')
        .instant()
        .legend_format("전체 RPS")
    )


def _total_4xx_stat() -> object:
    """전체 4xx 비율(%) — 클라이언트 오류 비중. 인증 실패·잘못된 요청 조기 신호."""
    panel = pb.stat_panel(
        "전체 4xx 비율",
        unit="percent",
        thresholds=pb._thresholds([(None, pb.GREEN), (5, pb.YELLOW), (20, pb.RED)]),
        span=pb.SPAN_QUARTER,
        decimals=1,
        description="전체 요청 중 4xx 비율(%) — 클라이언트 오류 추세",
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "100 * (sum"
            f'(rate(http_requests_total{{{_POD_SEL},status="4xx"}}[5m])) '
            "/ clamp_min(sum"
            f'(rate(http_requests_total{{{_POD_SEL}}}[5m])), 0.001))'
        )
        .instant()
        .legend_format("4xx 비율")
    )


# ── 비즈니스 흐름: cascade 발의/처리 ────────────────────────────────────
def _cascade_panel() -> object:
    """cascade 발의/처리량 — intervention-svc 발주 라이프사이클 엔드포인트 호출량.

    별도 커스텀 메트릭이 없어 instrumentator handler 라벨로 대리 집계한다.
    승인(approve·batch-approve) = 발의, dispatch/receive = 처리 단계.
    """
    panel = pb.timeseries_panel(
        "cascade 발의/처리량",
        unit="reqps",
        span=pb.SPAN_HALF,
        description=(
            "intervention-svc 발주 cascade 단계별 호출량(rate). "
            "approve=발의 · dispatch/receive=처리. handler 라벨 기반 대리 지표."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "sum by (handler) (rate(http_requests_total{"
            f'{_POD_SEL},app="intervention-svc",'
            'handler=~"/intervention/orders/.*"}[5m]))'
        )
        .legend_format("{{handler}}")
    )


# ── 비즈니스 흐름: notification 발송 ────────────────────────────────────
def _notification_panel() -> object:
    """notification 발송 성공/실패 — notification-svc /notification/send 호출량.

    status 라벨로 2xx(성공) vs 4xx/5xx(실패) 구분. Azure Logic Apps 메일
    실제 발송률은 Row 2 에서 별도 관측 — 여기서는 Pod 측 발송 요청 처리량.
    """
    panel = pb.timeseries_panel(
        "notification 발송 (성공/실패)",
        unit="reqps",
        span=pb.SPAN_HALF,
        description=(
            "notification-svc /notification/send 호출량 — status 별. "
            "2xx=성공 · 4xx/5xx=실패. 메일 실제 발송률은 Row 2(Logic Apps)."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "sum by (status) (rate(http_requests_total{"
            f'{_POD_SEL},app="notification-svc",'
            'handler="/notification/send"}[5m]))'
        )
        .legend_format("{{status}}")
    )


# ── 비즈니스 흐름: 신간 감지 트래픽 ─────────────────────────────────────
def _newbook_panel() -> object:
    """publisher-watcher 신간 감지 — dashboard-svc new-book-requests 트래픽 대리.

    publisher-watcher 는 CronJob 이라 instrumentator /metrics 가 없다. 신간
    편입 흐름의 가시 지표로 dashboard-svc /dashboard/new-book-requests 조회
    트래픽을 대리 사용한다. 전용 watcher 메트릭(예: publisher_newbook_detected_total)
    이 추가되면 그 쿼리로 교체한다.
    """
    panel = pb.timeseries_panel(
        "신간 감지 관련 트래픽",
        unit="reqps",
        span=pb.SPAN_HALF,
        description=(
            "dashboard-svc /dashboard/new-book-requests 조회량(rate) — "
            "publisher-watcher(CronJob·미계측) 신간 편입 흐름 대리 지표."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(
            "sum (rate(http_requests_total{"
            f'{_POD_SEL},app="dashboard-svc",'
            'handler=~"/dashboard/new-book-requests"}[5m]))'
        )
        .legend_format("신간 요청 조회")
    )


# ── WebSocket 연결 수 ───────────────────────────────────────────────────
def _websocket_panel() -> object:
    """WebSocket 연결 수 — 현재 동시 연결 추정.

    instrumentator 기본 메트릭에는 WebSocket 전용 게이지가 없고, 실측
    Prometheus 에도 websocket_* 메트릭이 아직 노출되지 않는다. dashboard-svc
    의 process_open_fds(열린 파일 디스크립터 — 소켓 포함)를 연결 부하의
    대리 추세로 사용한다. 전용 게이지(예: websocket_active_connections)가
    노출되면 그 쿼리로 교체한다.
    """
    panel = pb.timeseries_panel(
        "WebSocket 연결 (대리: open FDs)",
        unit="short",
        span=pb.SPAN_HALF,
        description=(
            "dashboard-svc process_open_fds — WebSocket 전용 게이지 부재로 "
            "열린 소켓 수 대리 추세. websocket_active_connections 노출 시 교체."
        ),
    )
    return panel.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery()
        .expr(f'sum by (app) (process_open_fds{{{_POD_SEL},app="dashboard-svc"}})')
        .legend_format("{{app}} open fds")
    )


def dashboard() -> Dashboard:
    """Row 4 대시보드 빌더를 반환. build.py 가 호출."""
    return (
        base_dashboard(TITLE, UID, DESCRIPTION)
        # ── Row 4: 애플리케이션 ────────────────────────────────────────
        .with_row(Row("Row 4 · 애플리케이션 (Prometheus 계측)"))
        # 전체 요약 stat
        .with_panel(_total_rps_stat())
        .with_panel(_total_4xx_stat())
        # Pod별 RPS / 지연 / 에러율
        .with_panel(_rps_panel())
        .with_panel(_p50_panel())
        .with_panel(_p95_panel())
        .with_panel(_error_rate_panel())
        # 비즈니스 흐름
        .with_panel(_cascade_panel())
        .with_panel(_notification_panel())
        .with_panel(_newbook_panel())
        # WebSocket
        .with_panel(_websocket_panel())
    )
