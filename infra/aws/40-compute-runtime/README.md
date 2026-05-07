# Tier 40 · Compute Runtime (⏰ 매일)

## 이 Tier의 역할

**EKS Node Group · EKS Core Addons · ECS Services × 3 · Publisher ASG** — Tier 30 클러스터 위에 실제 Pod/Task/Instance 배치.

## Stack (6개)

| YAML | 내용 | 위치 |
|---|---|---|
| `eks-nodegroup.yaml` | Managed Node Group (EC2 t3.medium × 1 default · MaxSize 2 autoscale · ON_DEMAND · AL2023) + Worker Node IAM Role | BookFlow AI VPC Private |
| `eks-addons.yaml` | vpc-cni · kube-proxy · coredns · aws-ebs-csi-driver (IRSA) · pod-identity-agent | EKS cluster 레벨 |
| `ecs-online-sim.yaml` | Online 판매 시뮬 (Fargate 0.25 vCPU · Kinesis put) | Sales Data VPC Private |
| `ecs-offline-sim.yaml` | Offline 판매 시뮬 (Fargate 0.25 vCPU · Kinesis put) | Sales Data VPC Private |
| `ecs-inventory-api.yaml` | 재고 조회 API (Fargate 0.25 vCPU · port 8080 · External ALB target) | Egress VPC Public |
| `publisher-asg.yaml` | Launch Template (Ubuntu 24 + codedeploy-agent) + ASG **t3.micro × 2** (비용산정 V1 일치 · Blue/Green 위해 최소 2) | Egress VPC Public |

## 🔑 CI/CD 연결 포인트

**모든 image URI · Target Group 은 parameter** — CI/CD 가 update-stack 으로 주입:

| Parameter | Default | CI/CD 가 주입할 곳 |
|---|---|---|
| `ImageTag` (3 ECS Services) | `placeholder` | CodeBuild → ECR push → ECS Service update-stack |
| `TargetGroupArn` (inventory-api · publisher-asg) | `''` | Tier 50 ALB Target Group 생성 후 update-stack |

**ECS Rolling 동작**:
- `DeploymentController: ECS` (rolling)
- `MinimumHealthyPercent: 100` · `MaximumPercent: 200`
- CI/CD 가 `aws ecs update-service --force-new-deployment` 또는 새 Task Definition revision 으로 trigger

**Publisher Blue/Green 동작**:
- Launch Template + ASG (Tier 40)
- CodeDeploy Deployment Group (CI/CD Tier 에서 별도 yaml · `bookflow-publisher-bg` Tag 로 ASG 식별)
- Blue/Green Traffic Swap 은 CodeDeploy 가 ALB Listener Rule 조작

## Tier 30 / Tier 00 / Tier 10 Import 매트릭스

### eks-nodegroup.yaml
- `bookflow-eks-cluster-name` (Tier 30)
- `bookflow-subnet-bookflow-ai-private-az1/az2` (Tier 10)

### eks-addons.yaml
- `bookflow-eks-cluster-name`
- `bookflow-eks-oidc-provider-arn` · `bookflow-eks-oidc-issuer-url` (Tier 30 · IRSA)

### ecs-online-sim · offline-sim
- `bookflow-ecs-cluster-name` · `bookflow-ecs-log-group-name` (Tier 30)
- `bookflow-ecr-registry-uri` (Tier 00)
- `bookflow-vpc-sales-data-id` · `bookflow-subnet-sales-data-private-az1/az2` (Tier 10)
- `bookflow-kinesis-pos-events-arn` · `bookflow-kinesis-pos-events-name` (Tier 20)

### ecs-inventory-api
- `bookflow-ecs-cluster-name` · `bookflow-ecs-log-group-name`
- `bookflow-ecr-registry-uri`
- `bookflow-vpc-egress-id` · `bookflow-subnet-egress-public-az1/az2`
- `bookflow-rds-endpoint` · `bookflow-rds-dbname` · `bookflow-secrets-rds-master-arn`

### publisher-asg
- `bookflow-vpc-egress-id` · `bookflow-subnet-egress-public-az1/az2`
- `bookflow-s3-cp-artifacts-name` (CodeDeploy artifact bucket)

## 배포 순서

