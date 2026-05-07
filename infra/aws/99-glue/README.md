# Tier 99-glue · Glue Catalog + 6 Jobs + Step Functions

## 이 Tier의 역할

**Glue Database + 6 ETL Jobs (Flex) + BigQuery Connection + Step Functions ETL3 orchestration** — Raw → Mart → Features 정제 파이프라인.

## Stack (2개)

| YAML | 내용 | 사용 task |
|---|---|---|
| `glue-catalog.yaml` | Glue Database (`bookflow_mart`) + Glue Service Role + 6 Jobs (Flex) + BigQuery Connection | task-glue |
| `step-functions.yaml` | ETL3 State Machine (Glue Jobs orchestration) + IAM Role + CW Logs | task-glue |

## 6 Glue Jobs (V6.2 + 비용산정 V1 매핑)

| Job | 입력 | 출력 | 역할 |
|---|---|---|---|
| `raw_pos_mart` | S3 Raw POS Parquet | S3 Mart `sales_fact` | POS 이벤트 정제 + 차원 키 매핑 |
| `raw_sns_mart` | S3 Raw SNS JSON | S3 Mart `sns_event_fact` | SNS 시뮬 데이터 정제 |
| `raw_aladin_mart` | S3 Raw 알라딘 JSON | S3 Mart `book_dim` 갱신 | 도서 메타 정보 정제 |
| `raw_event_mart` | S3 Raw 이벤트 JSON | S3 Mart `external_event_fact` | 공공데이터 이벤트 정제 |
| `sales_daily_agg` | S3 Mart `sales_fact` | S3 Mart `sales_daily_agg` | 일별 매장×도서 매출 집계 |
| `features_build` | S3 Mart + BigQuery historical | S3 Mart `features_train` | forecast 학습용 feature 조립 (BQ Connection) |

**공통 설정**:
- Glue 4.0 (Spark 3.3 · Python 3.10)
- ExecutionClass: **FLEX** (표준 $0.44 → $0.29/DPU-hr · 34% 할인)
- WorkerType: G.1X · NumberOfWorkers: 2 (features_build 만 4)
- Job bookmark: enable (재실행 시 처리한 파일 skip)
- ScriptLocation: `s3://bookflow-glue-scripts-*/scripts/<job_name>.py` (CI/CD Pipeline 5 Glue 가 push)

## ETL3 Step Functions State Machine

```
[Parallel Branch]
  ├── RawPosMart       (Glue Job 1)
  ├── RawSnsMart       (Glue Job 2)
  ├── RawAladinMart    (Glue Job 3)
  └── RawEventMart     (Glue Job 4)
       ↓ (모두 성공 시)
[SalesDailyAgg]        (Glue Job 5)
       ↓
[FeaturesBuild]        (Glue Job 6 · BigQuery Connection)
       ↓
[Done]
```

각 단계 Retry: 30s → 60s → 120s (BackoffRate 2.0 · MaxAttempts 2)
실패 시 catch → `Failed` state → SNS 알림 (선택 · 추후 추가)

**트리거**:
- 수동 실행: `aws stepfunctions start-execution`
- 자동: forecast-trigger Lambda (cron daily 04:00 KST · Tier 99-serverless)

## 배포

### task-glue.ps1 (권장)
```powershell
.\scripts\aws\2-tasks\task-glue.ps1
```
- glue-catalog → step-functions 순차 deploy
- task-lambdas 가 이미 deploy 됐으면 forecast-trigger 의 SF ARN 자동 update-stack
- task-lambdas 미배포 시: 추후 task-lambdas 실행 때 SF ARN 자동 조회 + 주입

### 직접 deploy
```powershell
aws cloudformation deploy --stack-name bookflow-99-glue-catalog --template-file infra/aws/99-glue/glue-catalog.yaml --capabilities CAPABILITY_NAMED_IAM
aws cloudformation deploy --stack-name bookflow-99-step-functions --template-file infra/aws/99-glue/step-functions.yaml --capabilities CAPABILITY_NAMED_IAM
```

## Import / Export

