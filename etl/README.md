# ETL 파이프라인 · 민지 담당 (Task 6/7/8)

> **담당:** 서민지 | **기간:** 2026-04-30 ~ 2026-05-11

---

## 디렉토리 구조

```
etl/
├── deploy-etl-infra.sh            ← ETL 전체 한 번에 배포 (ETL1 + ETL2 + ETL3 포함)
├── teardown-etl-infra.sh          ← 전체 삭제
├── README.md
│
├── 4_30/                          ← 4/30
│   ├── aladin_api/                  알라딘 API 데이터 수집 (로컬 실행)
│   │   ├── aladin_fetch.py
│   │   ├── requirements.txt
│   │   ├── .env.example             → .env 복사 후 TTBKey 입력
│   │   └── output/                  실행 후 생성 (CSV · ndjson.gz)
│   ├── task6_etl1_pos/              ECS 시뮬레이터 참고 코드
│   │   ├── ecs_online_sim/            온라인 POS 시뮬 (참고용 · 실제 배포는 ecs-sims/)
│   │   ├── ecs_offline_sim/           오프라인 POS 시뮬 (참고용)
│   │   └── ecs_api_client/            외부 판매처 API 클라이언트
│   └── task7_etl2_external/         외부 데이터 수집 Lambda
│       ├── lambda_event_sync/         공휴일/도서행사 수집 (매일 13:10 KST)
│       └── lambda_sns_gen/            SNS 멘션 합성 생성 (10분 cron)
│
├── 5_4/                           ← 5/4
│   ├── task6_etl1_pos/
│   │   └── lambda_pos_ingestor/     Kinesis → RDS sales_realtime + Redis
│   └── verify_e2e_etl1.py           ETL1 E2E 검증 스크립트
│
├── 5_6/                           ← 5/6
│   ├── task7_etl2_external/
│   │   └── lambda_spike_detect/     Z-score 스파이크 감지 (10분 cron)
│   └── task8_etl3_mart/
│       └── glue_pos_etl/
│           └── pos_etl.py           POS Raw → Mart Parquet
│
├── 5_8/                           ← 5/8
│   ├── task7_etl2_external/
│   │   └── verify_e2e_etl2.py       ETL2 E2E 검증
│   └── task8_etl3_mart/
│       └── glue_aladin_etl/
│           └── aladin_etl.py        알라딘 Raw → Mart (SCD Type-1)
│
└── 5_11/                          ← 5/11
    └── task8_etl3_mart/
        ├── glue_event_etl/
        │   └── event_etl.py         이벤트 Raw → Mart
        └── glue_sns_agg/
            └── sns_agg.py           SNS 일별 집계 → Mart
```

---

## 배포 · 삭제

### 전체 배포 (ETL1 + ETL2 + ETL3)

```bash
bash etl/deploy-etl-infra.sh
```

8단계를 순서대로 자동 실행한다:

| 단계 | 내용 |
|------|------|
| [0/8] S3 | 버킷 5개 생성 (없으면 생성, 있으면 스킵) |
| [1/8] base-up | Tier 10 VPC 4개 + Tier 30 ECS cluster |
| [2/8] task-data | Tier 20 RDS + Redis + Kinesis |
| [3/8] ECR | online-sim / offline-sim 이미지 빌드 & push |
| [4/8] task-etl-streaming | VPC endpoints + Tier 40 ECS sims |
| [5/8] SAM Lambda | Lambda 8개 + EventBridge cron(13:10 KST) + Kinesis ESM + API GW |
| [6/8] Glue Catalog | bookflow-99-glue-catalog + Step Functions ETL3 |
| [7/8] Glue scripts | S3 sync (6 Job 스크립트) |
| [8/8] 초기 수집 | aladin-sync / event-sync / sns-gen 즉시 invoke |

### 전체 삭제

```bash
bash etl/teardown-etl-infra.sh
```

삭제 순서: CodePipeline → Lambda(SAM) → Tier 10-99 → S3 버킷 5개 → bookflow-00-s3 CFN 스택

> Tier 00 (KMS / IAM / ECR / Secrets)는 삭제하지 않아 재배포 시 그대로 재사용된다.

---

## 전체 아키텍처 구축 순서

> **사전 준비**: AWS CLI, GCP CLI(`gcloud`), Terraform, SAM CLI, Docker 설치 및 인증 완료

