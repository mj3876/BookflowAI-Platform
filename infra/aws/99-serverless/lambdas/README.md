# lambdas

V6.3 / V6.2 Tier 99-serverless · 7 Lambdas (cron + Kinesis ESM + API GW). **Platform 단일 관리** (이전 `BookFlowAI-Apps/lambdas/` 폐기).

## 위치
`BookFlowAI-Platform/infra/aws/99-serverless/lambdas/<name>/` — sam-template.yaml 와 colocated. SAM CLI 표준 패턴.

## 현재 상태
| Lambda | 상태 | 설명 |
|---|---|---|
| `pos-ingestor` | **real** (`lambda_function.py`) | Kinesis ESM consumer · V3 sales_realtime/inventory/audit_log + Redis pub stock.changed |
| `aladin-sync` | placeholder (`index.py`) | EventBridge daily · books/authors/publishers UPSERT (Phase 4) |
| `event-sync` | placeholder | EventBridge daily · 외부 이벤트 sync |
| `sns-gen` | placeholder | EventBridge 10min · 가짜 SNS 멘션 데이터 → S3 |
| `spike-detect` | placeholder | EventBridge 10min · z-score → spike_events INSERT + Redis pub |
| `forecast-trigger` | placeholder | EventBridge daily · Step Functions invoke (Vertex AI 트리거) |
| `secret-forwarder` | placeholder | API GW HTTP · Azure Function 가 Entra secret push |

`sam-template.yaml` 7 Lambda 정의:
- pos-ingestor: `CodeUri: { Bucket: ..., Key: lambda/pos-ingestor.zip }` (S3 ref)
- 나머지 6: `InlineCode: |` placeholder (real 화 시 동일 패턴 — `build.sh <name>` + sam-template CodeUri 갱신)

## 빌드 + 배포

### Manual (CodeStar 부재 임시)
```bash
# 1. Linux x86_64 manylinux wheel zip + S3 upload
AWS_PROFILE=bookflow-admin AWS_REGION=ap-northeast-1 \
  bash infra/aws/99-serverless/lambdas/build.sh pos-ingestor

# 2. SAM template CFN deploy (S3 CodeUri auto-pull 새 zip)
aws cloudformation deploy --profile bookflow-admin --region ap-northeast-1 \
  --template-file infra/aws/99-serverless/sam-template.yaml \
  --stack-name bookflow-99-serverless \
  --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    LambdaArtifactBucket=bookflow-cp-artifacts-994878981869 \
    PosIngestorS3Key=lambda/pos-ingestor.zip \
    RdsHost=... RedisHost=... PosIngestorSecretArn=arn:...

# 3. (또는 코드만 빠르게) Lambda update-function-code
aws lambda update-function-code --function-name bookflow-pos-ingestor \
  --s3-bucket bookflow-cp-artifacts-994878981869 \
  --s3-key lambda/pos-ingestor.zip
```

### SAM CLI (평일/CICD 복구 시)
```bash
# SAM CLI 설치 후
cd infra/aws/99-serverless
sam build --use-container          # Docker 로 manylinux wheel
sam deploy --guided                # S3 자동 업로드 + CFN deploy
```

### CodePipeline (평일 codestar)
- `aws` branch push 감지 → CodeBuild → `sam build && sam deploy`
- `infra/aws/99-serverless/buildspec.yml` 후속

## 환경변수 (per Lambda · sam-template Parameters)
- pos-ingestor: `RDS_HOST` `RDS_DB=bookflow` `RDS_USER=pos_ingestor` `RDS_SECRET_ARN` `REDIS_HOST` `LOG_LEVEL`
- 다른 6 Lambda: real 화 시 추가
