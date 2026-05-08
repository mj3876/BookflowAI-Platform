"""
bq-load Cloud Function (GCP)

[호출 경로]
  S3 Mart Parquet
    → mart-to-gcs Lambda (AWS)  ← S3 ObjectCreated → EventBridge → Lambda
    → GCS staging 버킷
    → Eventarc (google.cloud.storage.object.v1.finalized)
    → Google Workflows gcs-router  ← workflow.tf
    → 이 함수 (bq-load)
    → BigQuery bookflow_dw 테이블

[Request body - workflow.tf gcs-router 가 전송]
  {
    "bucket":      "GCS 버킷명",
    "object":      "mart/pos_events/sale_date=2026-05-01/part-0.parquet",
    "dataset_id":  "bookflow_dw",
    "project_id":  "gcp-project-id",
    "bq_location": "asia-northeast1"
  }

[GCS 경로 → BigQuery 테이블 매핑]
  mart/pos_events/       → sales_fact      (Glue pos_etl.py 출력)
  mart/aladin_books/     → books_static    (Glue aladin_etl.py 출력)
  mart/calendar_events/  → features        (Glue event_etl.py 출력)
  mart/sns_mentions/     → sns_mentions    (Glue sns_agg.py 출력)
  위 4개 외: 폴더명 = 테이블명 (직접 매핑)
  → 커스텀 매핑: env var BOOKFLOW_TABLE_MAP (JSON) 으로 override 가능

[환경변수 - locals.tf function_specs.bq_load.env]
  BOOKFLOW_DATASET_ID        = "bookflow_dw"
  BOOKFLOW_BQ_LOCATION       = "asia-northeast1"
  BOOKFLOW_STAGING_BUCKET    = "{project_id}-bookflow-staging"
  BOOKFLOW_WRITE_DISPOSITION = "WRITE_APPEND"
  BOOKFLOW_TABLE_MAP         = (선택) JSON 문자열로 매핑 override
"""
import json
import os
import re

import functions_framework
from google.cloud import bigquery

# ── 기본 GCS 경로 → BigQuery 테이블명 매핑 ─────────────────────────────────
# Glue 스크립트가 S3 Mart에 쓰는 폴더명과 BigQuery 테이블명이 다른 경우 여기서 변환
_DEFAULT_TABLE_MAP: dict[str, str] = {
    "pos_events":      "sales_fact",     # Glue pos_etl.py → mart/pos_events/
    "sales_daily":     "sales_fact",
    "aladin_books":    "books_static",   # Glue aladin_etl.py → mart/aladin_books/
    "books_static":    "books_static",
    "calendar_events": "features",       # Glue event_etl.py → mart/calendar_events/
    "features":        "features",
    "inventory_daily": "inventory_daily",
    "locations_static": "locations_static",
    "store_location_map": "store_location_map",
    "sns_mentions":    "sns_mentions",   # Glue sns_agg.py → mart/sns_mentions/ (동일)
}

# Hive 파티션 컬럼 패턴: "key=value" 형태 → 이 앞 경로가 source_uri_prefix
# 예) mart/pos_events/sale_date=2026-05-01/part.parquet
#      ↑ 이 정규식으로 "mart/pos_events/" 를 추출
_HIVE_PART_RE = re.compile(r"/[^/]+=")


def _load_table_map() -> dict[str, str]:
    """
    환경변수 BOOKFLOW_TABLE_MAP (JSON) 이 있으면 기본 매핑을 merge하여 반환.
    예) BOOKFLOW_TABLE_MAP='{"pos_events": "sales_transactions"}'
    """
    raw = os.environ.get("BOOKFLOW_TABLE_MAP", "")
    aliases = os.environ.get("BOOKFLOW_LOAD_TABLE_ALIASES", "")
    try:
        table_map = {**_DEFAULT_TABLE_MAP, **json.loads(raw)} if raw else dict(_DEFAULT_TABLE_MAP)
        for alias in aliases.split(","):
            if not alias or ":" not in alias:
                continue
            source_name, table_name = alias.split(":", 1)
            table_map[source_name.strip()] = table_name.strip()
        return table_map
    except (json.JSONDecodeError, ValueError):
        return _DEFAULT_TABLE_MAP