### Phase 1 — GCP 인프라 (Terraform)

```bash
# 1-1. GCS 버킷 · BigQuery 데이터셋 · VPC 생성
terraform -chdir=infra/gcp/00-foundation init
terraform -chdir=infra/gcp/00-foundation apply

# GCS staging 버킷명 저장 (Phase 2 SAM 배포에 사용)
GCS_STAGING=$(terraform -chdir=infra/gcp/00-foundation output -raw staging_bucket_name)
echo "GCS_STAGING=${GCS_STAGING}"

# 1-2. Cloud Router · VPN · PSC 생성
terraform -chdir=infra/gcp/20-network-daily init
terraform -chdir=infra/gcp/20-network-daily apply

# 1-3. Cloud Functions(bq-load · feature-assemble · vertex-invoke) · Eventarc · Workflows 배포
terraform -chdir=infra/gcp/99-content init
terraform -chdir=infra/gcp/99-content apply
```

### Phase 2 — AWS Tier 00 (CloudFormation · 최초 1회)

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="ap-northeast-1"

# KMS 키
aws cloudformation deploy \
  --template-file infra/aws/00-foundation/kms.yaml \
  --stack-name bookflow-00-kms \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ${REGION}

# S3 버킷 (MartBucket EventBridgeEnabled: true 포함)
aws cloudformation deploy \
  --template-file infra/aws/00-foundation/s3.yaml \
  --stack-name bookflow-00-s3 \
  --parameter-overrides AccountSuffix=${ACCOUNT_ID} \
  --region ${REGION}

# Secrets Manager (bookflow/gcp-sa-key 포함)
aws cloudformation deploy \
  --template-file infra/aws/00-foundation/secrets.yaml \
  --stack-name bookflow-00-secrets \
  --region ${REGION}

# IAM 역할
aws cloudformation deploy \
  --template-file infra/aws/00-foundation/iam.yaml \
  --stack-name bookflow-00-iam \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ${REGION}

# ECR 리포지토리
aws cloudformation deploy \
  --template-file infra/aws/00-foundation/ecr.yaml \
  --stack-name bookflow-00-ecr \
  --region ${REGION}
```

### Phase 3 — ETL 전체 배포 (ETL1 + ETL2 + ETL3)

```bash
# GCS 버킷명이 기본값과 다른 경우 환경변수로 주입
# GCS_STAGING_BUCKET=your-bucket bash etl/deploy-etl-infra.sh

bash etl/deploy-etl-infra.sh
```

Lambda · Glue · Step Functions까지 포함해 8단계 자동 실행된다. 완료 후 ECS 확인:

```bash
aws ecs describe-services \
  --cluster bookflow-ecs \
  --services online-sim offline-sim \
  --region ap-northeast-1 \
  --query "services[*].{name:serviceName,running:runningCount,status:status}"
```

### Phase 4 — GCP SA 키 발급 및 시크릿 주입 (수동 1회)

```bash
# GCP 서비스 계정 생성 (미생성 시)
gcloud iam service-accounts create bookflow-mart-to-gcs \
  --display-name="BookFlow mart-to-gcs Lambda"

# GCS 버킷 쓰기 권한 부여
gcloud storage buckets add-iam-policy-binding gs://${GCS_STAGING} \
  --member="serviceAccount:bookflow-mart-to-gcs@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/storage.objectCreator"

# 키 발급
gcloud iam service-accounts keys create key.json \
  --iam-account=bookflow-mart-to-gcs@${GCP_PROJECT_ID}.iam.gserviceaccount.com

# AWS Secrets Manager에 주입 (Phase 2에서 빈 시크릿이 이미 생성됨)
aws secretsmanager put-secret-value \
  --secret-id bookflow/gcp-sa-key \
  --secret-string file://key.json \
  --region ap-northeast-1

