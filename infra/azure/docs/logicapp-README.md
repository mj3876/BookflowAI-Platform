# Logic Apps 알람 시스템 — 구축·실행·검증 가이드

> 대상: 발송 패턴 1–6 (AWS EKS → Azure Logic Apps → ACS Email, VPN 전용 통신)  
> 패턴 7(LambdaAlarm), 8(DeploymentRollback)은 구축하지 않음

---

## 목차

1. [아키텍처 개요](#1-아키텍처-개요)
2. [발송 패턴 1–6 명세](#2-발송-패턴-16-명세)
3. [현재 인프라 상태](#3-현재-인프라-상태)
4. [구축 가이드](#4-구축-가이드)
5. [실행 가이드 (workflow 배포)](#5-실행-가이드-workflow-배포)
6. [검증 방법](#6-검증-방법)

---

## 1. 아키텍처 개요

```
[EKS notification-svc]
        │
        │  POST (VPN 터널)
        │  172.16.2.x (private endpoint IP)
        ▼
[AWS TGW] ──VPN Tunnel──▶ [Azure VPN GW] ──▶ [VNet 172.16.0.0/16]
                                                       │
                                          snet-bookflowmj-services
                                          172.16.2.0/24
                                                       │
                                          [Private Endpoint pe-la-bookflowmj]
                                                       │
                                          [Logic Apps Standard la-bookflowmj]
                                          (.azurewebsites.net → private IP)
                                                       │
                                          [ACS Email (Managed Identity)]
                                                       │
                                          [수신자: 본사/물류센터/지점]
```

### DNS 흐름

```
EKS Pod nslookup la-bookflowmj.azurewebsites.net
  → CoreDNS Conditional Forwarder (privatelink.azurewebsites.net)
  → Azure Private DNS Zone (172.16.2.x 반환)
  ← public IP가 아니라 private endpoint IP여야 함
```

---

## 2. 발송 패턴 1–6 명세

| # | event_type | 심각도 | 수신자 | 트리거 방식 | 특이사항 |
|---|-----------|--------|--------|-------------|---------|
| 1 | `AutoExecutedUrgent` | CRITICAL | 본사/경영진 | 즉시 | 07:00 배치 자동 승인 시 |
| 2 | `DailyPlanFinalized` | INFO | 본사+물류센터+지점 전체 | 즉시 | PENDING=0 확인 후 plan-watcher가 별도 발송 |
| 3 | `SpikeUrgent` | CRITICAL | 본사+물류센터 | 즉시 | z-score 급등 도서 감지 |
| 4 | `ApprovalDelayed` | WARNING | 본사+물류센터 | 즉시 | 24h+ 양쪽 승인 대기 |
| 5 | `InboundRejected` | WARNING | 본사+물류센터 | **5분 배치** | wh_id별 Redis 버퍼 집계 후 발송 |
| 6 | `NewBookRequest` | INFO | 본사/경영진 | 즉시 | 출판사 신간 신청 접수 시 |

### 수신자 그룹 (configmap 기준)

| 그룹 | configmap 키 | 현재 값 |
|------|-------------|---------|
| 본사/경영진 | `NOTIFICATION_CONTACT_HQ_EMAILS` | ms8405493@gmail.com |
| 물류센터 | `NOTIFICATION_CONTACT_WH_EMAILS` | rladudgjs0427@gmail.com |
| 지점 전체 | `NOTIFICATION_CONTACT_BRANCH_EMAILS` | admin@bleach10905gmail.onmicrosoft.com |

### 호출 흐름

```python
# notification-svc/src/routes/notification.py

# 1. event_type → 수신자 결정
recipients = get_recipients(event_type, payload)
# AutoExecutedUrgent → [본사]
# SpikeUrgent        → [본사, 물류센터]
# DailyPlanFinalized → [본사, 물류센터, 지점]

# 2. Logic Apps 호출 (configmap URL이 sig= 포함 시 그대로 사용)
url = settings.logic_apps_url   # NOTIFICATION_LOGIC_APPS_URL
POST url body = {
    "event_type": "AutoExecutedUrgent",
    "severity": "CRITICAL",
    "payload": {"n": 17, "critical": 5, "urgent": 12},
    "recipients": [{"address": "ms8405493@gmail.com", "displayName": "본사/경영진"}]
}

# 3. Logic Apps Switch case → ACS Email 발송
```

### InboundRejected 5분 배치 특이사항

```
notification-svc POST /notification/send (InboundRejected)
  → Redis RPUSH inbound_rejected_buffer:{wh_id}   # 즉시 발송 없음
  ← status: BUFFERED

[5분마다 main.py _flush_inbound_rejected()]
  → Redis LRANGE + DEL
  → 권역별 집계 (wh_id=1: 수도권, 기타: 영남)
  → Logic Apps POST (InboundRejected)
```

---

## 3. 현재 인프라 상태

### Azure

| 리소스 | 상태 | 비고 |
|--------|------|------|
| VPN Gateway `vpngw-bookflowmj` | ✅ Succeeded | VpnGw1AZ, IP: 135.149.169.236, ASN: 65001 |
| VPN Connection `conn-bookflowmj-aws-active` | ✅ Connected | BGP: ON |
| Logic Apps (4개) Consumption | ✅ Enabled | `la-bookflowmj-notification` 외 3개 |
| Logic Apps Standard (WS1) | ❌ 미배포 | **WorkflowStandard 쿼터 0 → 신청 필요** |
| Private Endpoint | ❌ 미배포 | Standard 배포 후 생성 |
| Private DNS Zone | ❌ 미배포 | Standard 배포 후 생성 |
| Storage Account (Standard용) | ❌ 미배포 | Standard 배포 후 생성 |

### AWS

| 리소스 | 상태 | 비고 |
|--------|------|------|
| TGW `tgw-072f565670b41c323` | ✅ available | ASN: 64512 |
| VPN Connection (Azure행) | ✅ available | 터널 2개 UP |
| 라우팅 172.16.0.0/16 → TGW | ✅ active | 7개 VPC 라우팅 테이블 반영 완료 |
| notification-svc configmap | ✅ Consumption URL | Standard PE 배포 후 교체 필요 |

### 코드 준비 상태

| 항목 | 상태 |
|------|------|
| notification.py (패턴 1–6 처리) | ✅ 완료 |
| recipients.py (수신자 매핑) | ✅ 완료 |
| workflow.json (Switch case 1–6) | ✅ 완료 |
| logicapp.bicep (Standard IaC) | ✅ 완료 (쿼터 승인 후 배포 가능) |

---

## 4. 구축 가이드

### Step 1. WorkflowStandard 쿼터 신청 (필수)

```
Azure Portal → 구독 → Usage + quotas
→ 필터: "WorkflowStandard" → Japan West
→ Request increase → 최솟값 1 → 제출
(승인: 보통 1–2 영업일)
```

### Step 2. Logic Apps Standard 배포

쿼터 승인 후 실행:

```bash
cd BookFlowAI-Platform/infra/azure

az deployment group create \
  -g rg-bookflow \
  --template-file modules/logicapp.bicep \
  --parameters parameters/logicapp-only.json \
  --name "logicapp-standard-$(date +%Y%m%d)"
```

배포 소요 시간: 약 5–10분

배포 완료 후 확인:

```bash
# Logic Apps Standard 앱 확인
az webapp list -g rg-bookflow --query "[?kind=='workflowapp,functionapp'].{name:name, state:state, fqdn:defaultHostName}" -o table

# Private Endpoint IP 확인
az network private-endpoint show -g rg-bookflow -n pe-la-bookflowmj \
  --query "customDnsConfigs[0].ipAddresses[0]" -o tsv

# Private DNS Zone A 레코드 확인
az network private-dns record-set a list -g rg-bookflow \
  -z privatelink.azurewebsites.net -o table
```

### Step 3. Workflow 파일 배포 (zip deploy)

Logic Apps Standard는 workflow.json 파일을 별도 배포해야 합니다.

```bash
cd BookFlowAI-Platform/infra/azure

# zip 패키지 생성
cd workflows
zip -r ../la-workflows.zip .
cd ..

# zip 배포
az logicapp deployment source config-zip \
  -g rg-bookflow \
  -n la-bookflowmj \
  --src la-workflows.zip
```

배포 후 워크플로우 확인:

```bash
az logicapp workflow list -g rg-bookflow -n la-bookflowmj -o table
```

### Step 4. Notification Trigger URL 확인 및 ConfigMap 교체

```bash
# notification 워크플로우 HTTP trigger URL 조회
az logicapp workflow trigger show \
  -g rg-bookflow \
  -n la-bookflowmj \
  --workflow-name notification \
  --trigger-name manual \
  --query "value" -o tsv
```

출력 예시:
```
https://la-bookflowmj.azurewebsites.net/api/notification/triggers/manual/invoke?api-version=2022-05-01&sp=...&sv=1.0&sig=<KEY>
```

configmap 교체:

```yaml
# BookFlowAI-Apps/eks-pods/notification-svc/k8s/configmap.yaml
NOTIFICATION_LOGIC_APPS_URL: "https://la-bookflowmj.azurewebsites.net/api/notification/triggers/manual/invoke?api-version=2022-05-01&sp=...&sig=<KEY>"
```

> FQDN URL을 사용해야 합니다. private IP(10.x.x.x)를 직접 넣으면 TLS 인증서와 Host header가 깨집니다.

적용:

```bash
kubectl apply -f eks-pods/notification-svc/k8s/configmap.yaml -n bookflow
kubectl rollout restart deployment/notification-svc -n bookflow
```

### Step 5. EKS CoreDNS Conditional Forwarder 설정

EKS Pod에서 `la-bookflowmj.azurewebsites.net`이 private endpoint IP로 resolve되려면 CoreDNS에 forwarder 추가가 필요합니다.

```bash
kubectl edit configmap coredns -n kube-system
```

아래 블록 추가:

```
privatelink.azurewebsites.net:53 {
    forward . <Azure_Private_DNS_Resolver_IP>
    cache 30
}
```

> `Azure_Private_DNS_Resolver_IP`: Azure Private DNS Resolver 또는 Azure VNet DNS 서버 IP (168.63.129.16)  
> VPN 터널을 통해 168.63.129.16으로 DNS 쿼리가 전달되어 private zone이 응답해야 합니다.

---

## 5. 실행 가이드 (workflow 배포)

### 현재 상태에서 테스트 (Consumption, 임시)

Standard 배포 전에도 현재 Consumption Logic Apps로 패턴 1–6 테스트 가능합니다.  
configmap의 URL이 이미 Consumption trigger URL로 설정됩니다.

```bash
# notification-svc Pod 이름 확인
kubectl get pod -n bookflow -l app=notification-svc

# 패턴 1: AutoExecutedUrgent 테스트 (임시 mock 인증 모드)
kubectl exec -it <pod-name> -n bookflow -- \
  curl -s -X POST http://localhost:8000/notification/send \
  -H "Content-Type: application/json" \
  -H "X-Mock-User: hq-admin" \
  -d '{
    "event_type": "AutoExecutedUrgent",
    "severity": "CRITICAL",
    "payload_summary": {"n": 3, "critical": 1, "urgent": 2}
  }'
```

### Standard 배포 후 VPN 경로 테스트

```bash
# Pod에서 DNS resolve 확인
kubectl exec -it <pod-name> -n bookflow -- \
  nslookup la-bookflowmj.azurewebsites.net

# 기대 결과: private endpoint IP (172.16.2.x) 반환
# 실패 시: public IP (40.x.x.x 등) 반환 → CoreDNS forwarder 미설정

# VPN 경로 TCP 연결 확인
kubectl exec -it <pod-name> -n bookflow -- \
  curl -v --connect-timeout 5 \
  https://la-bookflowmj.azurewebsites.net/api/notification/triggers/manual/invoke 2>&1 | head -30
```

---

## 6. 검증 방법

### 6-1. 메일 실제 발송 확인

각 패턴별 curl 명령으로 notification-svc를 직접 호출하여 메일 수신 여부 확인:

```bash
# 패턴 1: AutoExecutedUrgent → 본사 메일 (ms8405493@gmail.com)
curl -X POST http://<notification-svc-svc>:8000/notification/send \
  -H "Content-Type: application/json" \
  -d '{"event_type":"AutoExecutedUrgent","severity":"CRITICAL","payload_summary":{"n":5,"critical":2,"urgent":3}}'

# 패턴 3: SpikeUrgent → 본사+물류센터 메일
curl -X POST http://<notification-svc-svc>:8000/notification/send \
  -H "Content-Type: application/json" \
  -d '{"event_type":"SpikeUrgent","severity":"CRITICAL","payload_summary":{"title":"테스트도서","isbn13":"9791100000001","z_score":4.2,"mentions_count":1500,"shortage_stores":3}}'

# 패턴 6: NewBookRequest → 본사 메일
curl -X POST http://<notification-svc-svc>:8000/notification/send \
  -H "Content-Type: application/json" \
  -d '{"event_type":"NewBookRequest","severity":"INFO","payload_summary":{"n":2}}'
```

수신 확인 대상:

| 패턴 | 수신 확인 메일 |
|------|--------------|
| 1 AutoExecutedUrgent | ms8405493@gmail.com |
| 2 DailyPlanFinalized | ms8405493@gmail.com + rladudgjs0427@gmail.com + admin@bleach10905gmail.onmicrosoft.com |
| 3 SpikeUrgent | ms8405493@gmail.com + rladudgjs0427@gmail.com |
| 4 ApprovalDelayed | ms8405493@gmail.com + rladudgjs0427@gmail.com |
| 5 InboundRejected | ms8405493@gmail.com + rladudgjs0427@gmail.com (5분 후) |
| 6 NewBookRequest | ms8405493@gmail.com |

### 6-2. Pod → Logic Apps 호출 확인

```bash
# notification-svc 로그에서 Logic Apps 호출 결과 확인
kubectl logs -n bookflow -l app=notification-svc --tail=50 | grep -E "logic_apps|SENT|FAILED"

# DB에서 발송 이력 확인
kubectl exec -it <pod-name> -n bookflow -- \
  psql -h $NOTIFICATION_RDS_HOST -U notification_svc -d bookflow -c \
  "SELECT event_type, status, sent_at FROM notifications_log ORDER BY sent_at DESC LIMIT 10;"
```

Logic Apps 실행 이력 확인 (Azure 측):

```bash
# Consumption 실행 이력
az logic workflow run list \
  -g rg-bookflow \
  -n la-bookflowmj-notification \
  --query "[].{status:status, startTime:startTime, trigger:trigger.name}" \
  -o table

# Standard 배포 후
az logicapp workflow run list \
  -g rg-bookflow \
  -n la-bookflowmj \
  --workflow-name notification \
  -o table
```

### 6-3. 수신자 데이터 적합성 확인

각 event_type별로 올바른 수신자가 payload에 담기는지 확인:

```bash
# notification-svc에서 recipients 조회 (디버그 로그 활성화)
kubectl set env deployment/notification-svc -n bookflow NOTIFICATION_LOG_LEVEL=DEBUG

# 로그에서 recipients 확인
kubectl logs -n bookflow -l app=notification-svc --tail=100 | grep "recipients"
```

또는 mock Logic Apps 응답으로 확인 (mock 환경):

```bash
# mock에서 마지막 invocation body 확인
curl http://azure-logic-apps-mock.stubs.svc.cluster.local/workflows/wf-auto-exec-urgent-0004/runs \
  | python3 -m json.tool | grep -A5 "recipients"
```

### 6-4. VPN 통신 경로 확인 (Standard 배포 후)

```bash
# 1. DNS: FQDN이 private IP로 resolve되는지
kubectl exec -it <pod-name> -n bookflow -- nslookup la-bookflowmj.azurewebsites.net
# 기대: Address: 172.16.2.x

# 2. 라우팅: 172.16.0.0/16 → TGW 경유 확인
aws ec2 describe-route-tables \
  --query "RouteTables[?Routes[?DestinationCidrBlock=='172.16.0.0/16']].{rtb:RouteTableId, gw:Routes[?DestinationCidrBlock=='172.16.0.0/16'].TransitGatewayId}" \
  --output table

# 3. VPN 터널 상태 확인
aws ec2 describe-vpn-connections \
  --filters "Name=state,Values=available" \
  --query "VpnConnections[*].VgwTelemetry[*].{ip:OutsideIpAddress, status:Status}" \
  --output table

# 4. Azure VPN Connection 상태 확인
az network vpn-connection show -g rg-bookflow -n conn-bookflowmj-aws-active \
  --query "{status:connectionStatus, bgp:enableBgp}" -o table
# 기대: Connected, BGP: True

# 5. tcptraceroute (optional)
kubectl exec -it <pod-name> -n bookflow -- \
  traceroute -T -p 443 la-bookflowmj.azurewebsites.net
# 경로 상에 172.16.x.x (Azure VNet) 대역이 보여야 VPN 경유 확인됨
```

### 6-5. ACS Email 발송 로그 확인 (Azure)

```bash
# ACS Communication Service 발송 이력 (Log Analytics)
az monitor log-analytics query \
  -w <LOG_ANALYTICS_WORKSPACE_ID> \
  --analytics-query "ACSEmailSendMailOperational | where TimeGenerated > ago(1h) | project TimeGenerated, SenderAddress, RecipientAddress, DeliveryStatus | order by TimeGenerated desc | take 20" \
  -o table
```

---

## 파일 위치 참조

| 파일 | 위치 |
|------|------|
| Logic Apps Bicep | `BookFlowAI-Platform/infra/azure/modules/logicapp.bicep` |
| Workflow 정의 | `BookFlowAI-Platform/infra/azure/workflows/{name}/workflow.json` |
| Bicep 파라미터 | `BookFlowAI-Platform/infra/azure/parameters/logicapp-only.json` |
| notification-svc 라우트 | `BookFlowAI-Apps/eks-pods/notification-svc/src/routes/notification.py` |
| 수신자 매핑 | `BookFlowAI-Apps/eks-pods/notification-svc/src/recipients.py` |
| K8s ConfigMap | `BookFlowAI-Apps/eks-pods/notification-svc/k8s/configmap.yaml` |
| Logic Apps mock | `BookFlowAI-Apps/mocks/azure-logic-apps-mock/src/main.py` |