### glue-catalog.yaml
- Imports: s3-raw-name · s3-mart-name · s3-glue-scripts-name · secrets-glue-gcp-arn · secrets-rds-master-arn
- Exports: glue-database-name · glue-service-role-arn · glue-job-* (6) · glue-connection-bigquery

### step-functions.yaml
- Imports: glue-job-* (6) — glue-catalog 후 deploy 필수
- Exports: **sfn-etl3-arn** · sfn-etl3-name (forecast-trigger Lambda 가 사용)

## 검증

```powershell
# lint
cfn-lint infra\aws\99-glue\*.yaml

# Glue Database
aws glue get-database --name bookflow_mart

# 6 Jobs 확인
aws glue get-jobs --query 'Jobs[?starts_with(Name, `bookflow-`)].{Name:Name,GlueVersion:GlueVersion,Class:ExecutionClass}' --output table

# State Machine
aws stepfunctions list-state-machines --query 'stateMachines[?contains(name, `bookflow-etl3`)]'

# 수동 ETL3 실행 (스크립트 placeholder 라 실제로는 fail · skeleton 검증용)
aws stepfunctions start-execution --state-machine-arn <arn> --input '{}'

# 실행 history
aws stepfunctions get-execution-history --execution-arn <execution-arn>
```

## Glue Script 작성 (실 구현)

스크립트는 `bookflow-apps` repo (또는 별도) 의 `glue-scripts/` 폴더에 작성:
- raw_pos_mart.py
- raw_sns_mart.py
- raw_aladin_mart.py
- raw_event_mart.py
- sales_daily_agg.py
- features_build.py

배포: CI/CD Pipeline 5 (Glue Scripts CI/CD · IaC_CICD_Plan Section 7)
- GitHub push glue-scripts/** → CodePipeline → CodeBuild → `aws s3 sync glue-scripts/ s3://bookflow-glue-scripts-*/scripts/`
- Glue Job 의 ScriptLocation 은 그대로 (재배포 불필요)

## 비용 (Tier 99-glue · 비용산정 V1 일치)

| 자원 | 시간당 | 월 비용 |
|---|---|---|
| Glue Jobs Flex × 14 DPU | $0.29/DPU-hr | $4.06 (실 실행 시간) |
| Glue Data Catalog | 첫 100만 객체 무료 | $0 |
| Glue Connection (BigQuery) | 자체 무료 (DPU 시간만) | $0 |
| Step Functions | 4,000 transitions/월 free tier | $0 (~500 transitions) |
| CW Logs (7d retention) | minimal | ~$0.10 |

**합계 예상**: ~$4.20/월

## CI/CD 연결

### Pipeline 5 (Glue Scripts CI/CD · IaC_CICD_Plan Section 7)
- Source: bookflow-apps `glue-scripts/**`
- Build: CodeBuild
  - `aws s3 sync glue-scripts/ s3://bookflow-glue-scripts-*/scripts/ --delete`
  - SHA256 검증
  - `aws glue start-job-run --job-name <name> --arguments '--dry-run=true'` (선택 · 검증 실행)
- 결과: 6 Jobs 새 코드로 즉시 사용 가능 (다음 cron 실행 시)

### GHA Glue Redeploy (Resource_Lifecycle Section 10)
- 대안: GHA OIDC → SSM → Ansible CN → `ansible-playbook glue-deploy.yml`
- 영구 자원: `gha-glue-redeploy` IAM Role (Tier 00 iam.yaml 이미 있음)
- 스크립트 변경 push → GHA 자동 reconcile (CN 부재 시 다음 base-up 에서 처리)

## 비고

- Glue Job MaxConcurrentRuns: 1 (동시 실행 방지 · 같은 데이터 중복 처리 방지)
- Job bookmark: enable (S3 partition 재처리 방지)
- BigQuery Connection: GCP SA key from Secrets Manager (실제 사용은 features_build · 추후 GCP 팀 SA key 받아서 secret 갱신)
- features_build NumberOfWorkers: 4 (BigQuery 큰 데이터 처리)
- 다른 jobs NumberOfWorkers: 2 (적은 데이터 · Flex 절감)
- ETL3 State Machine STANDARD type (EXPRESS 아님 · long-running 가능)
- 4 raw_*_mart parallel 실행 (병렬화 · 실행 시간 1/4 수준)
