# Cross-Cloud VPN 연결 가이드 (AWS ↔ GCP / Azure)

BookFlow 발표일 전용. AWS Transit Gateway 기반 Site-to-Site VPN으로 GCP와 Azure를 연결한다.

---

## 구성 개요

```
GCP HA VPN (interface 0)                     Azure VPN Gateway
  34.157.64.22                                  135.149.x.x
       │  IKEv2 / BGP ASN 64514                      │  IKEv2 / BGP ASN 65001
       │                                              │
  ─────┴──────────────────────────────────────────────┴─────
              AWS Transit Gateway (ASN 64512)
              tgw-0b9be8a371526c614 · ap-northeast-1
  ─────┬──────────────────────────────────────────────┬─────
       │                                              │
  bookflow-ai VPC     sales-data VPC     data VPC    egress VPC ...
```

**터널 파라미터 (GCP)**

| | Tunnel 0 | Tunnel 1 |
|---|---|---|
| AWS 외부 IP | 동적 할당 | 동적 할당 |
| Inside CIDR | 169.254.213.136/30 | 169.254.100.72/30 |
| AWS BGP IP | 169.254.213.137 | 169.254.100.73 |
| GCP BGP IP | 169.254.213.138 | 169.254.100.74 |
| IKE | v2 / AES128 / SHA2-256 / DH14 | 동일 |
| GCP interface | 0 (양쪽 동일) | 0 (양쪽 동일) |

**터널 파라미터 (Azure)**

| | Tunnel 0 | Tunnel 1 |
|---|---|---|
| Inside CIDR | 169.254.21.4/30 | 169.254.21.8/30 |
| IKE | v2 / AES256 / SHA2-256 / DH14 | 동일 |

---

## 사전 준비 (1회)

### 1. GCP HA VPN Interface 0 IP 확인

```bash
gcloud compute vpn-gateways describe bookflow-aws-ha-vpn \
  --region=asia-northeast1 \
  --format="value(vpnInterfaces[0].ipAddress)"
# 예: 34.157.64.22
```

> **주의**: Interface 0 IP만 사용. Interface 1 IP를 입력하면 IKE 인증 실패.
> AWS Customer Gateway는 단일 IP만 지원하므로 Interface 0 IP로 고정.

### 2. Azure VPN Gateway Public IP 확인

```bash
az network public-ip show \
  --resource-group bookflow-rg \
  --name bookflow-vpn-gw-pip \
  --query ipAddress -o tsv
# 예: 135.149.169.236
```

### 3. PSK 준비

GCP, Azure 각각 동일한 PSK를 사용(터널 0·1 동일). 재배포 시 GCP/Azure 측 PSK도 같이 변경해야 한다.

```bash
# 랜덤 PSK 생성 예시
openssl rand -base64 32
```

---

## 실행 (발표일 당일)

### Step 1. 환경변수 설정

```bash
# GCP만 연결하는 경우
export BOOKFLOW_GCP_VPN_GW_IP=34.157.64.22     # GCP interface 0 IP
export BOOKFLOW_GCP_VPN_PSK=<PSK>              # 선택 (없으면 AWS 자동생성)

# Azure만 연결하는 경우
export BOOKFLOW_AZURE_VPN_GW_IP=135.149.169.236
export BOOKFLOW_AZURE_VPN_PSK=<PSK>            # 선택

# GCP + Azure 동시 연결
export BOOKFLOW_GCP_VPN_GW_IP=34.157.64.22
export BOOKFLOW_GCP_VPN_PSK=<GCP_PSK>
export BOOKFLOW_AZURE_VPN_GW_IP=135.149.169.236
export BOOKFLOW_AZURE_VPN_PSK=<AZURE_PSK>
```

`.env.local` 파일에 저장하면 매번 export 불필요:

```bash
# scripts/aws/config/.env.local
BOOKFLOW_GCP_VPN_GW_IP=34.157.64.22
BOOKFLOW_GCP_VPN_PSK=<PSK>
BOOKFLOW_AZURE_VPN_GW_IP=135.149.169.236
BOOKFLOW_AZURE_VPN_PSK=<PSK>
```

### Step 2. Cross-Cloud 배포

```bash
cd BookFlowAI-Platform
./scripts/ops/network-mode.sh tgw
```