```
1. eks-nodegroup     ← EKS Cluster Import · 5-10분 (EC2 + ASG join)
2. eks-addons        ← Cluster + OIDC Import · 노드 join 후
3. ecs-online-sim    ← 독립 (Sales Data VPC)
4. ecs-offline-sim   ← 독립 (Sales Data VPC)
5. ecs-inventory-api ← RDS Import · LoadBalancer parameter (Tier 50 후 주입)
6. publisher-asg     ← S3 cp-artifacts Import · TargetGroup parameter (Tier 50 후 주입)
```

## 검증

```powershell
# lint
cfn-lint infra\aws\40-compute-runtime\*.yaml

# Node Group join 확인
aws eks describe-nodegroup --cluster-name bookflow-eks --nodegroup-name bookflow-eks-ng --query 'nodegroup.{status:status,version:version,instances:scalingConfig}'

# Addons 상태
aws eks list-addons --cluster-name bookflow-eks
aws eks describe-addon --cluster-name bookflow-eks --addon-name vpc-cni

# kubectl 로 노드 확인 (영헌 local · kubeconfig 설정 후)
kubectl get nodes
kubectl get pods -A

# ECS Services 상태
aws ecs list-services --cluster bookflow-ecs
aws ecs describe-services --cluster bookflow-ecs --services online-sim offline-sim inventory-api

# Publisher ASG
aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names bookflow-publisher-asg
```

## CI/CD Flow (V6.2 Slide 20·21·22 기반)

### EKS Pod 배포
```
GitHub push → GHA → CodePipeline → CodeBuild
  ↓
docker build → docker push (ECR)
  ↓
kubectl set image deployment/<pod> <container>=<ECR>:<tag>
  (CodeBuild Role 은 Tier 30 EKS Access Entry 로 cluster-admin)
```

### ECS Service 배포 (online-sim · offline-sim · inventory-api)
```
GitHub push → CodePipeline → CodeBuild → ECR push
  ↓
aws cloudformation deploy --parameter-overrides ImageTag=<new>
  → ECS rolling update (Tier 40 Service update-stack)
```

또는 `aws ecs update-service --force-new-deployment` (Task Definition 재생성 없이).

### Publisher EC2 Blue/Green
```
GitHub push → CodePipeline → CodeBuild → ZIP → S3 cp-artifacts
  ↓
CodeDeploy Application + Deployment Group (CI/CD Tier)
  ↓
Blue/Green Traffic Swap (ALB Listener Rule)
  ↓
codedeploy-agent 가 EC2 에서 appspec.yml 실행 (UserData 에서 미리 install)
```

## 비용 추정 (Tier 40 · 198h × 22d)

| 자원 | 시간당 | 월 비용 |
|---|---|---|
| EKS Node Group t3.medium × 1 (default · MaxSize 2) | $0.047 | $9.30 |
| EBS gp3 20GB × 1 | $0.08/GB-월 | $1.60 |
| ECS Fargate online-sim (0.25v · 0.5GB · 12일 가정) | $0.012 | $1.30 |
| ECS Fargate offline-sim (12일 가정) | $0.012 | $1.30 |
| ECS Fargate inventory-api (12일 가정) | $0.012 | $1.30 |
| Publisher ASG t3.micro × 2 | $0.0136 | $5.40 |
| EBS gp3 16GB × 2 (Publisher) | $0.08/GB-월 | $2.56 |

**합계 예상**: ~$23/월 (Tier 40 · 비용산정 V1 일치)

## 비고

- AMI: EKS worker = Amazon Linux 2023 (`AL2023_x86_64_STANDARD`) · Publisher = Ubuntu 24 (V6.2 일관성)
- ECS NetworkMode: `awsvpc` (Fargate 강제)
- IMDSv2 강제 (`HttpTokens: required` · Publisher Launch Template)
- Image URI 는 placeholder 로 시작 · 첫 CI/CD 배포 시 실제 이미지로 갱신 → ECS rolling 자동
- inventory-api 는 LoadBalancer 미연결 상태로 시작 → Tier 50 ALB 생성 후 update-stack 으로 연결
- Publisher ASG 도 동일 패턴 (TargetGroupARNs parameter)
