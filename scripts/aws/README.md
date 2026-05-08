# BookFlow Operations Scripts

> 매일 09:00 deploy / 18:00 destroy 자동화. AWS 메인 인프라 + Pod + 시드 + cross-cloud 일괄 관리.

---

## 🚀 Quickstart (5분)

### 1. 환경 설정 (1회)

```bash
# AWS profile (이미 있으면 skip)
aws configure --profile bookflow-deploy
aws configure --profile bookflow-admin   # 테스트용

# Python 의존성
pip install boto3

# Tool 설치 확인
aws --version          # >= 2.x
kubectl version --client
helm version
jq --version
```

### 2. 평일 시연 (가장 흔한 사례)

```bash
cd BookFlowAI-Platform

# 09:00 출근 — 모든 서비스 deploy (~20-25분)
./scripts/start-day.sh

# 작업 (브라우저로 https://bookflow.duckdns.org)

# 18:00 퇴근 — 전체 destroy (~15-20분)
./scripts/stop-day.sh
```

**결과**:
- start-day.sh: base → peering → eks/ecs/publisher/etl 4 서비스 병렬 → seed
- stop-day.sh: 4 서비스 병렬 down → peering/cross-cloud → base
- Tier 00 (영구 자원 · S3·ECR·Secrets·KMS·IAM 등) 만 잔존

### 3. 발표일 (Phase 4 · cross-cloud + private EKS)

```bash
./scripts/start-day.sh                       # 1단계: 평일과 동일
./scripts/ops/network-mode.sh tgw            # 2단계: peering → TGW + S2S VPN

# 출력된 TGW VPN tunnel IP 를 민지(Azure)/우혁(GCP) 에게 전달
# 예: Azure 담당자에게 "TGW Tunnel 1: 52.194.91.158" 보내면
#     민지가 본인 IaC 로 deploy

./scripts/ops/eks-mode.sh private            # 3단계: EKS private + client-vpn
# AWS Console → Client VPN endpoints → bookflow → Download client config (.ovpn)
# .ovpn 다운로드 후 시연 시 VPN 접속
```

---

## 📁 13 스크립트 구조

```
scripts/
├── README.md                                  # ← 이 파일
├── start-day.sh                               # 09:00 전체 deploy
├── stop-day.sh                                # 18:00 전체 destroy
├── lib/
│   └── common.sh                              # 공통 helper (모든 .sh 가 source)
├── config/
│   ├── admin.env                              # admin 계정 (994878981869 · 테스트용)
│   └── deploy.env                             # deploy 계정 (354493396671 · 실 운영)
└── ops/                                       # 부분 deploy 시 직접 호출
    ├── base.sh        up|down                 # 모든 서비스 prereq
    ├── eks.sh         up|down                 # MSA Pod 메인 (auth-pod · dashboard 등)
    ├── ecs.sh         up|down                 # POS sim (online · offline · inventory-api)
    ├── publisher.sh   up|down                 # 출판사 API (publisher-asg + alb-external)
    ├── etl.sh         up|down                 # Lambda + Glue + Step Functions
    ├── seed.sh        up|down                 # parquet → RDS COPY
    ├── cicd.sh        up|down                 # CodePipeline × 4
    ├── peering.sh     up|down                 # VPC peering (Phase 1-2 · 무과금)
    ├── cross-cloud.sh up|down [--with-waf] [--with-client-vpn]
    │                                          # NAT 외 cross-cloud (TGW + VPN)
    ├── network-mode.sh peering|tgw            # 네트워크 모드 전환
    └── eks-mode.sh    public|private          # EKS endpoint + client-vpn 전환
```

---

## 🎯 시나리오별 가이드

### 시나리오 A: 평일 일반 시연

```bash
./scripts/start-day.sh
# 끝. 브라우저로 https://bookflow.duckdns.org 접속
```

### 시나리오 B: 발표일 (cross-cloud + private)

