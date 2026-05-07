# Tier 99-serverless · Lambda + SAM

## 이 Tier의 역할

**7 Lambdas + EventBridge 5 cron + Kinesis ESM + API Gateway HTTP** — ETL · forecast · auth · 시뮬 데이터 처리.

## Stack (1개 SAM template)

`sam-template.yaml` — SAM Transform (`AWS::Serverless-2016-10-31`) 사용 · 단일 yaml 에 모든 리소스.

### 7 Lambdas 매트릭스

| # | Lambda | 트리거 | VPC | 권한 | 역할 |
|---|---|---|---|---|---|
| 1 | `aladin-sync` | EventBridge cron daily 04:00 KST | ❌ | S3 Raw write · Secrets (TTB key) | 알라딘 API → S3 Raw |
| 2 | `event-sync` | EventBridge cron daily 03:00 KST | ❌ | S3 Raw write · Secrets (publicdata) | 공공데이터 API → S3 Raw |
| 3 | `sns-gen` | EventBridge cron 10min | ❌ | S3 Raw write · Secrets (sns-config) | SNS 시뮬 데이터 생성 |
| 4 | `spike-detect` | EventBridge cron 10min | ✅ | RDS read/write (via Secrets) | Z-score 이상 감지 · spike_events INSERT |
| 5 | `forecast-trigger` | EventBridge cron daily 04:00 | ❌ | Step Functions invoke (StateMachine ARN parameter) | ETL3 forecast pipeline trigger |
| 6 | `secret-forwarder` | API Gateway HTTP POST `/secret` | ❌ | Secrets Manager create/update · KMS Decrypt | Azure Function 트리거 시 Entra Client Secret 회전 |
| 7 | `pos-ingestor` | **Kinesis ESM** (pos-events stream · BatchSize 100) | ✅ | Kinesis read · RDS write · Secrets | inventory UPDATE · sales_realtime INSERT |

### V6.2 + Schema v3 + 비용산정 V1 매핑

- V6.2 Slide 5 (ETL1 POS): Kinesis → Lambda → RDS update inventory ✅ pos-ingestor
- V6.2 Slide 7 (ETL2 외부): aladin-sync · event-sync · sns-gen · spike-detect ✅
- V6.2 Slide 11/12/13 (forecast): forecast-trigger ✅
- V6.2 Slide 15 (Auth keys): secret-forwarder ✅
- 비용산정 V1: 6 Lambda 명시 + pos-ingestor 누락 → 본 PR 에서 7 Lambda 작성 (산정 보완 권장)

## 배포

### task-lambdas.ps1 (권장)
```powershell
.\scripts\aws\2-tasks\task-lambdas.ps1
```
- 의존성 자동 체크 (VPC · Kinesis · RDS · Peering)
- Step Functions ARN 자동 주입 (Tier 99-glue 후 deploy 시)
- CAPABILITY_AUTO_EXPAND 자동 추가 (SAM transform 필수)

### 직접 deploy
```powershell
aws cloudformation deploy `
  --stack-name bookflow-99-lambdas `
  --template-file infra/aws/99-serverless/sam-template.yaml `
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND CAPABILITY_IAM `
  --region ap-northeast-1
```

## VPC 구성 (spike-detect · pos-ingestor)

- **Subnet**: BookFlow AI VPC Private (AZ1 + AZ2 · ENI 자동 생성)
- **SG**: `bookflow-lambda-vpc-sg` (egress all · ingress 없음)
- **RDS 접근 경로**: BookFlow AI ↔ Data Peering (`bookflow-ai-data` · task-msa-pods 가 deploy)
- **Cold start**: VPC Lambda 는 ENI Hyperplane 으로 fast cold start (~100ms 추가만)

## API Gateway HTTP (secret-forwarder)

- HTTP API (REST 보다 70% 저렴)
- `POST /secret` → secret-forwarder Lambda
- Stage: `$default`
- 인증: 추후 Lambda Authorizer 또는 IAM auth 추가 권장 (현재 public · 학프 환경)

**호출 예시** (Azure Function 에서):
```http
POST https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/secret
Content-Type: application/json

{ "secretName": "bookflow/auth/entra-client-secret", "value": "<new-secret>" }
```

## Step Functions 연결 (forecast-trigger)

