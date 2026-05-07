# Tier 60 · Network Cross-Cloud (📆 Phase 3-4 · task-auth-pod · task-forecast · task-client-vpn)

## 이 Tier의 역할

**Transit Gateway · Site-to-Site VPN · Client VPN** — cross-cloud (Azure · GCP) 통신 + 담당자 access. base-up 미포함 · 작업 시 task 가 deploy.

## Stack (3개)

| YAML | 내용 | 사용 task |
|---|---|---|
| `tgw.yaml` | Transit Gateway Hub + Route Table + 4 VPC Attachments (Ansible 제외) + Association + Propagation | task-auth-pod · task-forecast |
| `vpn-site-to-site.yaml` | VPN Connection (Azure + GCP) · TGW VPN Attachment · IPSec Tunnel · CGW IP parameter-driven | task-auth-pod (Azure) · task-forecast (GCP) |
| `client-vpn.yaml` | Client VPN Endpoint + Subnet Association + Authorization Rule + ACM mutual auth + CW Logs | task-client-vpn (신규) |

## Build vs Cross-cloud 분리

| 작업 | 사용 자원 | 비고 |
|---|---|---|
| **Build phase (Phase 1-2)** | Tier 10 Peering 만 (TGW 미사용) | bookflow-ai-data · bookflow-ai-egress · sales-data-egress · egress-data · ansible-data |
| **Phase 3-4 (cross-cloud)** | Tier 60 TGW + S2S VPN | auth-pod → Azure Entra · forecast-svc → GCP Vertex AI |

→ Tier 60 TGW 는 **cross-cloud 전용** · 같은 AWS 계정 내 VPC 통신은 Peering 으로 충분.

## TGW 흐름 (V6.2 기반)

```
[Phase 3-4 활성 시]

Azure VPN GW (민지)         GCP HA VPN (우혁)
     ↓ IPSec                    ↓ IPSec
[CGW Azure]                [CGW GCP]
     ↓                           ↓
[VPN Connection Azure]    [VPN Connection GCP]
     ↓ TGW VPN Attach          ↓
            ↘                  ↙
              [TGW Route Table]
              ↗      ↑      ↖
   [TGW Attach BookFlow AI]
   [TGW Attach Sales Data]
   [TGW Attach Egress]
   [TGW Attach Data]
```

→ auth-pod (BookFlow AI Private) → TGW → VPN Azure → Azure Entra OIDC
→ forecast-svc (BookFlow AI) → TGW → VPN GCP → Vertex AI Endpoint

## CGW IP 주입 흐름

1. **민지** (Azure 팀): `az network vnet-gateway show` 로 Azure VPN GW Public IP 확인 후 영헌에게 전달
2. **영헌**: `$env:BOOKFLOW_AZURE_VPN_GW_IP = "203.0.113.10"` 설정
3. `task-auth-pod.ps1` 실행 → CGW Azure update-stack + VPN Connection 생성
4. (GCP 도 동일 패턴 · 우혁 → 영헌 → task-forecast.ps1)

## Client VPN 사용

**대상**: 3 담당자 (영헌·민지·우혁)

**Bootstrap (1회 manual)**:
1. easy-rsa 로 server cert + client CA + 3 client cert 생성
2. ACM 에 import (Tier 00 acm.yaml)
3. `task-client-vpn.ps1` 실행 → Endpoint + Subnet Association + Authorization
4. `aws ec2 export-client-vpn-client-configuration` → .ovpn 파일 생성
5. 각 담당자 PC 에 .ovpn + client cert + key 배포 (Secrets Manager 또는 보안 채널)

**접속 후 가능 작업**:
- Internal ALB (https://dashboard.bookflow.internal) 접근
- kubectl get pods -n bookflow (EKS 디버그)
- psql -h bookflow-postgres -U bookflow_admin (RDS 접속)

## Import / Export

### tgw.yaml
- Imports: 4 VPC ID + 8 Subnet ID (각 VPC 의 AZ1/AZ2)
- Exports: TgwId · TgwRouteTableId · 4 Attachment ID

### vpn-site-to-site.yaml
- Imports: cgw-azure-id · cgw-gcp-id · tgw-id · tgw-rt-id
- Exports: vpn-azure-id · vpn-gcp-id (조건부)

### client-vpn.yaml
- Imports: vpc-bookflow-ai-id · subnet-bookflow-ai-private-az1 · acm-client-vpn-server-arn · acm-client-vpn-client-ca-arn
- Exports: client-vpn-endpoint-id

## 검증

```powershell
# lint
cfn-lint infra\aws\60-network-cross-cloud\*.yaml

# TGW 상태
aws ec2 describe-transit-gateways --query 'TransitGateways[?contains(Tags[?Key==`Name`].Value | [0], `bookflow`)].{id:TransitGatewayId,state:State}'

# TGW Attachment 4개 확인
aws ec2 describe-transit-gateway-attachments --filters "Name=transit-gateway-id,Values=<tgw-id>"

# VPN Tunnel 상태 (활성 시)
aws ec2 describe-vpn-connections --filters "Name=tag:Name,Values=bookflow-vpn-*" --query 'VpnConnections[].{id:VpnConnectionId,state:State,tunnels:VgwTelemetry[].Status}'

# Client VPN Endpoint
aws ec2 describe-client-vpn-endpoints --client-vpn-endpoint-ids <id>

# Client VPN config 다운로드
aws ec2 export-client-vpn-client-configuration --client-vpn-endpoint-id <id> --output text > bookflow.ovpn
```

## 비용 (Tier 60 · Phase 기반 · 비용산정 V1)

| 자원 | 시간당 | 월 비용 (가동 시간) |
|---|---|---|
| TGW Hub | $0 | $0 |
| TGW VPC Attachment × 4 | $0.07 × 4 | $17.64 (63h · P3+P4) |
| TGW VPN Attachment × 2 | $0.07 × 2 | $8.82 (63h) |
| TGW Data Processing | $0.02/GB | $0.30 (15GB) |
| Site-to-Site VPN × 2 | $0.048 × 2 | $6.05 (63h) |
| Customer Gateway × 2 | $0 | $0 |
| Client VPN Endpoint | $0.15/h | $12.15 (81h) |
| Client VPN Connection × 3 user | $0.05 × 3 | $12.15 |

**합계 예상**: ~$57/월 (모두 활성 시 · 실제 가동 Phase 별로)

## 비고

- **Ansible VPC TGW 미연결**: Peering (ansible-data) 만으로 충분 · TGW Attachment 비용 절감
- **VPN Tunnel HA**: 각 VPN Connection 당 2 tunnel (자동 · BGP 학습)
- **TGW ECMP**: 활성 (`VpnEcmpSupport: enable`) · Multi-tunnel 부하 분산
- **Client VPN SplitTunnel**: true (사용자 인터넷 트래픽은 자기 ISP 로 · VPN 으로는 VPC traffic 만)
- **VPN port**: UDP 443 (방화벽 우회용)
- **Session timeout**: 8 시간 (재인증 필요)
