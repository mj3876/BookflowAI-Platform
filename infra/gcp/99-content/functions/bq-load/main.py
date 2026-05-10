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
import uuid

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

_BQ_CAST_TYPES = {
    "BOOL": "BOOL",
    "BOOLEAN": "BOOL",
    "BYTES": "BYTES",
    "DATE": "DATE",
    "DATETIME": "DATETIME",
    "FLOAT": "FLOAT64",
    "FLOAT64": "FLOAT64",
    "INTEGER": "INT64",
    "INT64": "INT64",
    "NUMERIC": "NUMERIC",
    "STRING": "STRING",
    "TIME": "TIME",
    "TIMESTAMP": "TIMESTAMP",
}


def _load_table_map() -> dict[str, str]:
    """
    환경변수 BOOKFLOW_TABLE_MAP (JSON) 이 있으면 기본 매핑을 merge하여 반환.
    BOOKFLOW_LOAD_TABLES (쉼표 구분 테이블명 목록) 에 없는 테이블은 적재 거부.
    training_dataset 은 BigQuery 파이프라인이 학습 직전에 JOIN으로 생성하므로
    이 함수의 허용 목록에 포함되지 않음.
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
    except (json.JSONDecodeError, ValueError):
        table_map = dict(_DEFAULT_TABLE_MAP)

    # BOOKFLOW_LOAD_TABLES 에 정의된 테이블만 허용
    # 미설정 시 전체 허용 (하위 호환)
    allowed_raw = os.environ.get("BOOKFLOW_LOAD_TABLES", "")
    if allowed_raw:
        allowed = {t.strip() for t in allowed_raw.split(",") if t.strip()}
        table_map = {src: tgt for src, tgt in table_map.items() if tgt in allowed}

    return table_map


def _load_column_aliases() -> dict[str, dict[str, str]]:
    raw = os.environ.get("BOOKFLOW_LOAD_COLUMN_ALIASES", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return {
            str(table_name): {
                str(target_column): str(source_column)
                for target_column, source_column in aliases.items()
            }
            for table_name, aliases in parsed.items()
            if isinstance(aliases, dict)
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        return {}


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


def _field_expr(
    table_name: str,
    field: bigquery.SchemaField,
    source_columns: set[str],
    column_aliases: dict[str, dict[str, str]],
) -> str:
    field_name = field.name.replace("`", "")
    field_type = _BQ_CAST_TYPES.get(field.field_type.upper(), field.field_type.upper())
    if field_name in source_columns:
        return f"SAFE_CAST(`{field_name}` AS {field_type}) AS `{field_name}`"
    source_name = column_aliases.get(table_name, {}).get(field_name)
    if source_name and source_name in source_columns:
        return f"SAFE_CAST(`{source_name}` AS {field_type}) AS `{field_name}`"
    if source_name and re.fullmatch(r"-?\d+(\.\d+)?", source_name):
        return f"SAFE_CAST({source_name} AS {field_type}) AS `{field_name}`"
    return f"CAST(NULL AS {field_type}) AS `{field_name}`"


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
    column_aliases     = _load_column_aliases()
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

    load_config_kwargs = {
        "source_format": bigquery.SourceFormat.PARQUET,
        "write_disposition": write_disp,
        # BigQuery 테이블 스키마는 bookflow_bigquery_ddl.sql 로 미리 정의됨 → autodetect 불필요
        "autodetect": False,
    }

    # Hive 파티션 자동 감지는 key=value 경로가 있는 객체에만 적용한다.
    # 예) mart/pos_events/sale_date=2026-05-01/part.parquet → sale_date 컬럼 로드
    if _HIVE_PART_RE.search(object_name):
        hive_options = bigquery.HivePartitioningOptions()
        hive_options.mode = "AUTO"
        hive_options.source_uri_prefix = f"gs://{bucket}/{base_path}/"
        hive_options.require_partition_filter = False
        load_config_kwargs["hive_partitioning"] = hive_options

    temp_table_ref = f"{project_id}.{dataset_id}.__load_{table_name}_{uuid.uuid4().hex[:12]}"
    load_config_kwargs["autodetect"] = True
    load_config_kwargs["write_disposition"] = bigquery.WriteDisposition.WRITE_TRUNCATE
    job_config = bigquery.LoadJobConfig(**load_config_kwargs)

    # Parquet 원본은 임시 테이블에 먼저 autodetect로 적재한 뒤 대상 테이블 스키마에 맞춰 INSERT한다.
    # pandas/pyarrow Parquet의 nullable/date 타입 차이가 대상 테이블 직접 적재를 막는 경우를 피하기 위함이다.
    load_job = client.load_table_from_uri(gcs_uri, temp_table_ref, job_config=job_config)
    load_job.result()

    target_table = client.get_table(table_ref)
    temp_table = client.get_table(temp_table_ref)
    source_columns = {field.name for field in temp_table.schema}
    target_columns = [field.name.replace("`", "") for field in target_table.schema]
    select_exprs = [
        _field_expr(table_name, field, source_columns, column_aliases)
        for field in target_table.schema
    ]

    insert_sql = f"""
    INSERT INTO `{table_ref}` ({", ".join(f"`{column}`" for column in target_columns)})
    SELECT {", ".join(select_exprs)}
    FROM `{temp_table_ref}`
    """
    query_job = client.query(insert_sql)
    query_job.result()
    client.delete_table(temp_table_ref, not_found_ok=True)

    row_count = client.get_table(table_ref).num_rows
    print(f"[bq-load] {gcs_uri} → {table_ref} | disposition={write_disp} total_rows={row_count}")

    return (
        json.dumps({"table": table_ref, "gcs_uri": gcs_uri,
                    "status": "ok", "rows": row_count}),
        200,
        {"Content-Type": "application/json"},
    )
