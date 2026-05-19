"""공통 대시보드 메타 헬퍼.

모든 BookFlow 운영 대시보드가 동일한 tags / timezone / refresh / time range 를
갖도록 한 곳에서 캡슐화한다. 새 Row 모듈은 base_dashboard() 로 시작하면 메타가
자동 일관된다.
"""

from grafana_foundation_sdk.builders.dashboard import Dashboard

# BookFlow 운영 대시보드 공통 태그 — provisioning/검색 필터 기준
TAGS = ["bookflow", "ops"]

# 운영 관측 → 도쿄 리전 운영. 브라우저 TZ 사용(엔지니어 로컬).
TIMEZONE = "browser"

# 운영 대시보드 기본 새로고침/시간창
REFRESH = "30s"
TIME_FROM = "now-6h"
TIME_TO = "now"


def base_dashboard(title: str, uid: str, description: str = "") -> Dashboard:
    """공통 메타가 적용된 Dashboard 빌더를 반환.

    호출측은 .with_row(...) / .with_panel(...) 만 체이닝하면 된다.

        from lib.meta import base_dashboard
        dash = base_dashboard("BookFlow 운영 — 전체 개요", "bookflow-ops-overview")
        dash.with_row(...).with_panel(...)
    """
    return (
        Dashboard(title)
        .uid(uid)
        .description(description)
        .tags(TAGS)
        .timezone(TIMEZONE)
        .refresh(REFRESH)
        .time(TIME_FROM, TIME_TO)
        .editable()
    )
