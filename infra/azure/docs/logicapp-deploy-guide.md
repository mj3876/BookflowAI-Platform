# Logic Apps 배포 가이드

> **타입**: Consumption Logic App (`Microsoft.Logic/workflows`)
> **배포 방식**: `az rest PUT` + `arm-deploy.json` (envsubst 치환)
> **이유**: WS1 SKU 쿼터 = 0 → Standard 배포 불가, Consumption으로 전환

---

## 변경 이력

| 날짜 | 변경 내용 |
|---|---|
| 2026-05-27 | `daily-digest` Logic App 삭제 (Azure 리소스 + ARM 파일 모두 제거) |
| 2026-05-27 | `notification`: `DailyPlanFinalized` · `NegotiationDelay` · `InboundRejected` 케이스 삭제; `NewBookRequest` · `GoodsDisplayCampaign` 유지 |
| 2026-05-27 | `approval-request`: `OrderPending` · `NewBook*` 케이스 전체 삭제; `ForecastCompleted`만 유지 |

---

## 1. 워크플로 목록

| 워크플로 | 트리거 | 담당 이벤트 | 수신자 |
|---|---|---|---|
| `notification` | HTTP (SAS URL) | `SpikeUrgent` · `NewBookRequest` · `GoodsDisplayCampaign` | 이벤트별 상이 |
| `approval-request` | HTTP (SAS URL) | `ForecastCompleted` | HQ + WH + Branch |
| `stock-depart` | HTTP (SAS URL) | `StockDepartPending` | 도착지 담당자 1명 |
| `stock-arrival` | HTTP (SAS URL) | `StockArrivalPending` | 출발지 담당자 1명 |
| `secret-rotation` | Recurrence 02:00 KST | — (자체 스케줄) | HQ |

> `la-bookflowmj-daily-digest`는 2026-05-27 삭제됨. ARM 파일(`infra/azure/workflows/daily-digest/`) 및 Azure 리소스 모두 제거.

### 각 Logic App 역할

**`notification`** — 운영 알림 (이벤트별 수신자 상이)
Switch로 이벤트 분기:
- `SpikeUrgent`: ECS SNS 급등 감지 → **HQ 단독** 긴급발주 요청 (importance=high)
- `NewBookRequest`: 신간 신청 접수·승인·거절 알림 → payload의 `stage` 값으로 제목/본문 분기
- `GoodsDisplayCampaign`: 굿즈 이벤트 캠페인 진열 지시 → **지점 담당자 1명**

**`approval-request`** — 발주 승인 요청 (HQ + WH + Branch 전체)
수요예측 완료(`ForecastCompleted`) 시 전 레벨에 발주계획 승인 요청.