내부 순서:
1. `customer-gateway` 스택 — CGW 생성 (GCP interface 0 IP, Azure PIP)
2. `tgw` 스택 — Transit Gateway 생성
3. `tgw-vpc-routes` + `vpn-site-to-site` 병렬 — VPC 라우트 + VPN 연결 생성 (IKEv2)
4. TGW attachment → route table 연결 + BGP propagation 자동 활성화
5. 터널 Outside IP 출력

### Step 3. GCP 측 VPN 정보 확인 및 전달

```bash
bash scripts/aws/ops/gcp-vpn-info.sh
```

출력 내용을 우혁에게 전달. 우혁이 `infra/gcp/20-network-daily/terraform.tfvars`에 붙여넣고 `terraform apply`.

출력 예시:
```
  VPN Connection ID : vpn-0xxxxxxxxxxxxxxx
  Tunnel 0 Outside IP  : 35.79.xxx.xxx
  Tunnel 0 Inside CIDR : 169.254.213.136/30  (AWS=169.254.213.137  GCP=169.254.213.138)
  Tunnel 1 Outside IP  : 175.41.xxx.xxx
  Tunnel 1 Inside CIDR : 169.254.100.72/30   (AWS=169.254.100.73   GCP=169.254.100.74)
  PSK : <PSK>

  bgp_sessions = {
    tunnel0 = {
      vpn_gateway_interface           = 0
      peer_external_gateway_interface = 0
      shared_secret                   = "<PSK>"
      router_ip_cidr                  = "169.254.213.138/30"
      peer_ip_address                 = "169.254.213.137"
      advertised_route_priority       = 100
    }
    tunnel1 = {
      vpn_gateway_interface           = 0   # GCP는 양쪽 모두 interface 0
      peer_external_gateway_interface = 1
      shared_secret                   = "<PSK>"
      router_ip_cidr                  = "169.254.100.74/30"
      peer_ip_address                 = "169.254.100.73"
      advertised_route_priority       = 100
    }
  }
```

### Step 4. Azure 측 VPN 정보 전달

Azure에는 다음 정보를 민지에게 전달:

```bash
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-azure" \
  --query "VpnConnections[0].VgwTelemetry[*].{IP:OutsideIpAddress,InsideCIDR:OutsideIpAddress}" \
  --output table
```

Azure 측 설정값:
- Tunnel 0 Inside CIDR: `169.254.21.4/30` (AWS BGP: `.5`, Azure BGP: `.6`)
- Tunnel 1 Inside CIDR: `169.254.21.8/30` (AWS BGP: `.9`, Azure BGP: `.10`)
- IKE: v2 / AES256 / SHA2-256 / DH14

---

## 상태 확인

### AWS 터널 상태

```bash
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-*" \
  --query "VpnConnections[*].{Name:Tags[?Key=='Name']|[0].Value,T1:VgwTelemetry[0].StatusMessage,T2:VgwTelemetry[1].StatusMessage}" \
  --output table
```

정상:
```
bookflow-vpn-gcp   | 3 BGP ROUTES | 3 BGP ROUTES
bookflow-vpn-azure | 5 BGP ROUTES | 5 BGP ROUTES
```

### GCP 터널 상태

```bash
gcloud compute vpn-tunnels list \
  --format="table(name,status,detailedStatus)"
```

정상: `STATUS = ESTABLISHED`

### TGW 라우트 테이블

```bash
aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id tgw-rtb-022bb2cfc013f504f \
  --filters "Name=state,Values=active" \
  --query "Routes[*].{Dest:DestinationCidrBlock,Type:Type}" \
  --output table
```

정상 시 확인 경로:

| 목적지 | 출처 |
|--------|------|
| 10.0.0.0/16 ~ 10.4.0.0/16 | AWS VPC (propagated) |
| 192.168.10.0/24 | GCP VPC (BGP propagated) |
| 192.168.254.0/28 | GCP internal (BGP propagated) |
| 10.50.0.0/24 | GCP PSC endpoint (BGP propagated) |
| 172.16.0.0/16 | Azure VNet (BGP propagated) |

---

## 종료 (발표 후)

```bash
./scripts/ops/network-mode.sh peering
```

TGW + VPN 전체 삭제. GCP/Azure 측은 담당자가 `terraform destroy`.

---

## 트러블슈팅

### IPSEC IS DOWN (터널 미연결)

**원인 1: GCP IP 오입력** — Interface 0 IP가 아닌 Interface 1 IP를 입력한 경우

```bash
# GCP 실제 interface IP 확인
gcloud compute vpn-gateways describe bookflow-aws-ha-vpn \
  --region=asia-northeast1 \
  --format="table(vpnInterfaces[].id,vpnInterfaces[].ipAddress)"
```

