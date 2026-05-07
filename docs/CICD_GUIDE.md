# BOOKFLOW · CI/CD 작성 가이드 (V6.2 기반)

> 6 파이프라인 IaC 작성 표준 + 팀 분업.
> 각 파이프라인 = 1 PR (`docs/TEAM_GUIDE.md` 의 PR 룰 따름).

## 참조 문서 (single source of truth)

- `BOOKFLOW/02_아키텍처/V6.2_아키텍처 구성도_1조.pptx`
  - **Slide 22**: CI/CD 파이프라인 - 출판사 EC2 (Blue & Green)
  - **Slide 23**: CI/CD 파이프라인 - Glue ETL 스크립트 (Ansible)
  - **Slide 24**: CI/CD 파이프라인 - RDS 초기화 (GitHub Action + Ansible)
- `BOOKFLOW/09_WBS/V4_BOOKFLOW_WBS.xlsx` (요약 시트만)
  - "전원 = CI/CD" · "먼저끝" 룰
- `BOOKFLOW/10_비용산정/BOOKFLOW_비용산정_V1.xlsx`
  - **CodePipeline × 4** (매일 destroy/create) · CodeBuild EC2/Lambda/Docker 서버 · CodeDeploy
- `BOOKFLOW/01_데이터스키마/BOOKFLOW_Data_Schema_v3.xlsx`
  - **19 tables** (RDS schema · 시드 1000권 + 12지점 등 RDS pipeline 가 적용)

## 6 파이프라인 (4 CodePipeline + 2 GHA)

| # | 이름 | 종류 | Source repo / 브랜치 | 라이프사이클 | 담당 |
|---|------|------|---------------------|-----|------|
| 1 | **eks-pipeline** | CodePipeline | `BookFlowAI-Apps` / `eks-pods` → main | 🟡 필요 시 | **영헌** |
| 2 | **ecs-pipeline** | CodePipeline | `BookFlowAI-Apps` / `ecs-sims` → main | 🟡 필요 시 | **영헌** |
| 3 | **lambda-sam-pipeline** *(Optional)* | CodePipeline | `BookFlowAI-Platform` / `aws` → main | 🟡 필요 시 | TBD (시연 직전) |
| 4 | **publisher-codedeploy** | CodePipeline | `BookFlowAI-Apps` / `publisher` → main | 🟡 필요 시 | **우혁** |
| 5 | **glue-redeploy** | GitHub Actions | `BookFlowAI-Apps` / `glue-jobs` → main | 🔒 영구 | **민지** |
| 6 | **rds-redeploy** | GitHub Actions | `BookFlowAI-Platform` / `aws` → main | 🔒 영구 | **민지** (playbook 작성까지) |

**WBS 큰 스코프 룰**: `전원 = CICD · 먼저끝`. 위 표는 영역 매칭 디폴트 — 진행 상황에 따라 재배분 가능.

## 공통 표준

### 1. 파일 위치
```
infra/aws/00-foundation/
  ├── codestar-connection.yaml   영구 · GitHub OAuth (이미 deploy)
  └── iam.yaml                   영구 · OIDC + GHA 2 role (이미 deploy)

cicd/codepipeline/               # 🟡 필요 시 (코드 push 직전 deploy · 끝나면 destroy)
  ├── eks-pipeline.yaml          0 lines (TODO)
  ├── ecs-pipeline.yaml          0 lines (TODO)
  ├── lambda-sam-pipeline.yaml   0 lines (TODO)
  └── publisher-codedeploy.yaml  0 lines (TODO)

cicd/ansible/                    # 🔒 영구 (Ansible Control Node 배포 후 git pull)
  ├── playbooks/
  │   ├── glue-deploy.yml        TODO (V6.2 Slide 23 흐름)
  │   ├── rds-schema.yml         TODO (V6.2 Slide 24 · postgresql_query · 19 tables)
  │   ├── rds-seed.yml           TODO (Jinja2 · 1000권 + 12지점)
  │   └── rds-grants.yml         TODO (postgresql_privs · bookflow_app)
  ├── roles/
  └── sql/                       *.sql (스키마 v3 기준 변환)

.github/workflows/               # 🔒 영구
  ├── glue-redeploy.yml          stub (TODO)
  └── rds-redeploy.yml           stub (TODO)
```