**`stock-depart`** — 내부 재고 운송 시작 알림 (도착지 담당자 1명)
출고 버튼 클릭 시 `target_location_id` 기반으로 도착지 담당자 1명에게만 발송. 이메일 색상 파란색(#1a73e8).

**`stock-arrival`** — 내부 재고 운송 완료 알림 (출발지 담당자 1명)
수령 확인 버튼 클릭 시 `source_location_id` 기반으로 출발지 담당자 1명에게만 발송. 이메일 색상 초록색(#188038).

**`secret-rotation`** — Key Vault 시크릿 만료 점검 (HQ, 스케줄)
매일 02:00 KST 자동 실행. Key Vault에서 만료일 있는 시크릿 목록 조회 후 이름·만료일·남은 일수 표로 HQ에 발송. 시크릿 값은 메일에 포함되지 않음.

---

## 2. 이벤트 → Logic App 전체 흐름

### 이벤트 라우팅 맵

| event_type | Logic App | 수신자 | 성격 |
|---|---|---|---|
| `ForecastCompleted` | `approval-request` | HQ + WH + Branch | 수요예측 완료 → 발주계획 전원 승인 요청 |
| `SpikeUrgent` | `notification` | **HQ 단독** | **외부 신호**: ECS SNS 급등 → 긴급발주 요청 |
| `NewBookRequest` | `notification` | HQ + WH | 신간 신청 접수·승인·거절 단계별 알림 |
| `GoodsDisplayCampaign` | `notification` | **지점 담당자 1명** | 굿즈 이벤트 캠페인 진열 지시 |
| `StockDepartPending` | `stock-depart` | **도착지 담당자 1명** | **내부 이동**: 출발지 출고 → 도착지 "오는 중" 알림 |
| `StockArrivalPending` | `stock-arrival` | **출발지 담당자 1명** | **내부 이동**: 도착지 수령 확인 → 출발지 "도착 완료" 알림 |
| `OrderApproved` 등 | (없음) | — | Redis pub/sub(`order.*`)만 — Logic App 미트리거 |

> `StockDepartPending` / `StockArrivalPending` 수신자는 그룹 전체가 아닌
> `target_location_id` / `source_location_id` 로 개별 담당자 1명만 조회.
> 매핑은 `NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON` (K8s ConfigMap) 에서 읽음.

> `OrderApproved` / `OrderDispatched` / `OrderExecuted` / `OrderRejected` 등
> order state machine 이벤트는 **Redis pub/sub 전용**. 이메일 발송 없음.

---

### 이벤트별 상세 흐름

#### ForecastCompleted — 발주계획 승인 요청
```
수요예측 배치 (1일 1회)
  → forecast-pod → notification-svc /notification/send
      event_type=ForecastCompleted
      payload: { snapshot_date, rows_created, by_stage }
      recipients: HQ + WH + Branch (전 레벨)
  → Logic App approval-request (SAS URL POST)
      Switch: ForecastCompleted case
  → ACS Email → 전원 (발주계획 승인 요청, 대시보드 링크)
```

#### SpikeUrgent — 긴급발주 요청
```
ECS SNS 모니터링 (spike-detect pod) - 외부 시장 신호
  → notification-svc /notification/send
      event_type=SpikeUrgent
      payload: { isbn13, title, location, current_stock, detected_at }
  → Logic App notification (SAS URL POST)
      Switch: SpikeUrgent case
  → ACS Email → 본사(HQ) 단독 (긴급발주 요청, importance=high)
  + Redis publish → spike.detected 채널
```

#### NewBookRequest — 신간 신청 단계별 알림
```
신간 신청 처리 시스템
  → notification-svc /notification/send
      event_type=NewBookRequest
      payload: { isbn13, title, stage, [wh1_qty, wh2_qty], [reason] }
        stage: "PENDING" | "APPROVED" | "REJECTED"
      recipients: HQ + WH
  → Logic App notification (SAS URL POST)
      Switch: NewBookRequest case
      → stage 값에 따라 메일 제목·본문 분기:
          PENDING  → "[신간 신청 접수] 도서명 검토 중"
          APPROVED → "[신간 편입 승인] 도서명 발주 명세 (WH1: N권 / WH2: N권)"
          REJECTED → "[신간 편입 거절] 도서명 사유: ..."
  → ACS Email → HQ + WH
```

#### GoodsDisplayCampaign — 굿즈 이벤트 캠페인 진열 지시
```
굿즈/이벤트 캠페인 관리 시스템
  → notification-svc /notification/send
      event_type=GoodsDisplayCampaign
      payload: { campaign_title, campaign_period, branch_id, email_subject, email_body }
      recipients: 지점 담당자 1명 (branch_id 기반)
  → Logic App notification (SAS URL POST)
      Switch: GoodsDisplayCampaign case
  → ACS Email → 해당 지점 담당자 1명 (진열 지시 내용 포함)
```

#### StockDepartPending / StockArrivalPending — 내부 재고 이동
```
[출발지] 대시보드 출고버튼 클릭 → APPROVED → IN_TRANSIT
  → intervention-svc → notification-svc (event_type=StockDepartPending)
      payload 에 target_location_id 포함
  → recipients.py: NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON 파싱
      → target_location_id 담당자 1명 조회
  → Logic App stock-depart → ACS Email → 도착지 담당자 1명 ("오는 중")
  이메일 제목: [운송시작] N권 『도서명』 — 출발지 출발

[도착지] 대시보드 수령확인 클릭 → IN_TRANSIT → EXECUTED
  → intervention-svc → notification-svc (event_type=StockArrivalPending)
      payload 에 source_location_id 포함
  → recipients.py: NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON 파싱
      → source_location_id 담당자 1명 조회
  → Logic App stock-arrival → ACS Email → 출발지 담당자 1명 ("도착 완료")
  이메일 제목: [운송완료] N권 『도서명』 — 도착지 수령 완료
```

---

## 3. 수신자 이메일 매핑

### 그룹 이메일

| 그룹 | 이메일 | K8s ConfigMap 키 |
|---|---|---|
| 본사/경영진 (HQ) | woohek00@gmail.com | `NOTIFICATION_CONTACT_HQ_EMAILS` |
| 물류센터 (WH) | rladudgjs0427@gmail.com | `NOTIFICATION_CONTACT_WH_EMAILS` |
| 지점 전체 (Branch) | ms8405493@gmail.com | `NOTIFICATION_CONTACT_BRANCH_EMAILS` |

### 지점·물류센터 개별 담당자 (location_id 기반)

`NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON` (K8s ConfigMap) 에서 읽음.

| location_id | 지점명 | 이메일 |
|---|---|---|
| 1~14 | 지점 (강남·광화문·잠실 등) / 온라인 | ms8405493@gmail.com |
| 15 | 수도권 거점창고 (WH) | rladudgjs0427@gmail.com |
| 16 | 영남 거점창고 (WH) | rladudgjs0427@gmail.com |

---

## 4. 배포 방법

### 전체 스택 재배포 (deploy-all.sh)

```bash
cd BookFlowAI-Platform/scripts/azure/1-daily
bash deploy-all.sh
```

STACK 5에서 아래 Logic App을 순서대로 배포하고, 완료 후 SAS URL을 출력한다.

```
la-{PREFIX}-notification
la-{PREFIX}-approval-request
la-{PREFIX}-stock-depart
la-{PREFIX}-stock-arrival
la-{PREFIX}-secret-rotation
```

### 단독 배포 (개별 Logic App)

```bash
# 환경변수 설정
export LOCATION="japanwest"
export LOGICAPP_IDENTITY_ID="/subscriptions/.../id-bookflowmj-logicapp"
export ACS_EMAIL_URI="https://acs-bookflowmj.japan.communication.azure.com/emails:send?api-version=2023-03-31"
export ACS_SENDER="DoNotReply@<domain>.azurecomm.net"
export DASHBOARD_URL="https://bookflow.myosoon.store"

SUB_ID=$(az account show --query id --output tsv)
LA_NAME="la-bookflowmj-approval-request"
TEMPLATE="infra/azure/workflows/approval-request/arm-deploy.json"

# envsubst 치환 후 az rest PUT
# 주의: 변수 목록을 명시해야 ARM 템플릿의 $schema 등이 치환되지 않음
envsubst '${LOCATION} ${LOGICAPP_IDENTITY_ID} ${ACS_EMAIL_URI} ${ACS_SENDER} ${DASHBOARD_URL}' \
  < "$TEMPLATE" > /tmp/la-arm.json

az rest --method PUT \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/rg-bookflow/providers/Microsoft.Logic/workflows/${LA_NAME}?api-version=2016-06-01" \
  --body "@/tmp/la-arm.json"
```

> **envsubst 주의사항**: `envsubst < template` 형태(변수 목록 없음)로 실행하면
> ARM 템플릿 내의 `$schema` 필드까지 치환을 시도하여 빈 문자열로 대체된다.
> 반드시 `envsubst '${VAR1} ${VAR2} ...'` 형태로 대상 변수를 명시할 것.
> 이 문제 발생 시 에러: `"Could not find member '' on object of type 'FlowTemplate'"`

---

## 5. SAS URL 발급

HTTP 트리거 Logic App (`notification`, `approval-request`, `stock-depart`, `stock-arrival`)은 배포 후 SAS URL을 발급해야 한다.

```bash
SUB_ID=$(az account show --query id --output tsv)
RG="rg-bookflow"

for la_name in \
  la-bookflowmj-notification \
  la-bookflowmj-approval-request \
  la-bookflowmj-stock-depart \
  la-bookflowmj-stock-arrival; do
  echo "=== ${la_name} ==="
  az rest --method POST \
    --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/${RG}/providers/Microsoft.Logic/workflows/${la_name}/triggers/manual/listCallbackUrl?api-version=2016-06-01" \
    --query "value" --output tsv
done
```

---

## 6. notification-svc 환경변수 업데이트

> SAS URL은 인증키에 해당하므로 ConfigMap이 아닌 **Secret** (또는 ESO)에 보관해야 한다.
> 현재 `kubectl set env`로 임시 주입 중 → 운영 전 Secret으로 이관 필요.

### SAS URL 주입 (즉시 반영)

```bash
kubectl set env deployment/notification-svc -n bookflow \
  NOTIFICATION_LOGIC_APPS_URL="<notification SAS URL>" \
  NOTIFICATION_LOGIC_APPS_APPROVAL_REQUEST_URL="<approval-request SAS URL>" \
  NOTIFICATION_LOGIC_APPS_STOCK_DEPART_URL="<stock-depart SAS URL>" \
  NOTIFICATION_LOGIC_APPS_STOCK_ARRIVAL_URL="<stock-arrival SAS URL>"
```

### ConfigMap 키 목록 (`notification-svc-env`)

```yaml
NOTIFICATION_LOGIC_APPS_URL:                  "<notification SAS URL>"
NOTIFICATION_LOGIC_APPS_APPROVAL_REQUEST_URL: "<approval-request SAS URL>"
NOTIFICATION_LOGIC_APPS_STOCK_DEPART_URL:     "<stock-depart SAS URL>"
NOTIFICATION_LOGIC_APPS_STOCK_ARRIVAL_URL:    "<stock-arrival SAS URL>"
NOTIFICATION_CONTACT_HQ_EMAILS:              "woohek00@gmail.com"
NOTIFICATION_CONTACT_WH_EMAILS:              "rladudgjs0427@gmail.com"
NOTIFICATION_CONTACT_BRANCH_EMAILS:          "ms8405493@gmail.com"
NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON: '{"1":"ms8405493@gmail.com",...,"14":"ms8405493@gmail.com","15":"rladudgjs0427@gmail.com","16":"rladudgjs0427@gmail.com"}'
```

### 코드 변경 적용 방법

> **중요**: `kubectl set env`로는 SAS URL 같은 환경변수 값만 즉시 반영 가능.
> Python 코드(`.py`) 또는 ConfigMap(`.yaml`) 변경은 **CodeBuild → ECR → K8s 롤링 업데이트** 과정을 거쳐야 적용됨.

| 변경 종류 | 반영 방법 |
|---|---|
| SAS URL 교체 | `kubectl set env` → 즉시 반영 |
| 이메일 주소 변경 (ConfigMap) | git push → CodeBuild 빌드 → 파드 재시작 후 반영 |
| Python 코드 변경 (`.py`) | git push → CodeBuild 빌드 → 파드 재시작 후 반영 |

---

## 7. 동작 확인

### Logic App 목록 및 상태 확인
```bash
az logic workflow list \
  --resource-group rg-bookflow \
  --query "[].{name:name, state:properties.state}" \
  --output table
```

현재 활성 Logic App (5개):
```
la-bookflowmj-approval-request
la-bookflowmj-notification
la-bookflowmj-secret-rotation
la-bookflowmj-stock-arrival
la-bookflowmj-stock-depart
```

### ForecastCompleted 수동 테스트
```bash
SAS_URL="<approval-request SAS URL>"

curl -X POST "$SAS_URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @- << 'EOF'
{
  "event_type": "ForecastCompleted",
  "severity": "INFO",
  "correlation_id": "test-forecast-001",
  "payload": {
    "snapshot_date": "2026-05-27",
    "rows_created": 38,
    "by_stage": {"0": 18, "1": 8, "2": 7, "3": 5}
  },
  "recipients": [
    {"address": "woohek00@gmail.com", "displayName": "본사/경영진"},
    {"address": "rladudgjs0427@gmail.com", "displayName": "물류센터"},
    {"address": "ms8405493@gmail.com", "displayName": "지점"}
  ]
}
EOF
```

### SpikeUrgent 수동 테스트
```bash
SAS_URL="<notification SAS URL>"

curl -X POST "$SAS_URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @- << 'EOF'
{
  "event_type": "SpikeUrgent",
  "severity": "CRITICAL",
  "payload": {
    "isbn13": "9791234567890",
    "title": "테스트 도서",
    "location": "강남 지점",
    "current_stock": 3,
    "detected_at": "2026-05-27 14:30"
  },
  "recipients": [
    {"address": "woohek00@gmail.com", "displayName": "본사/경영진"}
  ]
}
EOF
```

### NewBookRequest 수동 테스트
```bash
SAS_URL="<notification SAS URL>"

curl -X POST "$SAS_URL" \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @- << 'EOF'
{
  "event_type": "NewBookRequest",
  "severity": "INFO",
  "payload": {
    "isbn13": "9791234567890",
    "title": "테스트 신간",
    "stage": "APPROVED",
    "wh1_qty": 20,
    "wh2_qty": 15
  },
  "recipients": [
    {"address": "woohek00@gmail.com", "displayName": "본사/경영진"},
    {"address": "rladudgjs0427@gmail.com", "displayName": "물류센터"}
  ]
}
EOF
```

### StockDepartPending 수동 테스트 (notification-svc 직접 호출)
```bash
# port-forward 필요: kubectl port-forward -n bookflow svc/notification-svc 18092:80
curl -s -X POST "http://localhost:18092/notification/send" \
  -H "Authorization: Bearer mock-token-hq-admin" \
  -H "Content-Type: application/json" \
  --data '{
    "event_type": "StockDepartPending",
    "severity": "INFO",
    "payload_summary": {
      "order_id": "test-manual-001",
      "isbn13": "9788925588735",
      "title": "프로젝트 헤일메리",
      "source_location": "영남 거점창고",
      "source_location_id": 16,
      "target_location": "부산 서면점",
      "target_location_id": 7,
      "qty": 5
    }
  }'
# 이메일: 부산서면점 담당자(ms8405493@gmail.com)에게 발송
```

### 실행 기록 확인
```bash
# 최근 5회 실행 기록
SUB_ID=$(az account show --query id --output tsv)
az rest --method GET \
  --url "https://management.azure.com/subscriptions/${SUB_ID}/resourceGroups/rg-bookflow/providers/Microsoft.Logic/workflows/la-bookflowmj-approval-request/runs?api-version=2016-06-01&\$top=5" \
  --query "value[].{status:properties.status, startTime:properties.startTime}" \
  --output table
```

---

## 8. 트러블슈팅

### "Need atleast one valid To, CC or BCC recipient"

Logic App에 전달된 `recipients` 배열이 빈 배열(`[]`)인 경우.

- **StockDepart/Arrival**: `NOTIFICATION_CONTACT_LOCATION_CONTACTS_JSON` 미설정 또는 payload에 `target/source_location_id` 필드 누락.
- **그룹 이벤트**: `NOTIFICATION_CONTACT_HQ_EMAILS` 등 환경변수 미설정.

```bash
# location contacts 로드 확인
kubectl exec -n bookflow <notification-svc-pod> -- python3 -c "
import sys; sys.path.insert(0, '/app')
from src.recipients import _location_contacts
print(len(_location_contacts()), 'locations loaded')
"
```

### "Could not find member '' on object of type 'FlowTemplate'"

`envsubst`에 변수 목록을 명시하지 않아 ARM 템플릿의 `$schema` 필드가 빈 문자열로 치환된 경우.

```bash
# 잘못된 예 (schema 필드 치환됨)
envsubst < arm-deploy.json > /tmp/out.json

# 올바른 예 (대상 변수 명시)
envsubst '${LOCATION} ${LOGICAPP_IDENTITY_ID} ${ACS_EMAIL_URI} ${ACS_SENDER} ${DASHBOARD_URL}' \
  < arm-deploy.json > /tmp/out.json
```

### AUTH_MODE 설정 확인

```bash
kubectl exec -n bookflow <pod> -- env | grep AUTH_MODE
# 테스트 시: AUTH_MODE=mock 이어야 함
# 운영 시: AUTH_MODE=jwt
```

AUTH_MODE가 jwt로 덮어씌워진 경우 임시 override:
```bash
kubectl set env deployment/notification-svc -n bookflow AUTH_MODE=mock
```
