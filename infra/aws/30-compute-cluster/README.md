# Tier 30 · Compute Cluster (⏰ 매일)

## 이 Tier의 역할

**EKS Control Plane · ECS Cluster · Ansible Control Node** — 매일 아침 올라가는 Compute 기반. 실제 Pod/Task/Instance 배치는 Tier 40.

## Stack (5개)

| YAML | 내용 | 위치 |
|---|---|---|
| `eks-cluster.yaml` | EKS Control Plane (Kubernetes 1.30) + OIDC + Cluster Role + Cluster SG + CW Log Group + Access Entry | BookFlow AI VPC Private |
| `eks-alb-controller-irsa.yaml` | ALB Controller IRSA Role (IAM 만 · Pod 본체는 CI/CD 가 K8s yaml 로 apply) | IAM |
| `eks-eso-irsa.yaml` | External Secrets Operator IRSA Role (auth-pod 가 Secrets Manager → K8s Secret sync) | IAM |
| `ecs-cluster.yaml` | ECS Cluster + Fargate CP + Container Insights + CW Log Group | 클러스터 레벨 |
| `ansible-node.yaml` | **Ansible 전용** Ubuntu 24 t3.nano EC2 + IAM Role + SG + cloud-init | Ansible VPC **Public** (SG ingress 차단) |

## 🔑 관심사 분리 (중요)

**세 가지 작업자가 서로 건드리지 않음**:

| 작업자 | 담당 | 건드리지 않는 것 |
|---|---|---|
| **CFN (이 Tier)** | EKS/ECS 인프라 · IAM · IRSA · Ansible Node 인프라 | K8s 리소스 · Pod · Ingress · ALB 자체 |
| **CI/CD (미래)** | K8s manifests 전체 (ALB Controller · Pod · Service · Ingress) · ECS 이미지 · Publisher 배포 | IAM/IRSA Role (CFN 담당) · 인프라 |
| **Ansible Node** | RDS 시드 · 스키마 migration · Glue job 관리 · Secrets ops | **K8s 관련 일체 무관** |

## Internal ALB 생성 흐름 (V6.2 기반)

```
Tier 30 CFN: EKS Cluster + OIDC + IRSA Role 준비
        ↓
CI/CD 아침에 K8s yaml apply (매일)
    ├── ALB Controller Deployment (IRSA Role annotation)
    ├── CRD (IngressClass, TargetGroupBinding)
    └── Pod + Service + Ingress
            ↓
ALB Controller 가 Ingress 감지
        ↓
Internal ALB 자동 생성 (BookFlow AI VPC Private)
```

**이 Tier 에서 CFN 이 담당**:
- IRSA Role (`eks-alb-controller-irsa.yaml`)
- OIDC Provider (`eks-cluster.yaml`)
- EKS Access Entry → CI/CD Role (parameter · 추후 주입)

**이 Tier 에서 CFN 이 담당하지 않음**:
- ALB Controller Pod/Deployment/SA → CI/CD
- Ingress resource → CI/CD
- 실제 ALB 오브젝트 → ALB Controller 자동 생성

## Ansible Node 역할 (K8s 와 완전 무관)

**하는 일**:
- RDS 시드 주입 · 스키마 migration (`psql` + Ansible playbook)
- Glue job 관리 (cicd/ansible/ · GHA → OIDC → SSM → CN → Ansible)
- Secrets Manager 읽기 · 값 주입 ops

**하지 않는 일**:
- **kubectl 없음** · EKS 와 무관
- **helm 없음** · K8s manifests 안 만짐
- **ALB Controller 와 무관**

**보안 모델 (Public Subnet + SG ingress block)**:
- Subnet: Ansible VPC **Public** (10.4.0.0/24 · IGW 직결 · 인터넷 outbound 가능)
- 이유: cloud-init 가 `apt`, `pip`, `git clone` 등 외부 인터넷 필요. Ansible 평상시 ops 도 git pull · pip install 등 외부 호출 필요.
- **SG 가 모든 ingress 차단** (egress only) → 보안적으로 Private 과 동일
- 접속: **SSM Session Manager** 만 (IAM 인증 · Public IP 무관)
- SSH/HTTP/HTTPS 등 어떤 inbound 포트도 안 열림

**접속 방법**: SSM Session Manager (SSH 없음)
```powershell
$id = aws cloudformation describe-stacks --stack-name bookflow-30-ansible-node --query 'Stacks[0].Outputs[?OutputKey==`InstanceId`].OutputValue' --output text
aws ssm start-session --target $id
```

