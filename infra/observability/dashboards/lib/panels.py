"""공유 패널 빌더 헬퍼.

BookFlow 운영 대시보드 9개 Row 가 동일한 색·threshold·단위·grid 스타일을
갖도록 패널 생성을 한 곳에 캡슐화한다. 다른 에이전트는 이 헬퍼만 써서 Row 를
작성하면 일관성이 자동 보장된다.

제공 빌더:
  stat_panel()       — 단일 숫자/신호등 (헬스, 카운트)
  gauge_panel()      — 게이지 (예산 대비 비용 등)
  timeseries_panel() — 시계열 추세
  table_panel()      — 표

threshold 헬퍼:
  health_thresholds()       — 0 위험 / 1 경고 / 2 정상 (신호등)
  availability_thresholds() — SLO % (red<99 / yellow<99.9 / green)
  budget_thresholds(budget) — 예산 대비 (green / yellow 80% / red 100%)
"""

from grafana_foundation_sdk.builders import (
    gauge as _gauge,
    stat as _stat,
    table as _table,
    timeseries as _timeseries,
)
from grafana_foundation_sdk.builders.common import (
    ReduceDataOptions,
    StackingConfig,
    VizLegendOptions,
)
from grafana_foundation_sdk.builders.dashboard import ThresholdsConfig
from grafana_foundation_sdk.models.common import (
    BigValueColorMode,
    BigValueGraphMode,
    BigValueTextMode,
    GraphDrawStyle,
    LegendDisplayMode,
    LegendPlacement,
    LineInterpolation,
    StackingMode,
    VisibilityMode,
    VizOrientation,
)
from grafana_foundation_sdk.models.dashboard import (
    Threshold,
    ThresholdsConfig as _ThresholdsConfigModel,
    ThresholdsMode,
)

# ── 색상 팔레트 (Grafana 표준 semantic 색) ──────────────────────────────
GREEN = "green"
YELLOW = "yellow"
RED = "red"
BLUE = "blue"

# ── 표준 패널 크기 (24-column grid) ─────────────────────────────────────
# 한 줄 = 24. stat 6폭 4개 / gauge 6폭 / timeseries 12폭 가 기본.
SPAN_QUARTER = 6
SPAN_THIRD = 8
SPAN_HALF = 12
SPAN_FULL = 24
HEIGHT_STAT = 6
HEIGHT_GAUGE = 8
HEIGHT_TS = 8
HEIGHT_TABLE = 9


# ── threshold 헬퍼 ──────────────────────────────────────────────────────
def _thresholds(steps: list[tuple[float | None, str]]) -> ThresholdsConfig:
    return ThresholdsConfig().mode(ThresholdsMode.ABSOLUTE).steps(
        [Threshold(value=v, color=c) for v, c in steps]
    )


def health_thresholds() -> ThresholdsConfig:
    """신호등: 0=위험(red) / 1=경고(yellow) / 2=정상(green)."""
    return _thresholds([(None, RED), (1, YELLOW), (2, GREEN)])


def updown_thresholds() -> ThresholdsConfig:
    """UP/DOWN: 0=DOWN(red) / 1=UP(green). VPN 터널 등."""
    return _thresholds([(None, RED), (1, GREEN)])


def availability_thresholds() -> ThresholdsConfig:
    """가용성 SLO %: <99 red / <99.9 yellow / >=99.9 green."""
    return _thresholds([(None, RED), (99, YELLOW), (99.9, GREEN)])


def budget_thresholds(budget: float) -> ThresholdsConfig:
    """예산 대비 비용: <80% green / <100% yellow / >=100% red."""
    return _thresholds(
        [(None, GREEN), (budget * 0.8, YELLOW), (budget, RED)]
    )


# ── 패널 빌더 ───────────────────────────────────────────────────────────
def stat_panel(
    title: str,
    *,
    unit: str = "short",
    thresholds: ThresholdsConfig | None = None,
    color_mode: BigValueColorMode = BigValueColorMode.BACKGROUND,
    graph_mode: BigValueGraphMode = BigValueGraphMode.NONE,
    text_mode: BigValueTextMode = BigValueTextMode.VALUE,
    span: int = SPAN_QUARTER,
    height: int = HEIGHT_STAT,
    mappings: list | None = None,
    decimals: int | None = None,
    description: str = "",
) -> _stat.Panel:
    """단일 숫자 / 신호등 stat 패널. 기본 = 배경색으로 상태 표현."""
    p = (
        _stat.Panel()
        .title(title)
        .description(description)
        .unit(unit)
        .span(span)
        .height(height)
        .color_mode(color_mode)
        .graph_mode(graph_mode)
        .text_mode(text_mode)
        .reduce_options(ReduceDataOptions().calcs(["lastNotNull"]).values(False))
        .thresholds(thresholds or health_thresholds())
    )
    if mappings:
        p = p.mappings(mappings)
    if decimals is not None:
        p = p.decimals(decimals)
    return p


def gauge_panel(
    title: str,
    *,
    unit: str = "short",
    thresholds: ThresholdsConfig | None = None,
    minimum: float = 0,
    maximum: float | None = None,
    span: int = SPAN_QUARTER,
    height: int = HEIGHT_GAUGE,
    decimals: int | None = None,
    description: str = "",
) -> _gauge.Panel:
    """게이지 패널. 예산 대비 비용, SLO 등 '목표선 대비' 값에 사용."""
    p = (
        _gauge.Panel()
        .title(title)
        .description(description)
        .unit(unit)
        .span(span)
        .height(height)
        .orientation(VizOrientation.AUTO)
        .show_threshold_markers(True)
        .show_threshold_labels(False)
        .min(minimum)
        .reduce_options(ReduceDataOptions().calcs(["lastNotNull"]).values(False))
        .thresholds(thresholds or availability_thresholds())
    )
    if maximum is not None:
        p = p.max(maximum)
    if decimals is not None:
        p = p.decimals(decimals)
    return p


def timeseries_panel(
    title: str,
    *,
    unit: str = "short",
    thresholds: ThresholdsConfig | None = None,
    span: int = SPAN_HALF,
    height: int = HEIGHT_TS,
    fill_opacity: int = 10,
    stack: bool = False,
    description: str = "",
) -> _timeseries.Panel:
    """시계열 추세 패널. 추세/누적 메트릭에 사용."""
    legend = (
        VizLegendOptions()
        .show_legend(True)
        .display_mode(LegendDisplayMode.LIST)
        .placement(LegendPlacement.BOTTOM)
        .calcs([])
    )
    p = (
        _timeseries.Panel()
        .title(title)
        .description(description)
        .unit(unit)
        .span(span)
        .height(height)
        .draw_style(GraphDrawStyle.LINE)
        .line_interpolation(LineInterpolation.SMOOTH)
        .line_width(2)
        .fill_opacity(fill_opacity)
        .show_points(VisibilityMode.NEVER)
        .legend(legend)
    )
    if stack:
        p = p.stacking(StackingConfig().mode(StackingMode.NORMAL))
    if thresholds is not None:
        p = p.thresholds(thresholds)
    return p


def table_panel(
    title: str,
    *,
    span: int = SPAN_FULL,
    height: int = HEIGHT_TABLE,
    description: str = "",
) -> _table.Panel:
    """표 패널. 리소스 목록/상태 매트릭스에 사용."""
    return (
        _table.Panel()
        .title(title)
        .description(description)
        .span(span)
        .height(height)
        .filterable(True)
    )
