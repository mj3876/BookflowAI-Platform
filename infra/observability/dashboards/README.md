# BookFlow 운영 대시보드 — dashboards-as-code

BookFlow 멀티클라우드 운영 대시보드를 **코드로** 정의한다. Grafana 공식
최신 방식인 **Grafana Foundation SDK (Python)** 를 사용 — Python 코드로
대시보드를 정의하고 JSON 을 생성한다.

설계 명세: Notion `📊 엔지니어 운영 대시보드 — 설계` (`365b4343-5916-81e3-82e1-f49ed2951cbb`)
— Row 0 ~ Row 8.

## 프로젝트 구조

```
infra/observability/dashboards/
  requirements.txt        grafana-foundation-sdk 핀 (11.6.0 = Grafana 11.x 호환)
  lib/
    datasources.py        고정 데이터소스 UID 상수 + ref() 헬퍼
    panels.py             공유 패널 빌더 (stat/gauge/timeseries/table) + threshold 헬퍼
    meta.py               공통 대시보드 메타 (tags/timezone/refresh/time)
  dashboards/
    row0_overview.py      Row 0 — 전체 개요 (rollup) · 레퍼런스 대시보드
  build.py                dashboards/ → dist/*.json 빌드
  dist/                   빌드 산출물 (provisioning 이 사용 · git 미추적)
```

## 빌드 방법

```bash
# 의존성 설치 (Python 3.11+)
py -m pip install -r requirements.txt

# 전체 빌드 → dist/*.json
py build.py

# 단일 대시보드 JSON 을 stdout 으로
py build.py --stdout row0_overview

# 라이브 import 테스트용 — 데이터소스 UID 치환본(dist/*.live.json) 추가 생성
py build.py --remap '{"prometheus":"PBFA97CFB590B2093"}'
```

`dist/` 는 빌드 산출물이라 `.gitignore` 처리한다 — provisioning(IaC)이 이
디렉토리를 빌드해 dashboard configmap 으로 사용하므로 커밋이 불필요하다.

## 데이터소스 UID 규약

코드에는 **고정 UID** (`prometheus` / `cloudwatch` / `azure-monitor` /
`gcp-monitoring`) 를 박는다. provisioning 의 datasource 정의에서 이 고정
UID 를 명시 부여하는 것이 목표다.

라이브 Grafana 가 아직 자동 생성 UID 를 쓰는 동안에는 `build.py --remap`
으로 import 시점에만 치환한다 (`dist/*.live.json`). 커밋되는 `dist/*.json`
과 코드는 항상 고정 UID 를 유지한다.

라이브 현재 UID (2026-05-19 `/api/datasources` 실측):

| 고정 UID         | 라이브 UID            | type                              |
|------------------|-----------------------|-----------------------------------|
| `prometheus`     | `PBFA97CFB590B2093`   | prometheus                        |
| `cloudwatch`     | `P034F075C744B399F`   | cloudwatch                        |
| `azure-monitor`  | `P1EB995EACC6832D3`   | grafana-azure-monitor-datasource  |
| `gcp-monitoring` | `P7CB847570CCC442B`   | stackdriver                       |

## 새 Row 추가 패턴 (다른 에이전트용)

Row 1 ~ Row 8 을 추가할 때는 `dashboards/row0_overview.py` 를 패턴으로 삼아
**공유 헬퍼만** 사용한다. 그러면 9개 Row 의 색·threshold·단위·grid 가 자동
일관된다.

1. **새 모듈 생성** — `dashboards/rowN_xxx.py`
2. **공통 메타로 시작** — `lib.meta.base_dashboard(title, uid, description)`
   → tags `["bookflow","ops"]` · timezone · refresh · time range 자동 적용
3. **패널은 공유 빌더로** — `lib.panels` 의 헬퍼만 사용:
   - `stat_panel()`   — 단일 숫자 / 신호등 (헬스, 카운트)
   - `gauge_panel()`  — 게이지 (예산·SLO 등 목표선 대비)
   - `timeseries_panel()` — 시계열 추세
   - `table_panel()`  — 표
   - threshold 헬퍼: `health_thresholds()` (신호등 0/1/2) ·
     `updown_thresholds()` (0/1) · `availability_thresholds()` (SLO %) ·
     `budget_thresholds(budget)` (예산 대비)
   - grid 폭 상수: `SPAN_QUARTER`(6) · `SPAN_THIRD`(8) · `SPAN_HALF`(12) ·
     `SPAN_FULL`(24)
4. **데이터소스는 ref() 로** — `lib.datasources.ref(ds.PROMETHEUS)` 등.
   패널 `.datasource(...)` 와 쿼리 `.datasource(...)` 양쪽에 지정.
5. **`dashboard()` 함수 하나를 export** — 인자 없이 `Dashboard` 빌더 반환.
   `build.py` 가 이 이름을 호출한다.
6. **`build.py` 의 `DASHBOARD_MODULES` 리스트에 모듈명 등록.**

최소 예시:

```python
from grafana_foundation_sdk.builders.dashboard import Dashboard, Row
from grafana_foundation_sdk.builders.prometheus import Dataquery as PromQuery
from lib import datasources as ds, panels as pb
from lib.meta import base_dashboard

def dashboard() -> Dashboard:
    p = pb.stat_panel("EKS 노드 Ready", unit="short")
    p = p.datasource(ds.ref(ds.PROMETHEUS)).with_target(
        PromQuery().expr('count(up{job="kubernetes-nodes"} == 1)').instant()
    )
    return (
        base_dashboard("BookFlow 운영 — AWS (Row 1)", "bookflow-ops-row1-aws")
        .with_row(Row("Row 1 · AWS"))
        .with_panel(p)
    )
```

## 검증 (Row 0 기준 · 2026-05-19)

- `py build.py` → `dist/row0_overview.json` 생성 (8 패널: 1 row + 7 panel).
- 라이브 Grafana `https://bookflow.myosoon.store/grafana` 에
  `POST /api/dashboards/db` import 성공 (schemaVersion 41 · Grafana 11.x).
- Row 0 패널 쿼리 실데이터 반환 확인: AWS 헬스=2(정상) · Azure 헬스=2(정상) ·
  종합 SLO=100% · 비용=0 (cost exporter 미연결 → `vector(0)` fallback).