```bash
# 1. 발표 1시간 전 — 평일 모드로 deploy
./scripts/start-day.sh

# 2. Azure/GCP 담당자에게 IP 전달 받기 (각 PIP 영구 자원이라 한 번만)
export BOOKFLOW_AZURE_VPN_GW_IP=135.149.169.236
export BOOKFLOW_GCP_VPN_GW_IP=34.123.45.67   # 우혁이 알려준 IP

# 3. peering → TGW + S2S VPN 전환
./scripts/ops/network-mode.sh tgw

# 4. 출력된 AWS Tunnel IP 를 민지/우혁에게 전달 → 그들이 본인 IaC deploy
# 예: "TGW Tunnel 1: 52.194.91.158, BGP IP: 169.254.21.5"

# 5. EKS private + client-vpn (보안)
./scripts/ops/eks-mode.sh private
# AWS Console 에서 ovpn 다운로드 → 시연 PC 에서 VPN 접속

# 6. 발표 끝 후 원복
./scripts/ops/eks-mode.sh public
./scripts/ops/network-mode.sh peering

# 7. 일반 destroy
./scripts/stop-day.sh
```

### 시나리오 C: 부분 deploy (코드 수정 후 빠른 검증)

```bash
# eks 만 다시 띄우고 싶음 (Pod 코드 수정 후)
./scripts/ops/eks.sh down
./scripts/ops/eks.sh up
# RDS · VPC 그대로 → 5-10분 만에 재 deploy

# seed 만 다시 (시드 데이터 갱신 후)
./scripts/ops/seed.sh up
```

### 시나리오 D: 긴급 fix (특정 stack 만)

```bash
# EKS 의 nodegroup 만 재 deploy
py scripts/aws/bookflow.py task msa-pods

# 또는 단일 CFN stack
aws cloudformation deploy --stack-name bookflow-40-eks-nodegroup \
  --template-file infra/aws/40-compute-runtime/eks-nodegroup.yaml ...
```

### 시나리오 E: admin 계정에서 테스트 후 deploy 로

```bash
# admin 에서 먼저 테스트
BOOKFLOW_ENV=admin ./scripts/start-day.sh
# 작동 확인 후
BOOKFLOW_ENV=admin ./scripts/stop-day.sh

# deploy 에서 검증
BOOKFLOW_ENV=deploy ./scripts/start-day.sh   # (default 라 BOOKFLOW_ENV 생략 OK)
```

---

## ⚙️ 환경 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `BOOKFLOW_ENV` | `deploy` | `admin` 또는 `deploy` (config/*.env 선택) |
| `AWS_PROFILE` | env 파일에서 자동 | aws CLI profile |
| `AWS_REGION` | `ap-northeast-1` | 리전 |
| `BOOKFLOW_AZURE_VPN_GW_IP` | (미입력) | cross-cloud.sh 시 Azure PIP IP |
| `BOOKFLOW_GCP_VPN_GW_IP` | (미입력) | cross-cloud.sh 시 GCP PIP IP |
| `BOOKFLOW_DUCKDNS_TOKEN` | `.env.local` | DuckDNS API token (auth-pod HTTPS) |
| `BOOKFLOW_ENTRA_CLIENT_ID/TENANT_ID` | `.env.local` | Entra OIDC (자동) |

`.env.local` 위치: `scripts/aws/config/.env.local` (gitignored).

---

## 📊 의존성 흐름

```
[Tier 00 영구 · destroy X · 월 ~$16]
  S3 · ECR · Secrets · KMS · IAM · ParamStore · CodeStar Connection
        │
        ├──→ [base.sh]
        │     ├─ Wave 1 (6 병렬): 5 VPC + ecs-cluster
        │     └─ Wave 2 (8 병렬): 3 endpoints + ansible-node + rds + redis + kinesis + nat + route53
        │           │
        │           ├──→ [peering.sh] OR [cross-cloud.sh]   ← 둘 중 하나 (cross-VPC 통신 필수)
        │           │
        │           ├──→ [eks.sh]        (MSA Pod 메인)
        │           ├──→ [ecs.sh]        (POS sim)
        │           ├──→ [publisher.sh]  (출판사 API)
        │           ├──→ [etl.sh]        (Lambda + Glue)
        │           └──→ [seed.sh]       (parquet → RDS)
        │
        └──→ [cicd.sh]   (Tier 00 만 의존 · 언제든)
```

