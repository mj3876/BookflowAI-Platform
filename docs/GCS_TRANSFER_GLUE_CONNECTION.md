# GCS 데이터 전송 — mart-to-gcs Lambda → Glue Connection 전환

## 배경

기존에는 S3 Mart에 파일이 적재되면 EventBridge → mart-to-gcs Lambda가 트리거되어 GCS로 전송했다.
이 방식을 제거하고 `features_build` Glue Job이 VPN 경유 Glue Connection으로 GCS에 직접 dual-write하는 방식으로 전환했다.

---

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `infra/aws/99-glue/glue-catalog.yaml` | Connection JDBC → NETWORK 교체, FeaturesBuildJob에 GCS 설정 추가 |
| `infra/aws/99-serverless/sam-template.yaml` | MartToGcsFn Lambda 및 관련 리소스 전체 제거 |
| `infra/aws/10-network-core/route53.yaml` | googleapis.com PHZ 추가 (VPN 강제 경유용 DNS override) |
| `scripts/aws/ops/cross-cloud.sh` | tgw-vpc-routes에 GcpVpcCidr=10.50.0.0/24 파라미터 전달 버그 수정 |
| `scripts/aws/lib/common.sh` | load_env()에 .env.local 자동 로드 추가 |
| `scripts/aws/daily/day06_0505_glue_raw.sh` | Step 8 검증 로직을 Glue Connection 방식에 맞게 업데이트 |
| `BookFlowAI-Apps/glue-jobs/features-build/features_build.py` | GCS dual-write 코드 추가 |

---

## 변경 상세

### 1. `infra/aws/99-glue/glue-catalog.yaml` — Connection 타입 교체

JDBC(BigQuery 전용) → NETWORK 타입으로 교체하고 `PhysicalConnectionRequirements` 추가.
NETWORK 타입이어야 Glue Worker가 VPC 내 ENI를 얻어 TGW → VPN 경로를 탈 수 있다.

```yaml
# 기존
BigQueryConnection:
  ConnectionType: JDBC
  ConnectionProperties:
    JDBC_CONNECTION_URL: jdbc:bigquery://...
    USERNAME: placeholder
    PASSWORD: placeholder

# 변경 후
GcsVpnConnection:
  Type: AWS::Glue::Connection
  Properties:
    CatalogId: !Ref AWS::AccountId
    ConnectionInput:
      Name: !Sub ${ProjectName}-gcs-vpn
      ConnectionType: NETWORK          # JDBC 아님 · VPC ENI 생성용
      Description: GCS via TGW VPN (bookflow-ai private subnet ENI)
      PhysicalConnectionRequirements:  # 이게 있어야 ENI 생성 → VPC 라우팅 탐
        SubnetId: !ImportValue
          Fn::Sub: ${ProjectName}-subnet-bookflow-ai-private-az1
        SecurityGroupIdList:
          - !ImportValue
            Fn::Sub: ${ProjectName}-lambda-vpc-sg-id
        AvailabilityZone: ap-northeast-1a
```

FeaturesBuildJob에 Glue Connection 연결 및 GCS 관련 인수 추가:

```yaml
FeaturesBuildJob:
  Connections:
    Connections:
      - !Sub ${ProjectName}-gcs-vpn      # NETWORK connection → ENI 생성
  DefaultArguments:
    '--extra-jars': s3://<glue-scripts>/jars/gcs-connector-hadoop3-latest.jar
    '--conf': >-
      spark.hadoop.fs.gs.impl=com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem
      --conf spark.hadoop.fs.AbstractFileSystem.gs.impl=com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS
    '--GCS_BUCKET': ''                   # 배포 시 실제 버킷명 전달
    '--gcp_secret_arn': !ImportValue     # GCP SA key (기존 secret 재사용)
        Fn::Sub: ${ProjectName}-secrets-glue-gcp-arn
```

---

### 2. `BookFlowAI-Apps/glue-jobs/features-build/features_build.py` — GCS dual-write 추가

BigQuery JDBC write를 제거하고 GCS Parquet dual-write로 교체:

```python
# 기존 (BigQuery JDBC write — 제거)
df.write \
  .format("bigquery") \
  .option("table", "project.dataset.features") \
  .save()

# 변경 후 — S3 primary write
TARGET = f"{MART}/mart/features/{_batch_id}/"
features.write.mode("overwrite").parquet(TARGET)

# GCS dual-write (Glue Connection ENI → TGW → VPN → GCP PSC → GCS)
_gcs_bucket = args.get("GCS_BUCKET", "")
if _gcs_bucket:
    GCS_TARGET = f"gs://{_gcs_bucket}/features/{_batch_id}/"
    features.write.mode("overwrite").parquet(GCS_TARGET)
```