def _parse_path(object_name: str) -> tuple[str, str]:
    """
    GCS object 경로에서 (폴더명, Hive 파티션 전 prefix) 추출.

    예) "mart/pos_events/sale_date=2026-05-01/part-0.parquet"
         → folder="pos_events"  base="mart/pos_events"

    folder → _DEFAULT_TABLE_MAP 으로 BigQuery 테이블명 결정
    base   → source_uri_prefix (Hive 파티션 자동 감지 기준점)
    """
    parts = object_name.strip("/").split("/")
    try:
        idx    = parts.index("mart")
        folder = parts[idx + 1]
        base   = "/".join(parts[: idx + 2])   # "mart/pos_events"
    except (ValueError, IndexError):
        folder = parts[0] if parts else ""
        # mart/ 세그먼트가 없으면 Hive 파티션 시작 전까지를 base 로 추정
        match = _HIVE_PART_RE.search(object_name)
        base  = object_name[: match.start()] if match else folder

    return folder, base


@functions_framework.http
def handler(request):
    """
    Google Workflows gcs-router 에서 HTTP POST 로 호출됨.
    요청 body: {bucket, object, dataset_id, project_id, bq_location}
    """
    body = request.get_json(silent=True) or {}

    # ── 요청 파라미터 파싱 (body 우선, 없으면 env var fallback) ──────────
    bucket      = body.get("bucket")      or os.environ.get("BOOKFLOW_STAGING_BUCKET", "")
    object_name = body.get("object", "")
    dataset_id  = body.get("dataset_id")  or os.environ.get("BOOKFLOW_DATASET_ID",  "bookflow_dw")
    project_id  = body.get("project_id")  or os.environ.get("BOOKFLOW_PROJECT_ID",  "")
    bq_location = body.get("bq_location") or os.environ.get("BOOKFLOW_BQ_LOCATION", "asia-northeast1")
    # WRITE_APPEND: 파티션에 새 행 추가 / WRITE_TRUNCATE: 테이블 전체 교체
    write_disp  = os.environ.get("BOOKFLOW_WRITE_DISPOSITION", "WRITE_APPEND")

    if not bucket or not object_name:
        return (
            json.dumps({"error": "bucket and object are required"}),
            400,
            {"Content-Type": "application/json"},
        )

    # .parquet 가 아닌 파일(_SUCCESS, .crc 등 Glue 임시 파일)은 무시
    if not object_name.endswith(".parquet"):
        return (
            json.dumps({"status": "skipped", "reason": "not parquet"}),
            200,
            {"Content-Type": "application/json"},
        )

    # ── GCS 경로 → BigQuery 테이블명 결정 ────────────────────────────────
    table_map          = _load_table_map()
    folder, base_path  = _parse_path(object_name)
    table_name         = table_map.get(folder, folder)   # 매핑 없으면 폴더명 그대로 사용

    if not table_name:
        return (
            json.dumps({"error": f"cannot infer table from path: {object_name}"}),
            400,
            {"Content-Type": "application/json"},
        )

    # ── GCS URI: 같은 파티션 디렉토리의 모든 Parquet 와일드카드 로드 ──────
    # 이유: 한 Glue 파티션에 여러 part-*.parquet 파일이 생성될 수 있음
    # 예) gs://bucket/mart/pos_events/sale_date=2026-05-01/*.parquet
    dir_path  = object_name.rsplit("/", 1)[0]
    gcs_uri   = f"gs://{bucket}/{dir_path}/*.parquet"
    table_ref = f"{project_id}.{dataset_id}.{table_name}"

    client = bigquery.Client(project=project_id, location=bq_location)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=write_disp,
        # BigQuery 테이블 스키마는 bookflow_bigquery_ddl.sql 로 미리 정의됨 → autodetect 불필요
        autodetect=False,
        # Hive 파티션 자동 감지: source_uri_prefix 기준으로 key=value 컬럼을 BigQuery 파티션으로 변환
        # 예) sale_date=2026-05-01/ → PARTITION BY sale_date
        hive_partitioning_options=bigquery.HivePartitioningOptions(
            mode="AUTO",
            source_uri_prefix=f"gs://{bucket}/{base_path}/",
            require_partition_filter=False,
        ),
    )

    # BigQuery Load Job 실행 (동기 대기 · locals.tf timeout=540s 이내)
    load_job = client.load_table_from_uri(gcs_uri, table_ref, job_config=job_config)
    load_job.result()

    row_count = client.get_table(table_ref).num_rows
    print(f"[bq-load] {gcs_uri} → {table_ref} | disposition={write_disp} total_rows={row_count}")

    return (
        json.dumps({"table": table_ref, "gcs_uri": gcs_uri,
                    "status": "ok", "rows": row_count}),
        200,
        {"Content-Type": "application/json"},
    )
