# pos-ingestor

pos-ingestor Lambda · Kinesis ESM (pos-events stream) → RDS inventory UPDATE · sales_realtime INSERT

**라이프사이클**: 🔒 영구 (코드는 영구 · 실행은 Kinesis 데이터 들어올 때만)

> BOOKFLOW V6.2 Slide 5 · Schema v3 MSA 소유권

## 트리거
- **Kinesis ESM**: `bookflow-pos-events` (5 shards · BatchSize 100 · MaxBatchingWindow 5s)
- **VPC**: BookFlow AI Private subnet (Peering bookflow-ai-data 로 RDS 접근)

## 권한
- Kinesis read (DescribeStream · GetShardIterator · GetRecords)
- Secrets Manager read (RDS master password)
- VPC ENI (AWSLambdaVPCAccessExecutionRole)

## 동작 (실 구현 가이드)
1. Kinesis batch 받음 (max 100 records · 5초 wait)
2. 각 record decode (base64 + JSON)
3. RDS connection (psycopg2 · password from Secrets Manager · 연결 풀링)
4. inventory 테이블 UPDATE (재고 차감)
5. sales_realtime 테이블 INSERT (실시간 매출)
6. audit_log 기록
7. 실패 record → batchItemFailures 반환 (Kinesis ESM 재시도)

## 환경 변수 (실 구현 시 추가)
- `RDS_ENDPOINT` (Tier 20 rds.yaml export)
- `RDS_DBNAME`
- `RDS_SECRET_ARN`
- `KINESIS_STREAM_NAME`

## 배포
SAM template (`../sam-template.yaml`) 에서 일괄 deploy. 개별 deploy 안 함.