### 2. CodeStar Connection (이미 영구로 활성화)
```yaml
ConnectionArn: !ImportValue bookflow-codestar-github-connection-arn
```

### 3. GitHub OIDC 두 role (이미 영구로 deploy)
- `bookflow-gha-glue-role` — S3 sync + SSM Run Command
- `bookflow-gha-rds-role` — SSM Run Command (Ansible 호출)

### 4. Artifact bucket (영구)
`s3://bookflow-cp-artifacts-{accountId}/` (Tier 00 · 30일 expiration)

### 5. KMS
CodePipeline artifact 는 `alias/aws/s3` 사용 (학프 비용 절감).

---

## 1. eks-pipeline (영헌)

### 흐름
```
[GitHub Apps eks-pods] → CodeStar webhook → CodePipeline
  → CodeBuild (Docker × 8) → ECR push
  → CodeBuild (kubectl set image) → EKS rolling update
```

### Stages
1. **Source** — CodeStar Connection · `BookFlowAI-Apps` `main` 브랜치 · path filter `eks-pods/**`
2. **Build** — CodeBuild
   - **8개 디렉토리** loop: `auth-pod` `dashboard-svc` `forecast-svc` `decision-svc` `intervention-svc` `inventory-svc` `notification-svc` `publish-watcher`
   - 각 `Dockerfile` 빌드 → ECR `bookflow/{pod}` push (tag = commit SHA)
3. **Deploy** — CodeBuild
   - `aws eks update-kubeconfig`
   - `kubectl set image deployment/{pod}` (rolling)
   - `kubectl rollout status` 검증

### 작성 파일
| 파일 | 위치 |
|------|------|
| `eks-pipeline.yaml` | `cicd/codepipeline/` |
| `buildspec.yml` | `BookFlowAI-Apps/eks-pods/` |

### IAM
- `codestar-connections:UseConnection`
- `s3:GetObject/PutObject` (artifacts)
- `ecr:GetAuthorizationToken` · `ecr:BatchCheckLayerAvailability` · `ecr:Put*`
- `eks:DescribeCluster` · `sts:AssumeRole` (kubectl access via aws-auth ConfigMap)

### 검증
```bash
aws codepipeline get-pipeline-state --name bookflow-cp-eks --query 'stageStates[].latestExecution.status'
kubectl rollout status deployment/auth-pod -n bookflow
```

---

## 2. ecs-pipeline (영헌)

### 흐름
```
[GitHub Apps ecs-sims] → CodeStar → CodeBuild (Docker × 3) → ECR
  → ecs:update-service --force-new-deployment
```

### Stages
1. **Source** — `BookFlowAI-Apps` `main` · path filter `ecs-sims/**`
2. **Build** — 3 service (`online-sim` `offline-sim` `inventory-api`) docker build/push
3. **Deploy** — `aws ecs update-service` (3개 service 각각)

### 작성 파일
| 파일 | 위치 |
|------|------|
| `ecs-pipeline.yaml` | `cicd/codepipeline/` |
| `buildspec.yml` | `BookFlowAI-Apps/ecs-sims/` |

### IAM 추가
- `ecs:UpdateService` · `ecs:DescribeServices`
- `iam:PassRole` (taskdef execution role)

### 검증
```bash
aws ecs describe-services --cluster bookflow-ecs --services bookflow-online-sim \
  --query 'services[0].deployments[0].rolloutState'  # 기대: COMPLETED
```

---

## 3. lambda-sam-pipeline (영헌) · **Optional**