GCS 인증은 Secrets Manager에서 SA key JSON을 가져와 `/tmp/sa-key.json`으로 저장 후 Spark 설정에 주입:

```python
_sa_key_json = json.loads(
    boto3.client("secretsmanager").get_secret_value(
        SecretId=args["gcp_secret_arn"]
    )["SecretString"]
)
with open("/tmp/sa-key.json", "w") as f:
    json.dump(_sa_key_json, f)

spark.conf.set("spark.hadoop.google.cloud.auth.service.account.enable", "true")
spark.conf.set("spark.hadoop.google.cloud.auth.service.account.json.keyfile", "/tmp/sa-key.json")
```

---

### 3. `infra/aws/99-serverless/sam-template.yaml` — mart-to-gcs Lambda 제거

제거된 리소스:
- `GcsStagingBucket` 파라미터
- `MartToGcsFn` Lambda 함수 (VPC 연결, IAM 정책, S3 EventBridge 트리거 포함)
- `MartToGcsArn` Output

```yaml
# 제거됨: S3 EventBridge 트리거 (mart/ 프리픽스 파일 생성 시 Lambda 자동 호출)
Events:
  S3EventBridge:
    Type: EventBridgeRule
    Properties:
      Pattern:
        detail:
          object:
            key:
              - prefix: mart/

# 제거됨: Output
MartToGcsArn:
  Value: !GetAtt MartToGcsFn.Arn
  Export: { Name: !Sub '${ProjectName}-lambda-mart-to-gcs-arn' }
```

---

### 4. `infra/aws/10-network-core/route53.yaml` — googleapis.com PHZ 추가

Glue Worker가 `gs://` 주소를 쓸 때 `storage.googleapis.com`을 공인 IP로 해석하면 VPN을 우회한다.
Route53 PHZ로 `storage.googleapis.com` → GCP PSC 엔드포인트 IP(`10.50.0.10`)로 강제해 반드시 VPN을 경유하게 한다.

```yaml
GoogleapisPrivateZone:
  Type: AWS::Route53::HostedZone
  Properties:
    Name: googleapis.com
    VPCs: [bookflow-ai, sales-data, egress, data, ansible]  # 5개 VPC 연결

StorageGoogleapisRecord:
  Type: AWS::Route53::RecordSet
  Properties:
    Name: storage.googleapis.com
    Type: A
    ResourceRecords: [10.50.0.10]   # GCP PSC endpoint IP

WildcardGoogleapisRecord:
  Type: AWS::Route53::RecordSet
  Properties:
    Name: '*.googleapis.com'
    Type: A
    ResourceRecords: [10.50.0.10]
```

---

### 5. `scripts/aws/ops/cross-cloud.sh` — GcpVpcCidr 파라미터 버그 수정

`tgw-vpc-routes.yaml`의 `GcpVpcCidr` 기본값이 `192.168.10.0/24`인데 실제 GCP VPC CIDR은 `10.50.0.0/24`다.
파라미터를 명시적으로 전달하지 않으면 Glue Worker 트래픽이 TGW로 라우팅되지 않는다.

```bash
# 기존 — GcpVpcCidr 미전달 (기본값 192.168.10.0/24 사용)
bookflow-60-tgw-vpc-routes|$INFRA/60-network-cross-cloud/tgw-vpc-routes.yaml

# 수정 후 — 실제 GCP CIDR 전달
GCP_VPC_CIDR="${BOOKFLOW_GCP_VPC_CIDR:-10.50.0.0/24}"
bookflow-60-tgw-vpc-routes|$INFRA/60-network-cross-cloud/tgw-vpc-routes.yaml|GcpVpcCidr=$GCP_VPC_CIDR
```

---

### 6. `scripts/aws/lib/common.sh` — .env.local 자동 로드

`load_env()`가 `deploy.env`/`admin.env`만 읽고 `.env.local`은 무시했다.
GCP VPN IP, PSK 등 로컬 전용 환경변수를 매번 `export`해야 했던 문제를 해결했다.

```bash
# 추가된 코드
local local_env="$SCRIPTS_DIR/config/.env.local"
[ -f "$local_env" ] && { set -a; . "$local_env"; set +a; }
```

`.env.local` 형식 (`scripts/aws/config/.env.local`, git push 금지):