rm key.json
```

### Phase 5 — Glue Raw Jobs 실행 (ETL3 1단계)

Phase 3의 [8/8] Lambda invoke로 S3 Raw에 데이터가 쌓인 후 실행한다.

```bash
bash scripts/aws/daily/day06_0505_glue_raw.sh
```

실행되는 Glue Job:

| Job | 입력 | 출력 |
|-----|------|------|
| `bookflow-raw-pos-mart` | S3 Raw pos-events/ | S3 Mart pos_events/ |
| `bookflow-raw-sns-mart` | S3 Raw sns/ | S3 Mart sns_mentions/ |
| `bookflow-raw-aladin-mart` | S3 Raw aladin/ | S3 Mart aladin_books/ |
| `bookflow-raw-event-mart` | S3 Raw events/ | S3 Mart calendar_events/ |

### Phase 6 — Glue 집계 Jobs + Step Functions ETL3 전체 검증

```bash
bash scripts/aws/daily/day07_0506_glue_agg.sh
```

| Job | 역할 |
|-----|------|
| `bookflow-sales-daily-agg` | Mart → 일별 판매 집계 |
| `bookflow-features-build` | 집계 → ML feature 생성 |
| Step Functions ETL3 | 6개 Job 전체 오케스트레이션 실행 및 검증 |

### Phase 7 — E2E 검증

```bash
# ETL1: Kinesis → RDS sales_realtime · inventory
python etl/5_4/verify_e2e_etl1.py

# ETL2: S3 SNS → RDS spike_events
python etl/5_8/task7_etl2_external/verify_e2e_etl2.py

# GCS 복사 확인
gsutil ls gs://${GCS_STAGING}/mart/

# BigQuery 적재 확인
bq query --use_legacy_sql=false 'SELECT COUNT(*) as cnt FROM bookflow_dw.sales_fact'
bq query --use_legacy_sql=false 'SELECT COUNT(*) as cnt FROM bookflow_dw.books_static'
bq query --use_legacy_sql=false 'SELECT COUNT(*) as cnt FROM bookflow_dw.sns_mentions'
```

---

## 일정별 구현 내용

### 4/30

#### 알라딘 API 데이터 수집 `4_30/aladin_api/aladin_fetch.py`

```powershell
cd etl\4_30\aladin_api
py -m pip install -r requirements.txt
copy .env.example .env        # .env 열어서 ALADIN_TTB_KEY 입력

python aladin_fetch.py                                          # 로컬 저장만
python aladin_fetch.py --upload-s3 --bucket bookflow-raw-354493396671  # S3도 업로드
```

- 출력 파일: `output\aladin_YYYYMMDD_HHMMSS.csv` (우혁에게 전달)
- S3 경로: `s3://bookflow-raw-354493396671/aladin/year=2026/month=04/day=30/`

#### ECS 시뮬레이터 배포

`etl/4_30/task6_etl1_pos/` 파일은 **참고용**이다.
실제 배포 파일은 `ecs-sims/` 이며 `deploy-etl-infra.sh` [3/8] 단계에서 자동 처리된다.

#### Lambda 배포 (SAM) 2단계 구조

> `sam-template.yaml`의 모든 Lambda는 최초 배포 시 `InlineCode`(플레이스홀더)로 되어 있다.
> `deploy-etl-infra.sh` [5/8]이 `sam build` → `sam deploy`를 자동으로 처리한다.
>
> | 단계 | 명령 | 결과 |
> |------|------|------|
> | 1단계 (인프라) | `sam deploy` | EventBridge Rule, IAM Role, Kinesis ESM, API GW 생성. Lambda는 플레이스홀더 동작 |
> | 2단계 (실제 코드) | `sam build` → `sam deploy` | `lambdas/*/index.py` + 외부 라이브러리 패키징 후 Lambda 교체 |
>
> CloudWatch 로그에서 `placeholder` 문자열이 찍히면 2단계 미완료 상태다.

---

### 5/4

#### pos-ingestor Lambda `5_4/task6_etl1_pos/lambda_pos_ingestor/index.py`

- Kinesis ESM 트리거 → RDS `sales_realtime` INSERT + `inventory` UPDATE + Redis 무효화
- VPC 내부 실행 · batchItemFailures 패턴

#### ETL1 E2E 검증

```powershell
python etl\5_4\verify_e2e_etl1.py
```

---

### 5/6

#### spike-detect Lambda `5_6/task7_etl2_external/lambda_spike_detect/index.py`

- 10분 cron
- S3 Raw sns 최근 1시간 → isbn13별 집계 → Poisson Z-score ≥ 3.0 → RDS `spike_events`

#### [설계 결정] spike-detect → intervention-svc 연동 방식

아키텍처 도에는 `spike-detect → Internal ALB → intervention-svc` 직접 HTTP 호출로 표기되어 있으나,
실제 구현은 **RDS `spike_events` 테이블을 이벤트 큐로 사용하는 Pull 방식**으로 구현됨.

