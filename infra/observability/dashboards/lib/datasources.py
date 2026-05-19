"""데이터소스 UID 상수.

코드에는 **고정 UID** 를 박는다. 라이브 Grafana 의 datasource UID(자동 생성값)와
다를 수 있으나, provisioning(datasource configmap/IaC)에서 아래 고정 UID 를 명시
부여하는 것이 목표다. 라이브와 임시로 다를 경우 build.py 가 --remap 옵션으로
import 시점에 치환할 수 있다 (커밋 코드는 항상 고정 UID 유지).

라이브 현재 UID (2026-05-19 /api/datasources 실측 · 참고용):
  prometheus      -> PBFA97CFB590B2093
  cloudwatch      -> P034F075C744B399F
  azure-monitor   -> P1EB995EACC6832D3
  gcp-monitoring  -> P7CB847570CCC442B
"""

from grafana_foundation_sdk.models.dashboard import DataSourceRef

# ── 고정 UID (provisioning 에서 부여 예정) ──────────────────────────────
PROMETHEUS = "prometheus"
CLOUDWATCH = "cloudwatch"
AZURE_MONITOR = "azure-monitor"
GCP_MONITORING = "gcp-monitoring"

# datasource type 식별자 (Grafana plugin id)
_TYPES = {
    PROMETHEUS: "prometheus",
    CLOUDWATCH: "cloudwatch",
    AZURE_MONITOR: "grafana-azure-monitor-datasource",
    GCP_MONITORING: "stackdriver",
}


def ref(uid: str) -> DataSourceRef:
    """UID 상수 -> DataSourceRef. 패널 .datasource() / 쿼리 .datasource() 에 사용."""
    return DataSourceRef(type_val=_TYPES.get(uid), uid=uid)