```bash
GCS_BUCKET=project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging
BOOKFLOW_GCP_VPN_GW_IP=34.157.75.103   # GCP HA VPN interface 0
BOOKFLOW_GCP_VPN_GW_IP_1=34.157.196.243 # GCP HA VPN interface 1 (참고용)
BOOKFLOW_GCP_VPN_PSK=bookflow_vpn_2026  # 하이픈 불가 · 언더스코어 사용
```

---

## 데이터 흐름 (변경 후)

```
[ETL3 Step Functions - 일 1회 18:24 KST]
         │
         ├─ raw_pos_mart    → s3://mart/mart/sales_fact/
         ├─ raw_sns_mart    → s3://mart/sns_mentions/
         ├─ raw_aladin_mart → s3://mart/aladin_books/
         ├─ raw_event_mart  → s3://mart/calendar_events/
         └─ sales_daily_agg → s3://mart/sales_daily/
                  │
                  ▼ (모두 features_build 입력)
         features_build (Glue Job)
           Glue Connection: bookflow-ai private subnet ENI
           → RT 10.50.0.0/24 → TGW → Site-to-Site VPN → GCP HA VPN
           → PSC endpoint 10.50.0.10 → storage.googleapis.com
                  │
                  ├─ S3: s3://mart/mart/features/{batch_id}/
                  └─ GCS: gs://bookflow-staging/features/{batch_id}/  ← Vertex AI 입력
```

---

## 배포 순서

```bash
# 1. VPN 터널 UP 확인 (터널 0, 1 모두 Status=UP이어야 함)
aws ec2 describe-vpn-connections \
  --filters "Name=tag:Name,Values=bookflow-vpn-gcp" \
  --query "VpnConnections[0].VgwTelemetry[].{IP:OutsideIpAddress,Status:Status}" \
  --output table --region ap-northeast-1

# 2. tgw-vpc-routes 스택 업데이트 (GcpVpcCidr 수정)
aws cloudformation update-stack \
  --stack-name bookflow-60-tgw-vpc-routes \
  --use-previous-template \
  --parameters ParameterKey=GcpVpcCidr,ParameterValue=10.50.0.0/24 \
  --region ap-northeast-1

# 3. route53 스택 업데이트 (googleapis.com PHZ 추가)
aws cloudformation update-stack \
  --stack-name bookflow-10-route53 \
  --template-body file://infra/aws/10-network-core/route53.yaml \
  --region ap-northeast-1

# 4. GCS connector JAR S3 업로드
SCRIPTS_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name bookflow-00-s3 \
  --query "Stacks[0].Outputs[?OutputKey=='GlueScriptsBucketName'].OutputValue" \
  --output text --region ap-northeast-1)
aws s3 cp gcs-connector-hadoop3-latest.jar \
  s3://${SCRIPTS_BUCKET}/jars/gcs-connector-hadoop3-latest.jar

# 5. glue-catalog 스택 배포 (GcsVpnConnection + FeaturesBuildJob)
aws cloudformation deploy \
  --stack-name bookflow-99-glue \
  --template-file infra/aws/99-glue/glue-catalog.yaml \
  --capabilities CAPABILITY_NAMED_IAM --region ap-northeast-1

# 6. serverless 스택 재배포 (mart-to-gcs Lambda 제거 반영)
sam deploy --stack-name bookflow-99-serverless --region ap-northeast-1

# 7. features_build 수동 실행 테스트
aws glue start-job-run \
  --job-name bookflow-features-build \
  --arguments '{"--GCS_BUCKET":"project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging"}' \
  --region ap-northeast-1
```

---

## GCS로 전송되는 데이터

GCP(Vertex AI)는 `features/` 하나만 수신한다. 나머지 테이블은 features_build 내부에서 집계되어 포함된다.

| 테이블 | GCS 전송 | 포함 방식 |
|--------|---------|---------|
| `sns_mentions` | 직접 전송 안 함 | `sns_mention_cnt`, `sns_pos_cnt` 등으로 집계 후 features에 포함 |
| `calendar_events` | 직접 전송 안 함 | `is_holiday`, `is_book_fair`로 변환 후 features에 포함 |
| `sales_daily` | 직접 전송 안 함 | `rolling_14d_qty`, `total_qty` 등으로 집계 후 features에 포함 |
| `aladin_books` | 직접 전송 안 함 | `price`, `rating`, `category`로 조인 후 features에 포함 |
| `mart/features/` | **GCS 전송** | features_build Job이 직접 dual-write |
