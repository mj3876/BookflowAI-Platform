# BookFlow AWS Operations Scripts

> AWS 인프라만 매일 09:00 deploy / 18:00 destroy.  
> GCP · Azure VPN 인프라는 상시 유지 — AWS 재배포 시 터널 peer IP만 업데이트.

---

## 목차

1. [환경 설정 (1회)](#1-환경-설정-1회)
2. [모드 구분](#2-모드-구분)
3. [Mode A — Peering (TGW/VPN 없음, 평일 기본)](#3-mode-a--peering-tgwvpn-없음-평일-기본)
4. [Mode B — TGW/VPN (Cross-Cloud 연동)](#4-mode-b--tgwvpn-cross-cloud-연동)
   - [4-1. 최초 설정 (GCP terraform state 없을 때)](#4-1-최초-설정-gcp-terraform-state-없을-때)
   - [4-2. 일일 AWS 재배포 흐름](#4-2-일일-aws-재배포-흐름)
   - [4-3. 종료 및 원복](#4-3-종료-및-원복)
5. [스크립트 구조](#5-스크립트-구조)
6. [의존성 흐름](#6-의존성-흐름)
7. [환경 변수](#7-환경-변수)
8. [VPN 터널 구성 상세](#8-vpn-터널-구성-상세)
9. [비용](#9-비용)
10. [트러블슈팅](#10-트러블슈팅)
11. [단일 스택 디버깅](#11-단일-스택-디버깅)
12. [로그 · 상태 파일](#12-로그--상태-파일)
13. [FAQ](#13-faq)

---

## 1. 환경 설정 (1회)

```bash
# AWS profile 설정
aws configure --profile bookflow-deploy   # 실 운영
aws configure --profile bookflow-admin    # 테스트용

# Python 의존성
pip install boto3

# 필수 tool 확인
aws --version          # >= 2.x
kubectl version --client
helm version
jq --version
gcloud version         # Mode B 사용 시
terraform version      # Mode B 사용 시 (GCP 쪽)
```

`scripts/aws/config/.env.local` 생성 (gitignored):

```bash
# Entra ID 자격증명 (민감정보 · git 커밋 금지)
BOOKFLOW_ENTRA_CLIENT_ID=<entra-client-id>
BOOKFLOW_ENTRA_TENANT_ID=<entra-tenant-id>

# Mode B 사용 시 — PSK 고정값 등록 (민감정보 · 매번 입력 방지)
BOOKFLOW_GCP_VPN_PSK=<gcp-shared-secret>
BOOKFLOW_AZURE_VPN_PSK=<azure-shared-secret>
```

> `BOOKFLOW_DOMAIN`은 민감정보가 아니므로 `deploy.env` / `admin.env`에 이미 포함되어 있다.

---

## 2. 모드 구분

| | Mode A (Peering) | Mode B (TGW/VPN) |
|---|---|---|
| **용도** | 평일 일반 시연 | Cross-cloud 연동 발표/시연 |
| **VPC 간 통신** | VPC Peering (5개) | Transit Gateway |
| **GCP · Azure 연결** | 없음 | S2S VPN (각 1개 연결, 터널 2개씩) |
| **비용** | ~$136/월 | ~$213/월 |
| **GCP · Azure 인프라** | 무관 | 상시 유지 (AWS만 재배포) |
| **시작 명령** | `start-day.sh` | `start-day.sh` + `network-mode.sh tgw` |

> **핵심**: GCP · Azure VPN Gateway는 항상 켜져 있다.  
> AWS VPN을 재배포할 때마다 Outside IP가 바뀌므로 GCP · Azure 쪽에서 `terraform apply`로 터널 peer IP를 업데이트해야 한다.

---

## 3. Mode A — Peering (TGW/VPN 없음, 평일 기본)

### 시작

```bash
cd BookFlowAI-Platform
bash scripts/aws/start-day.sh
```

`start-day.sh` 내부 실행 순서:

| 단계 | 스크립트 | 주요 리소스 |
|---|---|---|
| 1 | `ops/base.sh up` | 5 VPC, RDS, Redis, Kinesis, NAT GW, Route53 — Wave 1(6 병렬) + Wave 2(8 병렬) |
| 2 | `ops/peering.sh up` | VPC 간 Peering 5개 + Route Table |
| 3 (병렬) | `ops/eks.sh up` `ops/ecs.sh up` `ops/publisher.sh up` `ops/etl.sh up` | EKS MSA Pod, ECS POS 시뮬, ALB+ASG, Lambda+Glue+SF |
| 4 | `ops/seed.sh up` | Parquet → RDS COPY, 11 pod role 생성 |
| 5 | `bookflow.py task eks-addons` | ALTER ROLE + 7 pod rollout restart |

### 종료

```bash
bash scripts/aws/stop-day.sh
```

---

## 4. Mode B — TGW/VPN (Cross-Cloud 연동)

### 전체 구조

```
[항상 켜져 있음]              [매일 재배포]
GCP HA VPN Gateway  ←──────→  AWS Transit Gateway
  34.157.64.22                  (새 Outside IP 매번 할당)
  (Interface 0)
  35.220.56.212
  (Interface 1)

Azure VPN Gateway   ←──────→  AWS Transit Gateway
  <Azure PIP>                   (새 Outside IP 매번 할당)
```

AWS VPN이 재배포될 때마다 Outside IP가 바뀐다.  
→ GCP · Azure는 `terraform apply`로 peer IP만 교체 (Gateway 자체는 유지).

---

### 4-1. 최초 설정 (GCP terraform state 없을 때)

GCP 쪽에 기존 리소스는 있지만 terraform state 파일이 없는 경우, import 먼저 수행한다.

#### GCP 현재 리소스 확인

```bash
# 터널 현황
gcloud compute vpn-tunnels list \
  --project=project-8ab6bf05-54d2-4f5d-b8d \
  --format="table(name,peerIp,status)"

# External Gateway 확인
gcloud compute external-vpn-gateways list \
  --project=project-8ab6bf05-54d2-4f5d-b8d
```

현재 확인된 상태:

| 리소스 | 이름 | peerIp (구 AWS IP) | 상태 |
|---|---|---|---|
| HA VPN Gateway | `bookflow-aws-ha-vpn` | — | Interface 0: `34.157.64.22` |
| tunnel0 | `bookflow-aws-tunnel-tunnel0` | `43.206.42.66` | NO_INCOMING_PACKETS |
| tunnel1 | `bookflow-aws-tunnel-tunnel1` | `57.181.230.255` | NO_INCOMING_PACKETS |

#### GCP terraform import (state 없을 때 1회만 실행)

```bash
cd infra/gcp/20-network-daily
terraform init

# HA VPN Gateway
terraform import google_compute_ha_vpn_gateway.bookflow_aws_ha_vpn \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/vpnGateways/bookflow-aws-ha-vpn

# External Gateway
terraform import google_compute_external_vpn_gateway.aws_tgw \
  projects/project-8ab6bf05-54d2-4f5d-b8d/global/externalVpnGateways/bookflow-aws-tgw-external-gw

# VPN 터널 2개
terraform import 'google_compute_vpn_tunnel.aws_tunnels["tunnel0"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/vpnTunnels/bookflow-aws-tunnel-tunnel0

terraform import 'google_compute_vpn_tunnel.aws_tunnels["tunnel1"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/vpnTunnels/bookflow-aws-tunnel-tunnel1

# Router interface 2개
terraform import 'google_compute_router_interface.aws_interfaces["tunnel0"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/routers/bookflow-aws-cr/bookflow-aws-if-tunnel0

terraform import 'google_compute_router_interface.aws_interfaces["tunnel1"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/routers/bookflow-aws-cr/bookflow-aws-if-tunnel1

# BGP peer 2개
terraform import 'google_compute_router_peer.aws_peers["tunnel0"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/routers/bookflow-aws-cr/bookflow-aws-bgp-tunnel0

terraform import 'google_compute_router_peer.aws_peers["tunnel1"]' \
  projects/project-8ab6bf05-54d2-4f5d-b8d/regions/asia-northeast1/routers/bookflow-aws-cr/bookflow-aws-bgp-tunnel1
```

> import 이후에는 state가 생성되므로 이후 배포는 [4-2 일일 흐름](#4-2-일일-aws-재배포-흐름)을 따른다.

---

### 4-2. 일일 AWS 재배포 흐름

#### STEP 1 — AWS 기본 인프라 배포

```bash
bash scripts/aws/start-day.sh
```

완료되면 base + peering + 4 서비스 + seed가 올라온 상태.

#### STEP 2 — Peering → TGW 전환

GCP/Azure IP 환경변수를 설정하고 전환한다.

```bash
export BOOKFLOW_GCP_VPN_GW_IP="34.157.64.22"       # GCP HA VPN Interface 0 (고정값)
export BOOKFLOW_GCP_VPN_PSK="<gcp-shared-secret>"   # .env.local에 저장해 두면 생략 가능
export BOOKFLOW_AZURE_VPN_GW_IP="<Azure PIP>"        # Azure 팀 제공 (고정 PIP)
export BOOKFLOW_AZURE_VPN_PSK="<azure-shared-secret>"

bash scripts/aws/ops/network-mode.sh tgw
```

`network-mode.sh tgw` 내부 동작 (`peering.sh down` + `cross-cloud.sh up`):

| 단계 | CloudFormation 스택 | 내용 |
|---|---|---|
| 1 | `bookflow-10-customer-gateway` | Azure · GCP Customer Gateway 등록 |
| 2 | `bookflow-60-tgw` | Transit Gateway 생성 (ASN 64512) |
| 3 (병렬) | `bookflow-60-tgw-vpc-routes` | VPC → TGW 첨부, GCP CIDR 라우팅 |
| 3 (병렬) | `bookflow-60-vpn-site-to-site` | Azure VPN · GCP VPN 연결 생성 |
| 4 | Python 인라인 | VPN Attachment → TGW RT association + BGP propagation |
| 5 | 자동 출력 | 새 Tunnel Outside IP 목록 |

#### STEP 3 — GCP terraform.tfvars 생성

```bash
bash scripts/aws/ops/gcp-vpn-info.sh
```

스크립트가 Inside CIDR에서 BGP IP를 자동 계산하여 출력한다.  
출력된 `terraform.tfvars` 블록을 `infra/gcp/20-network-daily/terraform.tfvars`에 저장한다.

출력 예시:

```
Tunnel 0 Outside IP  : 43.206.42.xx
Tunnel 0 Inside CIDR : 169.254.213.136/30  (AWS=169.254.213.137  GCP=169.254.213.138)
Tunnel 1 Outside IP  : 57.181.230.xx
Tunnel 1 Inside CIDR : 169.254.100.72/30   (AWS=169.254.100.73   GCP=169.254.100.74)
PSK : xxxxxxxxxxxx

════════════════════════════
  infra/gcp/20-network-daily/terraform.tfvars
════════════════════════════
aws_peer_ips      = ["43.206.42.xx", "57.181.230.xx"]
aws_tgw_bgp_asn   = 64512
vpn_shared_secret = "xxxxxxxxxxxx"
bgp_sessions = {
  tunnel0 = { ... }
  tunnel1 = { ... }
}
```

#### STEP 4 — GCP terraform 업데이트

```bash
cd infra/gcp/20-network-daily
terraform apply -auto-approve
```

terraform plan 결과:
- `google_compute_ha_vpn_gateway` — **변경 없음** (Gateway IP 34.157.64.22 유지)
- `google_compute_external_vpn_gateway` — peer IP 업데이트 → 터널 강제 재생성 트리거
- `google_compute_vpn_tunnel` × 2 — 재생성 (새 AWS Outside IP + PSK 반영)
- `google_compute_router_interface / peer` × 2 — 터널 재생성에 따라 업데이트

> GCP HA VPN Gateway(34.157.64.22)는 항상 보존된다.  
> 터널만 재생성되므로 약 2-3분 소요.

#### STEP 5 — 연결 확인

```bash
# AWS 측 터널 상태
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-gcp" \
  --query "VpnConnections[0].VgwTelemetry[*].{IP:OutsideIpAddress,Status:Status,BGP:AcceptedRouteCount}" \
  --profile bookflow-deploy --region ap-northeast-1

# GCP 측 터널 상태
gcloud compute vpn-tunnels list \
  --project=project-8ab6bf05-54d2-4f5d-b8d \
  --format="table(name,peerIp,status,detailedStatus)"
```

정상: `ESTABLISHED` (BGP 수렴까지 2-3분 소요).

---

### 4-3. 종료 및 원복

```bash
# TGW/VPN → Peering 원복 (AWS만)
bash scripts/aws/ops/network-mode.sh peering

# AWS 전체 destroy
bash scripts/aws/stop-day.sh
```

> GCP · Azure VPN 인프라는 destroy하지 않는다.  
> GCP 터널은 `NO_INCOMING_PACKETS` 상태로 대기 — 다음 날 AWS 재배포 후 STEP 3-4를 실행하면 재연결된다.

---

## 5. 스크립트 구조

```
scripts/aws/
├── README.md
├── start-day.sh                                   # 09:00 전체 deploy (base + peering + 4 서비스 + seed)
├── stop-day.sh                                    # 18:00 전체 destroy
├── bookflow.py                                    # task 단위 CLI
├── requirements.txt
├── config/
│   ├── admin.env                                  # 테스트 계정 (994878981869)
│   ├── deploy.env                                 # 실 운영 계정 (354493396671)
│   └── .env.local                                 # 로컬 전용 오버라이드 (gitignored)
├── lib/
│   └── common.sh                                  # 공통 helper (모든 .sh가 source)
└── ops/
    ├── base.sh          up|down                   # VPC · RDS · Redis · Kinesis · NAT · Route53
    ├── eks.sh           up|down                   # EKS 클러스터 + nodegroup + helm + K8s manifests
    ├── ecs.sh           up|down                   # ECS Fargate (online-sim · offline-sim · inventory-api)
    ├── publisher.sh     up|down                   # ALB-external + publisher-asg
    ├── etl.sh           up|down                   # Lambda SAM → Glue → Step Functions (순차 필수)
    ├── seed.sh          up|down                   # Parquet → RDS COPY (ansible-node 경유)
    ├── cicd.sh          up|down                   # CodePipeline × 4
    ├── peering.sh       up|down                   # VPC Peering 5개 (Mode A)
    ├── cross-cloud.sh   up|down [--with-waf] [--with-client-vpn]
    │                                              # TGW + Customer GW + S2S VPN (Mode B)
    ├── network-mode.sh  peering|tgw               # Mode 전환 편의 래퍼
    │                                              #   peering: cross-cloud down → peering up
    │                                              #   tgw:     peering down → cross-cloud up
    ├── eks-mode.sh      public|private            # EKS endpoint + Client VPN 전환
    ├── tgw-vpn-attach.sh                          # VPN→TGW attachment 수동 처리
    └── gcp-vpn-info.sh                            # AWS VPN 정보 → GCP terraform.tfvars 자동 생성
```

---

## 6. 의존성 흐름

```
[Tier 00 영구 — destroy 금지 · ~$16/월]
  S3 · ECR · Secrets · KMS · IAM · ParamStore · CodeStar Connection
         │
         └──→ base.sh
               ├─ Wave 1 (6 병렬): 5 VPC + ECS cluster
               └─ Wave 2 (8 병렬): 3 VPC Endpoints + ansible-node
                                    + RDS + Redis + Kinesis + NAT GW + Route53
                       │
                       ├── [Mode A] peering.sh     ─┐
                       │                             ├─ 배타적 (동시 사용 불가)
                       └── [Mode B] cross-cloud.sh  ─┘
                               │
                               ├──→ eks.sh        (MSA Pod, cross-VPC 통신 필요)
                               ├──→ ecs.sh        (POS 시뮬, cross-VPC 통신 필요)
                               ├──→ publisher.sh  (cross-VPC 불필요, IGW 직접)
                               ├──→ etl.sh        (Lambda→Glue→SF, cross-VPC 통신 필요)
                               └──→ seed.sh       (Parquet→RDS, cross-VPC 통신 필요)
```

**제약:**
- `peering.sh`와 `cross-cloud.sh`는 동시에 사용 불가 (VPC route 충돌)
- `etl.sh` 내부 순서 고정: Lambda → Glue → Step Functions (SF가 Lambda ARN 주입)
- `seed.sh`는 `eks.sh`보다 먼저 완료되어야 eks-addons resync가 정상 동작

---

## 7. 환경 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `BOOKFLOW_ENV` | `deploy` | `admin` 또는 `deploy` (config/*.env 선택) |
| `AWS_PROFILE` | env 파일 자동 | aws CLI profile |
| `AWS_REGION` | `ap-northeast-1` | 리전 |
| `BOOKFLOW_GCP_VPN_GW_IP` | 필수 (Mode B) | GCP HA VPN Interface 0 공인 IP |
| `BOOKFLOW_GCP_VPN_PSK` | AWS 자동생성 | GCP VPN PSK (.env.local에 고정 권장) |
| `BOOKFLOW_AZURE_VPN_GW_IP` | 선택 (Mode B) | Azure VPN Gateway 공인 IP |
| `BOOKFLOW_AZURE_VPN_PSK` | AWS 자동생성 | Azure VPN PSK (.env.local에 고정 권장) |
| `BOOKFLOW_GCP_VPC_CIDR` | `10.50.0.0/24` | GCP VPC 라우팅 대상 CIDR |
| `GCP_PROJECT_ID` | param store 자동 | ETL Lambda → GCS 연동 |
| `GCS_STAGING_BUCKET` | param store 자동 | ETL staging 버킷 |
| `BOOKFLOW_DOMAIN` | `bookflow.myosoon.store` | 서비스 도메인 — `deploy.env` / `admin.env`에 고정 |
| `BOOKFLOW_ENTRA_CLIENT_ID` | `.env.local` | Entra OIDC 자격증명 (민감정보) |
| `BOOKFLOW_ENTRA_TENANT_ID` | `.env.local` | Entra OIDC 자격증명 (민감정보) |

> PSK를 환경변수로 고정하지 않으면 AWS가 매번 자동생성한다.  
> 자동생성 시 GCP · Azure도 매번 PSK를 업데이트해야 하므로 `.env.local`에 고정값 등록을 권장한다.

---

## 8. VPN 터널 구성 상세

```
AWS Transit Gateway (ASN 64512)
├── VPN Connection: bookflow-vpn-azure  →  Azure VPN Gateway (ASN 65001)
│   ├── Tunnel 1  Inside CIDR: 169.254.21.4/30    IKEv2 · AES256 · SHA2-256 · DH14
│   └── Tunnel 2  Inside CIDR: 169.254.21.8/30    IKEv2 · AES256 · SHA2-256 · DH14
└── VPN Connection: bookflow-vpn-gcp   →  GCP HA VPN (ASN 64514)
    ├── Tunnel 1  Inside CIDR: 169.254.213.136/30  IKEv2 · AES128 · SHA2-256 · DH14
    │             AWS BGP IP: 169.254.213.137  /  GCP BGP IP: 169.254.213.138
    └── Tunnel 2  Inside CIDR: 169.254.100.72/30   IKEv2 · AES128 · SHA2-256 · DH14
                  AWS BGP IP: 169.254.100.73   /  GCP BGP IP: 169.254.100.74
```

GCP HA VPN Gateway 고정 IP:

| Interface | IP |
|---|---|
| 0 (BOOKFLOW_GCP_VPN_GW_IP) | `34.157.64.22` |
| 1 | `35.220.56.212` |

Inside CIDR은 `vpn-site-to-site.yaml`에 하드코딩되어 있으므로 변경하려면 CFN 템플릿을 수정해야 한다.

---

## 9. 비용

| 영역 | 월 비용 (25일 × 9h 기준) |
|---|---|
| Tier 00 영구 (24h × 30일) | ~$16 |
| base (VPC · RDS · Redis · Kinesis · NAT · ansible) | ~$75 |
| eks (cluster + nodegroup) | ~$30 |
| ecs · publisher · etl · seed | ~$15 |
| **Mode A 합계 (peering)** | **~$136/월** |
| cross-cloud 추가 (TGW + VPN 2개) | +~$77 |
| **Mode B 합계 (TGW/VPN)** | **~$213/월** |

---

## 10. 트러블슈팅

### GCP 터널이 NO_INCOMING_PACKETS

**원인**: AWS VPN이 재배포되어 Outside IP가 바뀌었으나 GCP terraform.tfvars 미업데이트.

```bash
# AWS 현재 VPN Outside IP 확인
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-gcp" \
  --query "VpnConnections[0].VgwTelemetry[*].OutsideIpAddress" \
  --profile bookflow-deploy --region ap-northeast-1

# GCP 터널 peerIp 확인
gcloud compute vpn-tunnels list \
  --project=project-8ab6bf05-54d2-4f5d-b8d \
  --format="table(name,peerIp,status)"
```

IP가 다르면 `gcp-vpn-info.sh` → tfvars 업데이트 → `terraform apply`.

---

### terraform apply 시 "already exists" 오류

**원인**: terraform state가 없는데 GCP에 기존 리소스가 있음.  
**해결**: [4-1 최초 설정](#4-1-최초-설정-gcp-terraform-state-없을-때)의 `terraform import` 수행.

---

### `bookflow-99-lambdas` 30분+ DELETE_IN_PROGRESS

**원인**: Lambda VPC ENI (ELA Hyperplane) release 대기.  
**해결**: 기다림 (5-15분 자동 release).

```bash
aws ec2 describe-network-interfaces \
  --filters "Name=description,Values=AWS Lambda VPC ENI*" \
  --profile bookflow-deploy --region ap-northeast-1
```

---

### `publisher-asg` SG 삭제 실패

**원인**: CodeDeploy Blue-Green ASG(`CodeDeploy_*`)가 EC2 점유.  
**해결**: `publisher.sh down` 내부에 force-delete 포함, 자동 처리.

---

### K8s Secret이 placeholder로 리셋

**원인**: buildspec `*.yaml` glob이 `secret.example.yaml`도 매칭.  
**해결**: 이미 fix됨 (`eks-pods/buildspec.yml`).

---

### auth-pod DB pool unavailable

**원인**: RDS pod role password가 placeholder.

```bash
python scripts/aws/bookflow.py task eks-addons
```

---

## 11. 단일 스택 디버깅

```bash
# task 단위
python scripts/aws/bookflow.py task eks-addons
python scripts/aws/bookflow.py task msa-pods
python scripts/aws/bookflow.py task lambdas
python scripts/aws/bookflow.py task rds-seed
python scripts/aws/bookflow.py task glue
python scripts/aws/bookflow.py task lambdas --down

# 사용 가능한 task 목록
python scripts/aws/bookflow.py task --help

# 단일 CFN 스택
aws cloudformation deploy \
  --stack-name bookflow-40-eks-nodegroup \
  --template-file infra/aws/40-compute-runtime/eks-nodegroup.yaml \
  --profile bookflow-deploy --region ap-northeast-1
```

---

## 12. 로그 · 상태 파일

```
logs/
├── 2026-05-18_start-day_up.log
├── 2026-05-18_base_up.log
├── 2026-05-18_cross-cloud_up.log
└── ...

~/.bookflow/state.json
{
  "base": "up",
  "peering": "down",
  "cross-cloud": "up",
  "network-mode": "tgw",
  "last-start-day": "2026-05-18T09:00:00",
  "last-start-elapsed": 1380
}
```

---

## 13. FAQ

**Q. GCP · Azure VPN은 언제 destroy하나?**  
원칙적으로 destroy하지 않는다. GCP `infra/gcp/20-network-daily/`와 Azure VPN은 상시 유지. AWS VPN(`bookflow-60-vpn-site-to-site`)만 매일 재생성.

**Q. PSK를 매번 다르게 해도 되나?**  
가능하지만 GCP · Azure도 매번 PSK를 업데이트해야 한다. `.env.local`에 고정값을 등록해 두는 것을 권장.

**Q. GCP terraform state를 잃어버렸을 때?**  
[4-1 최초 설정](#4-1-최초-설정-gcp-terraform-state-없을-때) 참고 — terraform import 후 apply.

**Q. Mode B에서 GCP만 연결하고 Azure는 나중에?**  
`BOOKFLOW_AZURE_VPN_GW_IP` 미설정 시 Azure CGW · VPN 연결이 생략된다. 나중에 Azure IP 설정 후 `cross-cloud.sh up`을 재실행하면 추가된다 (idempotent).

**Q. 부분만 재배포하고 싶음?**

```bash
bash scripts/aws/ops/<svc>.sh down
bash scripts/aws/ops/<svc>.sh up
```

**Q. fail 후 재시도 안전?**  
모든 .sh가 idempotent. 그냥 다시 실행.

**Q. Tier 00 destroy?**  
S3 시드 CSV · ECR 이미지 · Secrets 전부 삭제됨. 강제 시:

```bash
python scripts/aws/bookflow.py phase0 --down
```

---

**작성**: 2026-05-18 · 영헌  
**프로젝트**: BookFlow V6.2 · MSA EKS + ETL + Multi-Cloud (AWS · GCP · Azure)