Customer Gateway IP와 Interface 0 IP가 일치해야 한다.

```bash
aws ec2 describe-customer-gateways \
  --filters "Name=tag:Name,Values=bookflow-cgw-gcp*" \
  --query "CustomerGateways[*].{IP:IpAddress,State:State}" \
  --output table
```

불일치 시 → `cross-cloud.sh down` 후 올바른 IP로 재실행.

**원인 2: PSK 불일치** — AWS와 GCP/Azure PSK가 다름

```bash
# AWS PSK 확인
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-gcp" \
  --query "VpnConnections[0].Options.TunnelOptions[*].{IP:OutsideIpAddress,PSK:PreSharedKey}" \
  --output table
```

GCP PSK 확인:
```bash
gcloud compute vpn-tunnels describe bookflow-aws-tunnel-tunnel0 \
  --region=asia-northeast1 --format="value(sharedSecretHash)"
```

불일치 시 → GCP 측 PSK를 AWS 값으로 변경.

**원인 3: GCP IKE 버전** — GCP HA VPN은 IKEv2 전용. AWS가 IKEv1이면 연결 안 됨

```bash
# IKEv2 적용 여부 확인
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-gcp" \
  --query "VpnConnections[0].Options.TunnelOptions[*].IkeVersions" \
  --output json
# 정상: [[{"Value":"ikev2"}], [{"Value":"ikev2"}]]
```

`ikev2`가 아닌 경우 → `cross-cloud.sh down` 후 재실행 (템플릿에 IKEv2 이미 반영됨).

### NO_INCOMING_PACKETS (GCP 콘솔)

GCP가 IKE 패킷을 보내도 AWS가 응답하지 않는 상태.
대부분 CGW IP가 GCP 실제 interface IP와 다른 경우.

```bash
# GCP 터널 상태 확인
gcloud compute vpn-tunnels list \
  --format="table(name,status,detailedStatus,peerIp)"
```

`peerIp`(AWS 터널 IP)가 현재 배포된 VPN 연결의 Outside IP와 일치하는지 확인.

### TGW 라우트 테이블에 GCP 경로 없음

```bash
# VPN 어태치먼트 propagation 확인
aws ec2 get-transit-gateway-route-table-propagations \
  --transit-gateway-route-table-id tgw-rtb-022bb2cfc013f504f \
  --query "TransitGatewayRouteTablePropagations[?ResourceType=='vpn']" \
  --output table
```

`State = disabled`인 경우:
```bash
bash scripts/aws/ops/tgw-vpn-attach.sh
```

### GCS 접속 타임아웃 (Glue ETL)

TGW 라우트 테이블에 `10.50.0.0/24` 경로가 있는지 확인:

```bash
aws ec2 search-transit-gateway-routes \
  --transit-gateway-route-table-id tgw-rtb-022bb2cfc013f504f \
  --filters "Name=state,Values=active" \
  --query "Routes[?DestinationCidrBlock=='10.50.0.0/24']" \
  --output table
```

없는 경우 BGP 광고 대기(수 분). 그래도 없으면 수동 추가:

```bash
VPN_ATT=$(aws ec2 describe-transit-gateway-attachments \
  --filters "Name=resource-type,Values=vpn" \
  --query "TransitGatewayAttachments[?contains(Tags[?Key=='Name'].Value|[0], 'gcp')].TransitGatewayAttachmentId" \
  --output text)

aws ec2 create-transit-gateway-route \
  --transit-gateway-route-table-id tgw-rtb-022bb2cfc013f504f \
  --destination-cidr-block 10.50.0.0/24 \
  --transit-gateway-attachment-id "$VPN_ATT"
```

---

## 주요 리소스 ID

| 리소스 | ID |
|--------|-----|
| Transit Gateway | tgw-0b9be8a371526c614 |
| TGW Route Table | tgw-rtb-022bb2cfc013f504f |
| GCP HA VPN Gateway (GCP) | bookflow-aws-ha-vpn (asia-northeast1) |
| GCP Cloud Router (GCP) | bookflow-aws-cr (asia-northeast1) |
| GCP VPC (GCP) | bookflow-vpc |
| CGW (GCP) | cgw-06f5cd2b79f580af6 (34.157.64.22) |

---

*작성: 2026-05-14 · 민지 · BookFlow V6.2 cross-cloud VPN 장애 대응 후 정리*