- Tier 99-glue 의 ETL3 Step Functions State Machine 을 trigger
- Tier 99-glue 배포 후 stack output `Etl3StateMachineArn` → task-lambdas 가 자동 update-stack

## Kinesis ESM 설정 (pos-ingestor)

- **BatchSize**: 100 (Kinesis 한 번에 100 records)
- **MaximumBatchingWindow**: 5초 (low latency)
- **StartingPosition**: LATEST (재시작 시 새 데이터부터)
- **FunctionResponseTypes**: ReportBatchItemFailures (개별 record 실패 보고)
- **ReservedConcurrency**: 5 (RDS connection pool 보호)

## 코드 작성 (실 구현)

현재는 inline placeholder 코드 (handler 가 print + return 만).

실 코드 작성 시:
1. `lambdas/<name>/app.py` 작성 (이미 README skeleton 있음)
2. `lambdas/<name>/requirements.txt` 작성
3. SAM template 의 `InlineCode:` 제거 + `CodeUri: lambdas/<name>/` 로 변경
4. `sam build` (PIP 의존성 설치) → `sam deploy` (S3 upload + CFN deploy)

또는:
- `aws cloudformation package --template-file sam-template.yaml --s3-bucket <bucket> --output-template-file packaged.yaml`
- `aws cloudformation deploy --template-file packaged.yaml ...`

## 비용 (Tier 99-serverless · 모두 활성 시)

| Lambda | 호출 횟수/월 | Free Tier | 실제 비용 |
|---|---|---|---|
| aladin-sync (1/일) | ~25 | 100만/월 | $0 |
| event-sync (1/일) | ~25 | | $0 |
| sns-gen (10min) | ~3,600 | | $0 |
| spike-detect (10min · VPC) | ~3,600 | | $0 (~$0.05 EBS Lambda 미미) |
| forecast-trigger (1/일) | ~25 | | $0 |
| secret-forwarder (수동) | ~10 | | $0 |
| pos-ingestor (Kinesis ESM) | ~수만 | | $0 (프리티어) |
| API Gateway HTTP | ~10 호출 | 100만/월 | $0 |
| EventBridge | 5 cron | 100만 이벤트/월 | $0 |

**합계 예상**: **~$0/월** (모두 프리티어 내)

## 검증

```powershell
# lint (SAM transform 후 expand 된 결과 검증)
cfn-lint infra\aws\99-serverless\sam-template.yaml

# 배포 후 Lambda 7개 확인
aws lambda list-functions --query 'Functions[?starts_with(FunctionName, `bookflow-`)].{Name:FunctionName,Runtime:Runtime,Memory:MemorySize}' --output table

# EventBridge cron 5개 확인
aws events list-rules --query 'Rules[?contains(Name, `bookflow`)].{Name:Name,Schedule:ScheduleExpression}' --output table

# Kinesis ESM 확인
aws lambda list-event-source-mappings --function-name bookflow-pos-ingestor

# API Gateway URL 조회
aws cloudformation describe-stacks --stack-name bookflow-99-lambdas --query 'Stacks[0].Outputs[?OutputKey==`SecretForwarderApiUrl`].OutputValue' --output text

# Lambda 동작 테스트
aws lambda invoke --function-name bookflow-aladin-sync --payload '{"test":true}' /tmp/out.json && cat /tmp/out.json
```

## CI/CD 연결 (V6.2 Slide 21 · Lambda SAM)

- bookflow-platform repo 에 SAM template 위치 (`infra/aws/99-serverless/sam-template.yaml`)
- bookflow-platform repo 에 lambdas/* 폴더 (실 코드)
- GitHub push → CodePipeline → CodeBuild (sam build) → CodeBuild (sam deploy)
- → 7 Lambda 모두 새 코드로 갱신

## 비고

- SAM transform 필수 → CAPABILITY_AUTO_EXPAND
- Lambda Runtime: python3.12 (LTS)
- forecast-trigger 의 StepFunctionsArn parameter: 비어있을 때 placeholder · Tier 99-glue 후 update-stack
- pos-ingestor `ReservedConcurrency: 5` — RDS connection pool 과다 방지
- API Gateway HTTP API 사용 (REST API 대비 저렴 · OAuth 등 advanced 기능 불필요)
- VPC Lambda 의 ENI 는 SG 부착되어 외부 inbound 차단됨