**중요**:
- `peering.sh` 와 `cross-cloud.sh` 는 **배타적** (둘 중 하나만 사용)
- `eks.sh` · `ecs.sh` · `etl.sh` · `seed.sh` 는 cross-VPC 통신 필요 → **peering 또는 cross-cloud 둘 중 하나 필수**
- `publisher.sh` 만 cross-VPC 불필요 (vpc-egress 자체로 IGW outbound)

---

## ⚡ 병렬 처리

| .sh | 안 병렬 | start-day 흐름 |
|---|---|---|
| base.sh | Wave 1 (6) + Wave 2 (8) | 1단계 |
| peering.sh | 5 peering 병렬 | 2단계 |
| eks · ecs · publisher · etl | 4 .sh 병렬 | 3단계 |
| seed.sh | 단독 | 4단계 |

**시간 단축**: sequential 45분 → 병렬화 **20-25분** (절반 이하)

---

## 💰 비용 (월 25일 × 9h 기준)

| 영역 | 비용 |
|---|---|
| Tier 00 영구 (24h × 30일) | ~$16 |
| base (VPC · RDS · Redis · Kinesis · NAT · ansible) | ~$75 |
| eks (cluster + nodegroup) | ~$30 |
| ecs · publisher · etl · seed | ~$15 |
| peering (default) | $0 |
| cross-cloud (TGW + VPN + ALB) | ~$77 (발표일만) |
| **평일 default** | **~$136/월** |
| **발표일 추가** | **~$77** |

---

## 🛡️ 안전장치 (Idempotent + Auto-retry)

각 .sh 는 **재실행 안전**:
- CFN `update-stack` (기존 있으면 update · 없으면 create)
- helm `upgrade --install`
- kubectl `apply`
- 중간 fail 후 재실행 → 끝까지

### Stop-day 의 stuck 처리
- 모든 stack `delete-stack` 동시 trigger
- **30초 polling 자동 재 trigger** (CREATE_COMPLETE / DELETE_FAILED 자동 감지)
- CFN export dependency cascade 자동 (lambda → kinesis · vpc 등)
- Stuck namespace finalizer 강제 patch (cert-manager · ingress-nginx)
- ELA Hyperplane ENI 자동 release 대기 (Lambda 함수 삭제 후 5-15분)
- CodeDeploy ASG 강제 삭제 (publisher-asg 정리 시 · 오늘 본 패턴)

---

## 🔧 트러블슈팅

### 증상: `bookflow-99-lambdas` 30분+ DELETE_IN_PROGRESS
**원인**: ELA Hyperplane ENI release 대기 (Lambda VPC ENI 자동)
**해결**: 그냥 기다림 (5-15분 자동 release · AWS 한계)

```bash
# 상태 확인
aws ec2 describe-network-interfaces \
  --filters "Name=description,Values=AWS Lambda VPC ENI*"
```

### 증상: `publisher-asg` SG 삭제 못함
**원인**: CodeDeploy Blue-Green 이 만든 별도 ASG (`CodeDeploy_*`) 가 EC2 hold
**해결**: 자동 처리됨 (publisher.sh down 안에 force-delete 포함)

```bash
# 수동 정리 시
aws autoscaling delete-auto-scaling-group \
  --auto-scaling-group-name "CodeDeploy_bookflow-publisher-bg_*" \
  --force-delete
```

### 증상: 일부 stack 이 `CREATE_COMPLETE` 인 채 안 지워짐
**원인**: export dependency cascade 대기 (예: lambdas → kinesis)
**해결**: 자동 재 trigger (30초 polling) — 기다림

### 증상: K8s Secret 이 placeholder 로 reset (auth-pod 인증 실패)
**원인**: cicd buildspec 의 `*.yaml` glob 이 `secret.example.yaml` 도 매칭
**해결**: buildspec 에 `case "$f" in *.example.yaml) continue;;` (이미 fix · `/eks-pods/buildspec.yml`)

### 증상: auth-pod token exchange failed
**원인**: K8s Secret 의 `AUTH_ENTRA_CLIENT_SECRET` 이 placeholder
**해결**: `eks_addons.py _sync_pod_secrets()` 자동 sync (eks.sh up 시 자동 호출)

