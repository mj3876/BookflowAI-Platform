"""
mart-to-gcs Lambda

[역할]
  AWS Glue ETL 이 S3 Mart 버킷에 Parquet 파일을 쓸 때마다 GCS staging 버킷으로 복사.
  이 복사가 완료되면 GCP 쪽에서 자동으로 파이프라인이 이어진다.

[전체 흐름]
  Glue ETL (pos_etl / aladin_etl / event_etl / sns_agg)
    → S3 Mart 버킷 (mart/...)
    → S3 ObjectCreated 이벤트
    → EventBridge Rule (sam-template.yaml MartToGcsFn S3EventBridge)
    → 이 Lambda (mart-to-gcs)
    → GCS staging 버킷 (같은 경로로 복사)
    → Eventarc (google.cloud.storage.object.v1.finalized)
    → Google Workflows gcs-router
    → bq-load Cloud Function
    → BigQuery bookflow_dw

[트리거 이벤트 포맷 - EventBridge S3 ObjectCreated]
  {
    "source": "aws.s3",
    "detail-type": "Object Created",
    "detail": {
      "bucket": {"name": "bookflow-mart-354493396671"},
      "object": {"key": "mart/pos_events/sale_date=2026-05-01/part-0.parquet"}
    }
  }
  ※ EventBridge 이벤트는 Records[] 구조가 아님 (S3 직접 트리거와 다름)
  ※ S3 직접 트리거 포맷(Records[])도 fallback 으로 지원

[필요한 Secrets Manager 시크릿]
  bookflow/gcp-sa-key  (secrets.yaml GcpSaKey 로 생성)
  → GCP 서비스 계정 JSON 키 전체를 문자열로 저장
  → 예시:
    {
      "type": "service_account",
      "project_id": "your-gcp-project",
      "private_key_id": "...",
      "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...",
      "client_email": "bookflow-mart-to-gcs@your-gcp-project.iam.gserviceaccount.com",
      "token_uri": "https://oauth2.googleapis.com/token"
    }
  → 실제 키 발급: gcloud iam service-accounts keys create key.json \
        --iam-account=bookflow-mart-to-gcs@{project}.iam.gserviceaccount.com
    이후 key.json 내용을 시크릿 값으로 업데이트

[환경변수 - sam-template.yaml MartToGcsFn]
  GCS_STAGING_BUCKET = "{gcp-project-id}-bookflow-staging"
    → Terraform gcs.tf 의 google_storage_bucket.staging.name 과 일치해야 함
"""
import json
import os

import boto3
from google.cloud import storage
from google.oauth2 import service_account

REGION     = os.environ.get("AWS_REGION", "ap-northeast-1")
GCS_BUCKET = os.environ.get("GCS_STAGING_BUCKET", "")


def _gcs_client() -> storage.Client:
    """
    Secrets Manager 에서 GCP 서비스 계정 JSON 키를 읽어 GCS 클라이언트 초기화.
    키는 secrets.yaml GcpSaKey 리소스로 생성된 bookflow/gcp-sa-key 시크릿에 저장됨.
    """
    sm  = boto3.client("secretsmanager", region_name=REGION)
    raw = sm.get_secret_value(SecretId="bookflow/gcp-sa-key")["SecretString"]
    key = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        key,
        # GCS 읽기/쓰기에 필요한 최소 scope
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return storage.Client(project=key["project_id"], credentials=creds)


def _extract_s3_records(event: dict) -> list[tuple[str, str]]:
    """
    이벤트 소스에 따라 (bucket, key) 목록 반환.

    ① EventBridge S3 이벤트 (sam-template.yaml S3EventBridge 트리거):
       event["source"] == "aws.s3"
       event["detail"]["bucket"]["name"]
       event["detail"]["object"]["key"]

    ② S3 직접 트리거 (fallback · 수동 테스트 시):
       event["Records"][]["s3"]["bucket"]["name"]
       event["Records"][]["s3"]["object"]["key"]
    """
    # ① EventBridge 포맷
    if event.get("source") == "aws.s3" and "detail" in event:
        detail = event["detail"]
        bucket = detail.get("bucket", {}).get("name", "")
        key    = detail.get("object", {}).get("key", "")
        return [(bucket, key)] if bucket and key else []

    # ② S3 직접 트리거 포맷 (fallback)
    records = []
    for rec in event.get("Records", []):
        bucket = rec.get("s3", {}).get("bucket", {}).get("name", "")
        # S3 직접 이벤트는 key 가 URL 인코딩될 수 있음 (공백→%20 등)
        key = rec.get("s3", {}).get("object", {}).get("key", "")
        if bucket and key:
            records.append((bucket, key))
    return records


def lambda_handler(event, context):
    s3_records = _extract_s3_records(event)

    if not s3_records:
        print("[mart-to-gcs] no valid S3 records in event")
        return {"copied": 0, "failures": []}

    s3  = boto3.client("s3", region_name=REGION)
    gcs = _gcs_client()
    gcs_bucket_client = gcs.bucket(GCS_BUCKET)

    copied   = []
    failures = []

    for src_bucket, src_key in s3_records:
        # mart/ 프리픽스가 아닌 파일(Glue _SUCCESS, .crc 임시파일 등)은 스킵
        if not src_key.startswith("mart/") or not src_key.endswith(".parquet"):
            print(f"[mart-to-gcs] skipped (not mart/*.parquet): {src_key}")
            continue

        try:
            # S3에서 Parquet 파일 다운로드 → GCS에 동일 경로로 업로드
            # GCS 경로 = S3 key 와 동일 (mart/pos_events/sale_date=.../part-0.parquet)
            # → bq-load Cloud Function 이 이 경로에서 테이블명을 파싱함
            obj  = s3.get_object(Bucket=src_bucket, Key=src_key)
            data = obj["Body"].read()

            blob = gcs_bucket_client.blob(src_key)
            blob.upload_from_string(data, content_type="application/octet-stream")

            print(f"[mart-to-gcs] s3://{src_bucket}/{src_key} → gs://{GCS_BUCKET}/{src_key}")
            copied.append(src_key)

        except Exception as e:
            # 개별 파일 실패 시 나머지 파일 처리는 계속 진행
            print(f"[mart-to-gcs] ERROR {src_key}: {e}")
            failures.append(src_key)

    print(f"[mart-to-gcs] done · copied={len(copied)} failures={len(failures)}")
    return {"copied": len(copied), "failures": failures}
