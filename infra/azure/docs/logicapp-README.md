# Logic Apps 알람 시스템 — 구축·실행·검증 가이드

> 구현 범위: 패턴 2 (DailyPlanFinalized) · 패턴 3 (SpikeUrgent)  
> 패턴 1, 4–8은 구현하지 않음

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [발송 패턴 명세](#2-발송-패턴-명세)
3. [현재 인프라 상태](#3-현재-인프라-상태)
4. [수동 실행 방법 (Logic Apps 직접 호출)](#4-수동-실행-방법-logic-apps-직접-호출)
5. [notification-svc 경유 실행](#5-notification-svc-경유-실행)
6. [검증 방법](#6-검증-방법)

---

## 1. 아키텍처 개요

```
[EKS notification-svc]
        │
        │  HTTPS POST (Consumption SAS URL)
        │  NOTIFICATION_LOGIC_APPS_URL (configmap)
        ▼
[Azure Logic Apps Consumption]
  la-bookflowmj-notification
        │
        │  Switch event_type
        ▼
[ACS Email (Managed Identity)]
        │
[수신자: 본사 / 물류센터 / 지점]
```

> 현재 Logic Apps Standard(WS1) 쿼터 부재로 Consumption 방식을 사용합니다.  
> VPN 터널 (AWS TGW ↔ Azure VPN GW)은 연결되어 있으나, Consumption Logic Apps는 public endpoint를 사용합니다.

---

## 2. 발송 패턴 명세

| # | event_type | 심각도 | 수신자 | 메일 내용 |
|---|-----------|--------|--------|---------|
| 2 | `DailyPlanFinalized` | INFO | 본사 + 물류센터 + 지점 전체 | 처리해야 할 의사결정이 모두 완료됐습니다 — 운송 시작 가능 상태. |
| 3 | `SpikeUrgent` | CRITICAL | 본사 + 물류센터 | SNS 화제 도서가 감지되었습니다. 24h 내 폭증 매출 가능성. |

### 수신자 그룹

| 그룹 | configmap 키 |
|------|-------------|
| 본사/경영진 | `NOTIFICATION_CONTACT_HQ_EMAILS` |
| 물류센터 | `NOTIFICATION_CONTACT_WH_EMAILS` |
| 지점 전체 | `NOTIFICATION_CONTACT_BRANCH_EMAILS` |

### 호출 흐름

```
upstream 서비스
  → POST /notification/send
    body: { event_type, severity, payload_summary }

notification-svc
  → _needs_logic_apps(event_type) 확인
  → get_recipients(event_type) 로 수신자 목록 결정
  → POST logic_apps_url (Consumption SAS URL)
    body: { event_type, severity, payload, recipients }

Logic Apps Switch_EventType
  → ACS Email 발송
```

> `payload_summary`에 담기는 데이터(재고 수량, 건수 등)는 upstream 서비스(intervention-svc, batch job 등)가 계산하여 전달합니다. Logic Apps와 notification-svc는 RDS를 직접 조회하지 않습니다.

---

## 3. 현재 인프라 상태

### Azure

| 리소스 | 상태 | 비고 |
|--------|------|------|
| VPN Gateway `vpngw-bookflowmj` | Connected | VpnGw1AZ, IP: 135.149.169.236, ASN: 65001 |
| Logic Apps Consumption `la-bookflowmj-notification` | Enabled | HTTP trigger SAS URL 발급 완료 |
| Logic Apps Standard (WS1) | 미배포 | WorkflowStandard 쿼터 0 → 신청 필요 |
| ACS Email | 구성 완료 | Managed Identity 인증 |

### AWS

| 리소스 | 상태 | 비고 |
|--------|------|------|
| TGW | available | ASN: 64512 |
| VPN Connection (Azure행) | available | 터널 2개 UP |
| 라우팅 172.16.0.0/16 → TGW | active | 7개 VPC 반영 완료 |

### ConfigMap 현재 설정

```yaml
NOTIFICATION_LOGIC_APPS_URL: "<SAS URL — Secret/ESO로 이동 예정, README에 기재 금지>"
```

---

## 4. 수동 실행 방법 (Logic Apps 직접 호출)

notification-svc Pod 없이 Logic Apps trigger URL을 직접 호출해서 메일 발송을 테스트할 수 있습니다.

### 패턴 2: DailyPlanFinalized

수신자: 본사 + 물류센터 + 지점

```bash
# SAS URL은 Secret/ESO에서 조회 (README에 기재 금지)
LA_URL="<NOTIFICATION_LOGIC_APPS_URL>"

curl -s -X POST "$LA_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "DailyPlanFinalized",
    "severity": "INFO",
    "payload": {},
    "recipients": [
      {"address": "<HQ_EMAIL>", "displayName": "본사/경영진"},
      {"address": "<WH_EMAIL>", "displayName": "물류센터"},
      {"address": "<BRANCH_EMAIL>", "displayName": "지점"}
    ]
  }'
```

기대 응답: `{"result":"ok"}`  
기대 메일 내용: "처리해야 할 의사결정이 모두 완료됐습니다 — 운송 시작 가능 상태."

### 패턴 3: SpikeUrgent

수신자: 본사 + 물류센터

```bash
# SAS URL은 Secret/ESO에서 조회 (README에 기재 금지)
LA_URL="<NOTIFICATION_LOGIC_APPS_URL>"

curl -s -X POST "$LA_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "event_type": "SpikeUrgent",
    "severity": "CRITICAL",
    "payload": {},
    "recipients": [
      {"address": "<HQ_EMAIL>", "displayName": "본사/경영진"},
      {"address": "<WH_EMAIL>", "displayName": "물류센터"}
    ]
  }'
```

기대 응답: `{"result":"ok"}`  
기대 메일 내용: "SNS 화제 도서가 감지되었습니다. 24h 내 폭증 매출 가능성."

### Logic Apps 실행 이력 확인

```bash
az logic workflow run list \
  -g rg-bookflow \
  -n la-bookflowmj-notification \
  --query "[].{status:status, startTime:startTime}" \
  -o table
```

---

## 5. notification-svc 경유 실행

Pod 내부에서 notification-svc API를 호출하면 수신자 결정 → Logic Apps 호출 → DB 기록이 모두 수행됩니다.

```bash
# Pod 이름 확인
kubectl get pod -n bookflow -l app=notification-svc

# 패턴 2: DailyPlanFinalized
kubectl exec -it <pod-name> -n bookflow -- \
  python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/notification/send',
    data=json.dumps({
        'event_type': 'DailyPlanFinalized',
        'severity': 'INFO',
        'payload_summary': {}
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer mock-token-hq-admin'},
    method='POST'
)
print(urllib.request.urlopen(req).read().decode())
"

# 패턴 3: SpikeUrgent
kubectl exec -it <pod-name> -n bookflow -- \
  python3 -c "
import urllib.request, json
req = urllib.request.Request(
    'http://localhost:8000/notification/send',
    data=json.dumps({
        'event_type': 'SpikeUrgent',
        'severity': 'CRITICAL',
        'payload_summary': {}
    }).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer mock-token-hq-admin'},
    method='POST'
)
print(urllib.request.urlopen(req).read().decode())
"
```

> `NOTIFICATION_AUTH_MODE: "mock"` 설정 시 `Authorization: Bearer mock-token-hq-admin` 헤더로 인증 통과합니다.

---

## 6. 검증 방법

### 6-1. 메일 수신 확인

| 패턴 | 수신 그룹 |
|------|----------|
| 2 DailyPlanFinalized | 본사/경영진 + 물류센터 + 지점 (`NOTIFICATION_CONTACT_*` configmap 참조) |
| 3 SpikeUrgent | 본사/경영진 + 물류센터 |

### 6-2. Logic Apps 실행 이력 (Azure)

```bash
az logic workflow run list \
  -g rg-bookflow \
  -n la-bookflowmj-notification \
  --query "[].{status:status, startTime:startTime, trigger:trigger.name}" \
  -o table
```

### 6-3. ACS Email 발송 로그

```bash
az monitor log-analytics query \
  -w /subscriptions/e98a94bb-7532-4e49-8a36-bc42e30d5a81/resourceGroups/rg-bookflow/providers/Microsoft.OperationalInsights/workspaces/law-bookflowmj \
  --analytics-query "ACSEmailSendMailOperational | where TimeGenerated > ago(1h) | project TimeGenerated, SenderAddress, RecipientAddress, DeliveryStatus | order by TimeGenerated desc | take 20" \
  -o table
```

### 6-4. notification-svc 로그 확인

```bash
kubectl logs -n bookflow -l app=notification-svc --tail=50 | grep -E "logic_apps|SENT|FAILED"
```

---

## 7. 향후 작업 (TODO)

### 즉시 필요

| # | 작업 | 담당 | 비고 |
|---|------|------|------|
| 1 | **Azure Portal에서 Logic Apps Consumption workflow 업데이트** | Azure 담당 | `la-bookflowmj-notification` → Logic app designer → Code view → `definition` 블록을 `workflows/notification/workflow.json`의 `definition`으로 교체. Switch case를 DailyPlanFinalized·SpikeUrgent 2개만 남기고 메일 본문 단순화 반영 |
| 2 | **패턴 2·3 메일 수신 재검증** | 검증 담당 | workflow.json 교체 후 §4 수동 실행 명령으로 테스트. ms8405493 / rladudgjs0427 / woohek00 수신 확인 |
| 3 | **notification-svc RDS 연결 복구** | EKS 담당 | configmap의 `NOTIFICATION_RDS_HOST: "${RDS_HOST}"` → 실제 RDS 엔드포인트로 교체 후 `kubectl apply` + rollout restart. Pod CrashLoopBackOff 해소 필요 |

### WorkflowStandard 쿼터 승인 후 (선택 — VPN 경유 전환)

| # | 작업 | 비고 |
|---|------|------|
| 4 | WorkflowStandard 쿼터 신청 | Azure Portal → 구독 → Usage + quotas → WorkflowStandard Japan West → Request increase 1 |
| 5 | Logic Apps Standard 배포 | `az deployment group create --template-file modules/logicapp.bicep --parameters parameters/logicapp-only.json` |
| 6 | Private Endpoint IP 확인 후 Private DNS Zone A 레코드 검증 | `az network private-endpoint show ... --query customDnsConfigs` |
| 7 | EKS CoreDNS Conditional Forwarder 설정 | `kubectl edit configmap coredns -n kube-system` — `privatelink.azurewebsites.net:53` forwarder 추가 |
| 8 | ConfigMap URL 교체 | `NOTIFICATION_LOGIC_APPS_URL` → Standard trigger URL (FQDN, sig= 포함) |
| 9 | VPN 경로 DNS·TCP 연결 최종 검증 | Pod에서 nslookup + curl로 172.16.2.x private IP 반환 확인 |

---

## 파일 위치 참조

| 파일 | 위치 |
|------|------|
| Logic Apps Bicep | `BookFlowAI-Platform/infra/azure/modules/logicapp.bicep` |
| Workflow 정의 | `BookFlowAI-Platform/infra/azure/workflows/notification/workflow.json` |
| Bicep 파라미터 | `BookFlowAI-Platform/infra/azure/parameters/logicapp-only.json` |
| notification-svc 라우트 | `BookFlowAI-Apps/eks-pods/notification-svc/src/routes/notification.py` |
| 수신자 매핑 | `BookFlowAI-Apps/eks-pods/notification-svc/src/recipients.py` |
| K8s ConfigMap | `BookFlowAI-Apps/eks-pods/notification-svc/k8s/configmap.yaml` |