```bash
# 수동 sync 시
py -c "from scripts.aws.tasks.eks_addons import _sync_pod_secrets, _ensure_kubeconfig; _ensure_kubeconfig(); _sync_pod_secrets()"
```

### 증상: auth-pod DB pool unavailable
**원인**: RDS auth_pod role password 가 `CHANGE_ME_AUTH` placeholder
**해결**: `_alter_rds_pod_roles()` 자동 ALTER ROLE (eks.sh up 시 자동)

### 증상: ansible-node → RDS:5432 timeout
**원인**: TGW Ansible attachment 누락 (peering destroy 후)
**해결**: TGW yaml 에 Ansible attachment 추가 (`infra/aws/60-network-cross-cloud/tgw.yaml`)

---

## 📋 단일 stack 명령 (긴급 디버깅)

기존 `bookflow.py` 그대로 작동:

```bash
# task 단위 (기존)
py scripts/aws/bookflow.py task data
py scripts/aws/bookflow.py task msa-pods
py scripts/aws/bookflow.py task lambdas
py scripts/aws/bookflow.py task data --down

# 사용 가능한 task list
py scripts/aws/bookflow.py task --help
# → data · msa-pods · eks-addons · mocks · etl-streaming · publisher
#    auth-pod · forecast · lambdas · glue · client-vpn · rds-seed
```

---

## 📂 로그 + 상태 파일

```
logs/                                          # 자동 생성
├── 2026-05-06_start-day_up.log
├── 2026-05-06_base_up.log
├── 2026-05-06_eks_up.log
└── ...

~/.bookflow/state.json                         # deploy 상태 추적
{
  "base": "up",
  "eks": "up",
  "network-mode": "peering",
  "last-start-day": "2026-05-06T09:00:00",
  "last-start-elapsed": 1380
}
```

---

## ❓ FAQ

### Q1. 처음 사용할 때 무엇부터?
1. `aws configure --profile bookflow-deploy` (또는 admin)
2. `pip install boto3`
3. `./scripts/start-day.sh`

### Q2. 부분 만 다시 띄우고 싶음
```bash
./scripts/ops/<svc>.sh down
./scripts/ops/<svc>.sh up
```

### Q3. cross-cloud 안 쓸 거면?
`start-day.sh` 만 실행하면 됨 (default 가 peering 모드 · cross-cloud 안 띄움).

### Q4. Tier 00 (영구 자원) destroy 가능?
가능하지만 권장 X. S3 의 시드 CSV · ECR Pod images · Secrets 다 사라짐. 강제 시:
```bash
py scripts/aws/bookflow.py phase0 --down
```

### Q5. admin/deploy 계정 동시?
한 셸 = 한 계정. 동시 작업 시 셸 두 개:
```bash
# 셸 1
BOOKFLOW_ENV=admin ./scripts/start-day.sh
# 셸 2
BOOKFLOW_ENV=deploy ./scripts/start-day.sh
```

### Q6. fail 후 재시도 안전?
모든 .sh idempotent. 그냥 다시 실행:
```bash
./scripts/start-day.sh   # 이미 deploy 된 stack 은 자동 skip
```

### Q7. 매일 destroy 안 하면 비용?
24h × 30일 = 평일 default `~$136 → ~$365/월`. destroy 권장.

### Q8. dry-run 모드?
미구현 (TODO). 현재 실 deploy 만. 사전 검증 필요 시:
```bash
aws cloudformation list-stacks ...  # 직접 CFN 상태 확인
```

---

## 📞 도움 안 되면

1. `logs/<DATE>_<svc>_up.log` 확인 (실 출력 보존)
2. AWS Console → CloudFormation → Events 탭 (각 stack 별 reason)
3. `kubectl logs -n bookflow <pod>` (Pod 에러)
4. **트러블슈팅 섹션 위 5 사례** 다 점검

---

**작성**: 2026-05-06 · 영헌 (YHK0427)
**프로젝트**: BookFlow V6.2 · MSA EKS + ETL + 멀티클라우드