| 항목 | 현재 방식 (RDS 이벤트 테이블) | ALB 직접 호출 |
|------|-------------------------------|---------------|
| 결합도 | 느슨함 — Lambda와 Pod 독립 | 강함 — Pod 장애 시 Lambda 실패 |
| 신뢰성 | intervention-svc 재시작 중이어도 spike 유실 없음 | Pod 스케일 다운/재시작 타이밍에 호출 유실 가능 |
| ALB URL 관리 | 불필요 | K8s ALB Controller가 동적 생성 → Lambda env var에 주입 어려움 |
| 감지 지연 | intervention-svc 폴링 주기만큼 지연 | 즉시 |
| 감사 추적 | spike_events 테이블이 히스토리 + `is_resolved` 상태 관리 | 별도 로깅 없으면 유실 |
| 중복 처리 | `ON CONFLICT DO NOTHING`으로 Lambda 재시도 안전 | ALB 호출 중복 방지 로직 직접 구현 필요 |

**결론**: 자동 발주 기준이 "24시간 내 품절 예상"이므로 폴링 지연(수 분)은 비즈니스 영향 없음.
`spike_events` 테이블이 이벤트 큐 역할을 겸해 intervention-svc 배포 사이클과 완전히 독립되며,
`is_resolved` 플래그로 처리 상태 추적 및 재처리가 가능한 현재 방식이 이 시스템에 더 적합하다.

> Internal ALB는 K8s ALB Controller가 Ingress YAML 기반으로 동적 생성하므로 CFN/SAM 배포 시점에
> URL이 확정되지 않아 Lambda env var 주입이 구조적으로 어렵다는 점도 직접 호출을 선택하지 않은 이유.

#### Glue pos_etl `5_6/task8_etl3_mart/glue_pos_etl/pos_etl.py`

- Raw `pos-events/` (GZIP JSON) → Mart `pos_events/` (Parquet · 파티션: `sale_date`)
- 스키마: `tx_id`, `isbn13`, `qty`, `unit_price`, `total_price`, `channel`, `location_id`, `ts`

---

### 5/8

#### ETL2 E2E 검증

```powershell
python etl\5_8\task7_etl2_external\verify_e2e_etl2.py
```

#### Glue aladin_etl `5_8/task8_etl3_mart/glue_aladin_etl/aladin_etl.py`

- Raw `aladin/` (GZIP NDJSON) → Mart `aladin_books/` (Parquet)
- SCD Type-1: isbn13 기준 최신 `synced_at` 유지

---

### 5/11

#### Glue event_etl `5_11/task8_etl3_mart/glue_event_etl/event_etl.py`

- Raw `events/{event_type}/` → Mart `calendar_events/` (파티션: `event_type`)
- 4종 UNION: `book_fair`, `holiday`, `publisher_promo`, `author_signing`

#### Glue sns_agg `5_11/task8_etl3_mart/glue_sns_agg/sns_agg.py`

- Raw `sns/` → Mart `sns_mentions/` (파티션: `mention_date`)
- `mention_count ≥ 10` → `is_spike_seed = True`

---

## 데이터 흐름

```
[알라딘 API]
    └─ aladin_fetch.py (로컬) / Lambda aladin-sync (매일 13:10 KST)
       └─ S3 Raw aladin/
          └─ [Glue] aladin_etl.py → S3 Mart aladin_books/ (Parquet)
                                         │
[공공 API]                               │
    └─ Lambda event-sync (매일 13:10 KST)│
       └─ S3 Raw events/                 │
          └─ [Glue] event_etl.py → S3 Mart calendar_events/ (Parquet)
                                         │
[SNS 합성]                               │  ← mart-to-gcs Lambda
    └─ Lambda sns-gen (10분 cron)        │     (S3 ObjectCreated → GCS 복사)
       └─ S3 Raw sns/                    ▼
          ├─ [Glue] sns_agg.py → S3 Mart sns_mentions/ (Parquet)
          │                          GCS staging 버킷
          └─ Lambda spike-detect         │
               (10분 cron)              │  Eventarc (GCS finalize)
               → RDS spike_events        ▼
                                    Google Workflows gcs-router
[ECS 시뮬레이터]                         │
    ├─ ECS online-sim  → Kinesis         ▼
    └─ ECS offline-sim → Kinesis    bq-load Cloud Function
          ├─ Firehose → S3 Raw pos-events/    │
          │    └─ [Glue] pos_etl.py           ▼
          │         → S3 Mart pos_events/  BigQuery bookflow_dw
          └─ Lambda pos-ingestor              (sales_fact · books_static ·
               → RDS sales_realtime + inventory  features · sns_mentions)
               → Redis stock:{isbn13}:{loc} 무효화

                    ↑ ETL3 Step Functions가 위 Glue Job 6개 오케스트레이션
```