> **권장**: 평소 iteration 은 [로컬 `sam deploy` (Appendix A)](#appendix-a-lambda-로컬-sam-deploy-iteration-디폴트) 으로. 이 CodePipeline 은 시연/표준 CICD 데모용.

### 흐름
```
[GitHub Platform aws] → CodeStar → CodeBuild (sam build/package) 
  → CloudFormation deploy stack `bookflow-99-lambdas`
```

### Stages
1. **Source** — `BookFlowAI-Platform` `main` · path filter `infra/aws/99-serverless/**`
2. **Build** — `sam build --template infra/aws/99-serverless/sam-template.yaml`
3. **Deploy** — `sam deploy` 또는 CFN action (CHANGE_SET_REPLACE)

### 작성 파일
| 파일 | 위치 |
|------|------|
| `lambda-sam-pipeline.yaml` | `cicd/codepipeline/` |
| `buildspec-sam.yml` | `scripts/aws/` |

### IAM
- CFN deploy role (Lambda + IAM + EventBridge + API Gateway 생성/수정 권한)

### 검증
```bash
aws lambda list-functions --query 'Functions[?starts_with(FunctionName,`bookflow-`)].[FunctionName,LastModified]'
# 7 Lambda 모두 최신 commit 시각 이후
```

---

## 4. publisher-codedeploy (우혁) · V6.2 Slide 22

### 흐름 (PPT 7 단계 그대로)
```
1. GitHub                → CodePipeline (webhook)
2. CodePipeline          → CodeBuild (소스 빌드 · AppSpec/scripts 압축 → 1 artifact)
3. CodeBuild             → S3 (artifact bucket)
4. CodePipeline          → CodeDeploy (배포 명령)
5. CodeDeploy            → ASG Green (Blue Launch Template 기반 신규 EC2 그룹 생성)
6. Green EC2 → S3 VPC EP → S3 (artifact 다운로드 + 검증)
7. CodeDeploy            → ALB (Traffic Swap: Blue → Green) · 이전 Blue 자동 제거
```

### Stages
1. **Source** — `BookFlowAI-Apps` `main` · path filter `publisher/**`
2. **Build** — `publisher/` 압축 → S3 artifact
3. **Deploy** — CodeDeploy Blue/Green
   - Application: `bookflow-publisher`
   - DeploymentGroup: Tier 40 publisher-asg.yaml 의 ASG 와 ALB TG (Blue) 연결
   - Strategy: `BLUE_GREEN` · `OneAtATime`
   - DeploymentConfig: `CodeDeployDefault.AllAtOnce` 또는 `HalfAtATime`

### 작성 파일
| 파일 | 위치 |
|------|------|
| `publisher-codedeploy.yaml` | `cicd/codepipeline/` (Pipeline + CodeDeploy app/group) |
| `appspec.yml` | `BookFlowAI-Apps/publisher/` (BeforeInstall · AfterInstall · ValidateService) |
| `scripts/*.sh` | `BookFlowAI-Apps/publisher/scripts/` (hook scripts) |

### IAM
- `codedeploy:*` on `bookflow-publisher` deployment group
- `autoscaling:*` (ASG Green 생성)
- `ec2:Describe*` · `elasticloadbalancing:*` (Traffic Swap)
- `iam:PassRole` (Publisher EC2 instance role)

### 검증
```bash
aws deploy list-deployments --application-name bookflow-publisher --deployment-group-name bookflow-publisher-dg \
  --query 'deployments[0]'
curl -i http://${ALB_DNS}/  # 200 OK
```

---

## 5. glue-redeploy · GitHub Actions (민지) · V6.2 Slide 23

### 흐름 (PPT 5 단계 그대로)
```
1. GitHub                       → GHA (Push: glue-jobs/**.py 변경 감지)
2. GHA                          → AWS IAM (OIDC · 15분 임시 권한)
3. AWS SSM Send-Command         → Ansible CN (t3.nano · Ansible VPC) · git pull
4. Ansible CN: glue-deploy.yml  → Task 1: amazon.aws.s3_sync (S3 Gateway Endpoint 경유 · bookflow-glue-scripts-{account})
5. Ansible CN: glue-deploy.yml  → Task 2: community.aws.glue_job (StartJobRun · Glue Interface VPC Endpoint 경유)
```

### 핵심 포인트
- Ansible CN 위치: **Ansible VPC** (Tier 10 `vpc-ansible.yaml` · 별도 VPC)
- S3 sync: **S3 Gateway Endpoint** 경유 (무료)
- Glue Job 호출: **Glue Interface VPC Endpoint** 경유 (Tier 30 endpoint)
- 트리거: GHA workflow 의 `paths: ['glue-jobs/**']` 만 (다른 파일 변경 시 skip)

### 작성 파일
| 파일 | 역할 |
|------|------|
| `.github/workflows/glue-redeploy.yml` | GHA workflow (현재 stub) |
| `cicd/ansible/playbooks/glue-deploy.yml` | Task 1+2 정의 |
| `cicd/ansible/roles/glue-sync/` | 모듈 호출 wrapper (S3 sync · Glue StartJobRun) |

### Workflow 골격
```yaml
name: glue-redeploy
on:
  push:
    branches: [main]
    paths: ['glue-jobs/**']
permissions:
  id-token: write   # OIDC
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::${{ secrets.AWS_ACCOUNT }}:role/bookflow-gha-glue-role
          aws-region: ap-northeast-1
      - name: Send-Command to Ansible CN (git pull + playbook)
        run: |
          aws ssm send-command \
            --document-name AWS-RunShellScript \
            --targets "Key=tag:Name,Values=bookflow-ansible-node" \
            --parameters 'commands=[
              "cd /opt/ansible && git pull",
              "ansible-playbook playbooks/glue-deploy.yml -e github_sha=${{ github.sha }}"
            ]'
```

### Playbook 골격 (`glue-deploy.yml`)
```yaml
- hosts: localhost
  tasks:
    - name: Sync glue scripts → S3
      amazon.aws.s3_sync:
        bucket: bookflow-glue-scripts-{{ aws_account }}
        file_root: /opt/ansible/glue-jobs/
        region: ap-northeast-1
    - name: Trigger Glue Job
      community.aws.glue_job:
        name: bookflow-{{ item }}
        state: present
      loop:
        - raw-pos-mart
        - raw-sns-mart
        - raw-aladin-mart
        - raw-event-mart
        - sales-daily-agg
        - features-build
```

### 검증
```bash
aws s3 ls s3://bookflow-glue-scripts-${ACCOUNT}/ --recursive
aws glue get-job-runs --job-name bookflow-raw-pos-mart --query 'JobRuns[0].[JobRunState,StartedOn]'
```

---

## 6. rds-redeploy · GitHub Actions (민지 · playbook 작성까지) · V6.2 Slide 24

> **Note**: SQL 파일 (`cicd/ansible/sql/*.sql`) 의 schema/seed 내용 자체는 영헌 (WBS "영헌=RDS 시드"). 민지는 GHA workflow + Ansible playbook 까지.

### 흐름 (PPT 7 단계 그대로)
```
1. GitHub                          → GHA (Push: cicd/ansible/sql/**.sql 또는 cicd/ansible/playbooks/rds-*.yml 감지)
2. GHA                             → AWS IAM (OIDC · 15분 임시 권한)
3. AWS SSM                         → Ansible CN · git pull
4. SSM Run Command                 → Ansible CN · DB 초기화 playbook 실행
5. Ansible CN: rds-schema.yml      → postgresql_query 모듈 · TGW 경유 · 19 테이블 DDL 생성
6. Ansible CN: rds-seed.yml        → Jinja2 SQL · 도서 1000권 + 지점 12개 + 판매 + 재분배 계획 INSERT
7. Ansible CN: rds-grants.yml      → postgresql_privs 모듈 · bookflow_app 최소 권한
```

### 핵심 포인트
- Schema = **19 tables** (BOOKFLOW_Data_Schema_v3.xlsx 기준)
- Seed = **도서 1000권 + 지점 12개** + 판매 데이터 + 재분배 계획
- 통신: 구축 시 = VPC Peering · 운영 = TGW
- Ansible CN 인증: SecretsManager 의 RDS password (IAM role 으로 fetch)

### 작성 파일
| 파일 | 역할 |
|------|------|
| `.github/workflows/rds-redeploy.yml` | GHA workflow |
| `cicd/ansible/playbooks/rds-schema.yml` | postgresql_query · DDL 19 tables |
| `cicd/ansible/playbooks/rds-seed.yml` | Jinja2 + INSERT (1000권 등) |
| `cicd/ansible/playbooks/rds-grants.yml` | postgresql_privs · bookflow_app |
| `cicd/ansible/sql/01-schema.sql` | 19 tables DDL (스키마 v3 변환) |
| `cicd/ansible/sql/02-seed.sql` | 시드 데이터 |
| `cicd/ansible/sql/03-grants.sql` | 권한 |

### 검증
```bash
psql -h $RDS_ENDPOINT -U bookflow -d bookflow -c "\dt"  # 19 tables
psql -h $RDS_ENDPOINT -U bookflow -d bookflow -c "SELECT COUNT(*) FROM books"  # 1000
psql -h $RDS_ENDPOINT -U bookflow -d bookflow -c "SELECT COUNT(*) FROM branches"  # 12
```

---

## 분업 제안

각 파이프라인 = 1 PR. WBS "전원=CICD · 먼저끝" 룰 + 영역 매칭.

- [ ] **#1 eks-pipeline** — **영헌**
- [ ] **#2 ecs-pipeline** — **영헌**
- [ ] **#3 lambda-sam-pipeline** — *Optional* · 시연 직전 누구든 (디폴트 로컬 `sam deploy`)
- [ ] **#4 publisher-codedeploy** — **우혁**
- [ ] **#5 glue-redeploy** — **민지**
- [ ] **#6 rds-redeploy** — **민지** (GHA + Ansible playbook 작성까지) · SQL 본문은 영헌

## 작성 순서 권장

1. **#5 glue-redeploy** (민지) — 단순 (S3 sync + Glue StartJobRun) · 민지 ETL 시작 시 필요.
2. **#6 rds-redeploy** (영헌) — 스키마 v3 → SQL 변환만 새로 · 패턴은 #5 와 동일.
3. **#3 lambda-sam-pipeline** (영헌) — CodePipeline 가장 단순 · 4 CFN 의 패턴 레퍼런스.
4. **#4 publisher-codedeploy** (영헌) — Blue/Green 데모 · V6.2 Slide 22 흐름 그대로.
5. **#2 ecs-pipeline** (영헌) — eks 직전 워밍업.
6. **#1 eks-pipeline** (영헌) — 가장 복잡 (Pod 8 multi-build + kubectl 인증).

## 비용 (참고 · 비용산정 V1)

| 자원 | 라이프 | 비용 |
|------|------|------|
| CodePipeline × 4 | 🟡 필요 시 deploy (idle 시 $1/월 · 첫 30일 free) | 사실상 $0 |
| CodeBuild | 트리거 시만 compute 과금 (분당) | 빌드당 \~$0.005 |
| CodeDeploy | 트리거 시만 | 배포당 \~$0 |
| GitHub Actions | 🔒 영구 (public 무료 · private 2000분/월 free) | $0 |

**원칙**: V1 비용산정 의 "매일 destroy/create" 는 CodePipeline 비용을 보수적으로 잡은 거. 실제로는 idle 비용 없으므로 **CICD stack 은 코드 push 검증 필요할 때만 deploy** 하고 검증 끝나면 destroy. 매일 자동 destroy/create 는 학프 환경엔 과함.

**합계 (CICD 카테고리)**: $10.52/월 (전체 비용의 5.2%)

---

## Appendix A · Lambda 로컬 sam deploy (iteration 디폴트)

CodePipeline (#3) 은 표준 데모용. 평소 iteration 은 로컬 `sam deploy` 가 빠름 (~30초).

### 사전 요구
```bash
pip install aws-sam-cli
aws configure   # 계정 + region
```

### iteration cycle
```bash
cd "C:/Users/User/Desktop/kyobo project/BookFlowAI-Platform"

# 1. Lambda 코드 수정 (예: infra/aws/99-serverless/lambdas/pos-ingestor/handler.py)

# 2. SAM build + deploy
sam build --template infra/aws/99-serverless/sam-template.yaml
sam deploy \
  --stack-name bookflow-99-lambdas \
  --s3-bucket bookflow-cp-artifacts-${ACCOUNT_ID} \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

# 3. 즉시 검증
aws lambda invoke --function-name bookflow-pos-ingestor --payload '{}' /tmp/out.json
cat /tmp/out.json
aws logs tail /aws/lambda/bookflow-pos-ingestor --follow
```

### 첫 실행만 (`--guided`)
```bash
sam deploy --guided
# stack name, region, capabilities 한 번 입력 → ./samconfig.toml 자동 저장
```

### 비교

| 방식 | iteration 시간 | 적합 |
|------|--------------|------|
| 로컬 `sam deploy` | \~30초 | 일상 개발 |
| GHA workflow | \~2-3분 (push → trigger → build → deploy) | 협업 자동화 |
| CodePipeline (#3) | \~3-5분 (Source + Build + Deploy stage) | 시연용 |

→ **개발 중 = 로컬, 시연 직전 = CodePipeline 한 번 띄워서 보여줌.**

