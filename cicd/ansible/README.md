# BOOKFLOW CI/CD 파일 설명

> 이번 세션에서 작성·수정된 파일들의 역할과 기능 설명

---

## 파일 구조

```
BookFlowAI-Platform/
├── .github/workflows/
│   └── glue-redeploy.yml          # GHA 워크플로우 (파이프라인 시작점)
└── cicd/ansible/
    ├── inventory/
    │   └── hosts.yml              # Ansible 실행 대상 정의
    ├── playbooks/
    │   └── glue-deploy.yml        # 배포 작업 정의서
    └── roles/glue-scripts/tasks/
        └── main.yml               # S3 sync + Glue StartJobRun 실행

bookflow-azure-iac/scripts/
├── cleanup-selective.sh           # Azure 리소스 선택적 삭제
└── vpn-connect.sh                 # AWS ↔ Azure VPN 자동 연결
```

---

## 1. `glue-redeploy.yml` — GitHub Actions Workflow

**역할**: 자동화 파이프라인의 **시작점 · 오케스트레이터**

```
glue-jobs/*.py 파일이 main 브랜치에 push → 전체 파이프라인 자동 실행
```

### Step별 기능

| Step | 기능 |
|------|------|
| OIDC 인증 | GitHub → AWS 직접 인증. 비밀번호 없이 15분 임시 권한 발급 (`bookflow-gha-glue-role`) |
| 인스턴스 ID 조회 | `bookflow-ansible-node` 태그로 Ansible CN의 EC2 ID 자동 탐색 |
| SSM Send-Command | Ansible CN에 원격 명령 전송 (SSH 없이). `git pull` + `ansible-playbook` 실행 |
| 완료 대기 | Ansible 플레이북 종료까지 최대 10분 대기 |
| 결과 출력 | 성공/실패 + 실행 로그를 GHA 화면에 출력. 실패 시 워크플로우도 실패 처리 |

### 트리거 조건

```yaml
on:
  push:
    branches: [main]
    paths: ['glue-jobs/**']   # 이 경로 외 파일 변경은 무시
```

### 사전 준비

GitHub Settings → Secrets → Actions에 등록 필요:
```
AWS_ACCOUNT_ID = 123456789012
```

---

## 2. `glue-deploy.yml` — Ansible Playbook

**역할**: Ansible CN 위에서 실행되는 **실제 배포 작업 정의서**

```
SSM 명령을 받은 Ansible CN이 실행
→ S3 sync + Glue Job 실행을 순서대로 수행
```

### 세부 기능

| 항목 | 내용 |
|------|------|
| `hosts: localhost` | Ansible CN이 자기 자신에서 실행 (외부 SSH 불필요) |
| AWS account ID 자동 조회 | `aws sts get-caller-identity`로 계정 ID 동적 조회 (하드코딩 없음) |
| S3 버킷 이름 동적 생성 | `bookflow-glue-scripts-{account_id}` 형태로 자동 조합 |
| 변수 확인 출력 | 실행 전 S3 버킷명·스크립트 경로·commit SHA 출력 (디버깅용) |
| `roles: glue-scripts` 호출 | 실제 작업은 role에 위임 |

### 전달 변수

```bash
# GHA에서 SSM Send-Command로 주입
ansible-playbook glue-deploy.yml -e github_sha=<commit_sha>
```

---

## 3. `roles/glue-scripts/tasks/main.yml` — Ansible Role

**역할**: 배포의 **핵심 실행 엔진** — S3 업로드 + Glue Job 실행 2가지 수행

### Task 1: S3 sync

```
/opt/ansible/glue-jobs/*.py
    → S3 Gateway Endpoint (무료 경유)
    → s3://bookflow-glue-scripts-{accountId}/
```

| 항목 | 내용 |
|------|------|
| `amazon.aws.s3_sync` | 변경된 파일만 업로드 (전체 재업로드 아님) |
| `delete: false` | S3의 기존 파일 삭제 안 함 (안전) |
| S3 Gateway Endpoint | VPC 내부 → S3 직접 통신, 인터넷 미사용, 비용 $0 |

### Task 2: Glue Job StartJobRun

6개 Glue Job을 순서대로 실행 요청:

| Job 이름 | 역할 |
|----------|------|
| `bookflow-raw-pos-mart` | POS 판매 데이터 정제 |
| `bookflow-raw-sns-mart` | SNS 알림 데이터 정제 |
| `bookflow-raw-aladin-mart` | 알라딘 도서 데이터 정제 |
| `bookflow-raw-event-mart` | 이벤트 데이터 정제 |
| `bookflow-sales-daily-agg` | 일별 판매 집계 |
| `bookflow-features-build` | ML 피처 생성 |

- `--github_sha` 인자 전달 → 어떤 버전 코드로 실행됐는지 추적 가능
- 각 Job의 `JobRunId` 로그 출력 → AWS 콘솔에서 직접 조회 가능

---

## 4. `inventory/hosts.yml` — Ansible Inventory

**역할**: Ansible에게 **"어디서 실행할지"** 알려주는 접속 대상 정의

```yaml
localhost:
  ansible_connection: local       # SSH 없이 자기 자신에서 실행
  ansible_python_interpreter: /usr/bin/python3
```

**왜 localhost인가:**
- Ansible CN(EC2)이 SSM 명령을 받아 **자기 자신**에서 플레이북 실행
- Glue·S3 모두 AWS SDK(boto3)로 호출 → 원격 접속 불필요
- CN의 IAM Role이 S3/Glue 권한 보유 → 별도 크리덴셜 불필요

### glue-redeploy 실행 확인

```bash
# S3 업로드 확인
aws s3 ls s3://bookflow-glue-scripts-${AWS_ACCOUNT_ID}/ --recursive

# Glue Job 실행 상태 확인
for job in raw-pos-mart raw-sns-mart raw-aladin-mart raw-event-mart sales-daily-agg features-build; do
  aws glue get-job-runs --job-name bookflow-$job \
    --query "JobRuns[0].{State:JobRunState, Started:StartedOn}" \
    --output table
done

# SSM 명령 이력 확인
aws ssm list-command-invocations \
  --filters key=DocumentName,value=AWS-RunShellScript \
  --details \
  --query "CommandInvocations[0].{Status:Status,Output:CommandPlugins[0].Output}"
```