### Mart → BigQuery 파이프라인

| 단계 | 구성요소 | 위치 |
|------|----------|------|
| ① Glue ETL | S3 Raw → S3 Mart Parquet 변환 | `etl/5_6~5_11/` Glue 스크립트 |
| ② S3 → GCS 복사 | `mart-to-gcs` Lambda (S3 ObjectCreated 트리거) | `infra/aws/99-serverless/lambdas/mart-to-gcs/` |
| ③ GCS 도착 감지 | Eventarc `google.cloud.storage.object.v1.finalized` | `infra/gcp/99-content/eventarc.tf` |
| ④ 워크플로 라우팅 | Google Workflows `gcs-router` | `infra/gcp/99-content/workflow.tf` |
| ⑤ BigQuery 적재 | `bq-load` Cloud Function (Parquet → BQ Load Job) | `infra/gcp/99-content/functions/bq-load/` |

**GCS 경로 → BigQuery 테이블 매핑**:

| GCS 경로 | BigQuery 테이블 |
|----------|----------------|
| `mart/pos_events/` | `sales_fact` |
| `mart/aladin_books/` | `books_static` |
| `mart/calendar_events/` | `features` |
| `mart/sns_mentions/` | `sns_mentions` |

---

## ETL3 관련 IaC 구현 현황

| IaC 파일 | 구현 내용 |
|----------|----------|
| `infra/aws/00-foundation/s3.yaml` | `MartBucket` `EventBridgeEnabled: true` |
| `infra/aws/00-foundation/secrets.yaml` | `bookflow/gcp-sa-key` 시크릿 리소스 |
| `infra/aws/99-serverless/sam-template.yaml` | `mart-to-gcs` Lambda + EventBridgeRule + `GcsStagingBucket` 파라미터 |
| `infra/aws/99-serverless/lambdas/mart-to-gcs/` | Lambda 실제 코드 + requirements.txt |
| `infra/gcp/99-content/functions/bq-load/` | bq-load Cloud Function 실제 구현 |

---

## 배포 후 자동 실행 스케줄

| Lambda | 주기 | 역할 |
|--------|------|------|
| `aladin-sync` | 매일 13:10 KST | 알라딘 API → S3 Raw/aladin/ |
| `event-sync` | 매일 13:10 KST | 공공 API → S3 Raw/events/ |
| `sns-gen` | 10분마다 | SNS 합성 → S3 Raw/sns/ |
| `spike-detect` | 10분마다 | Z-score 분석 → RDS spike_events |
| `pos-ingestor` | Kinesis 이벤트 시 | POS 판매 → RDS sales_realtime |
| `mart-to-gcs` | Glue Parquet 생성 시 | S3 Mart → GCS staging → BigQuery |
| `forecast-trigger` | 매일 13:10 KST | Step Functions ETL3 파이프라인 트리거 |

---

## 환경변수 요약

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `ALADIN_TTB_KEY` | 알라딘 API TTBKey | (필수) |
| `AWS_REGION` | AWS 리전 | `ap-northeast-1` |
| `GCP_PROJECT_ID` | GCP 프로젝트 ID | `project-8ab6bf05-54d2-4f5d-b8d` |
| `GCS_STAGING_BUCKET` | GCS staging 버킷명 | `{GCP_PROJECT_ID}-bookflow-staging` |
| `RAW_BUCKET` | S3 Raw 버킷명 | `bookflow-raw-354493396671` |
| `KINESIS_STREAM_NAME` | Kinesis 스트림명 | `bookflow-pos-events` |

---

## 우혁에게 전달할 데이터

알라딘 API 수집 후 생성된 CSV 파일:

```
etl\4_30\aladin_api\output\aladin_20260430_HHMMSS.csv
```

S3 업로드된 경우 경로 공유:
```
s3://bookflow-raw-354493396671/aladin/year=2026/month=04/day=30/
```