**cloud-init 설치 범위** (Ubuntu 24):
- `apt`: git, python3-pip, curl, unzip, jq, **postgresql-client** (psql)
- `pip`: ansible, boto3, botocore, **psycopg2-binary**, requests
- AWS CLI v2
- repo clone → `/opt/bookflow`

**설치 안 하는 것**: kubectl, helm, eksctl (K8s 무관이므로)

## Parameter: `CiCdRoleArn` (eks-cluster.yaml)

CI/CD Tier 에서 CodeBuild/CodePipeline Role 생성 후 주입:

```powershell
Deploy-Stack -Tier "30" -Name "eks-cluster" -Template "..." `
  -Parameters @{ CiCdRoleArn = "arn:aws:iam::xxxxx:role/bookflow-codebuild-role" }
```

→ EKS Access Entry 에 cluster-admin 권한 자동 부여 · CodeBuild 에서 `kubectl apply -f` 가능.

## 배포 순서

```
1. eks-cluster              ← 약 10-15 분 소요
2. eks-alb-controller-irsa  ← OIDC Import 필요
3. ecs-cluster              ← 독립 · 병렬 가능
4. ansible-node             ← Ansible VPC 전용 · 독립
```

## 주요 Import

### eks-cluster.yaml
- `bookflow-vpc-bookflow-ai-id`
- `bookflow-subnet-bookflow-ai-private-az1/az2`
- `bookflow-kms-eks-arn`

### eks-alb-controller-irsa.yaml
- `bookflow-eks-oidc-provider-arn`
- `bookflow-eks-oidc-issuer-url`

### ansible-node.yaml
- `bookflow-vpc-ansible-id`
- `bookflow-subnet-ansible-public-az1` (Public · 인터넷 outbound · SG 로 ingress 차단)
- `bookflow-s3-glue-scripts-name`
- (Secrets Manager: `bookflow/*` 전체 read)

## 검증

```powershell
# lint
cfn-lint infra\aws\30-compute-cluster\*.yaml

# EKS 상태
aws eks describe-cluster --name bookflow-eks --query 'cluster.{status:status,version:version,endpoint:endpoint}'

# OIDC Provider
aws iam list-open-id-connect-providers

# ECS Cluster
aws ecs describe-clusters --clusters bookflow-ecs

# Ansible Node (SSM 접속 확인 · kubectl 없음 확인)
aws ssm describe-instance-information --filters "Key=tag:Name,Values=bookflow-ansible-node"
```

## 다음 Tier 와의 관계

- **Tier 40 (compute-runtime)**: `bookflow-eks-cluster-name`, `bookflow-ecs-cluster-name` Import · **EKS Managed Node Group (EC2 기반 · full K8s)** · ECS Service (Fargate) · Publisher ASG 배치
- **Tier 50 (network-traffic)**: External ALB · NAT · WAF (Egress VPC)
- **CI/CD Tier (미래)**: CodeBuild Role 생성 후 `eks-cluster.yaml` parameter 에 주입 · Access Entry 로 kubectl 권한

## 비용 추정 (Tier 30 · 198h × 22d)

| 자원 | 시간당 | 월 비용 |
|---|---|---|
| EKS Control Plane | $0.10 | **$19.80** |
| ECS Cluster | $0 | $0 |
| Ansible Node t3.nano | $0.0052 | $1.03 |
| EBS gp3 16GB | $0.08/GB-월 | $1.28 |
| CloudWatch Logs (7d) | 소량 | ~$1 |

**합계 예상**: ~$23/월 (Tier 30)

## 비고

- EKS 버전 `1.30` (2026-04 기준 지원)
- Ubuntu 24.04 AMI: SSM public parameter 로 region 별 최신 자동 resolve
- `AccessConfig.AuthenticationMode: API` — aws-auth ConfigMap 미사용 · Access Entry 전용
- `BootstrapClusterCreatorAdminPermissions: true` — stack 생성자 IAM User 자동 cluster-admin
- ALB Controller 본체 (Deployment / CRD / SA) 는 **repo 의 `k8s/` 폴더** (추후 작성) · CI/CD 가 매일 apply
- Ansible playbook 은 **repo 의 `ansible/` 폴더** (추후 작성) · SSM Session Manager 로 Ansible Node 접속 후 trigger
