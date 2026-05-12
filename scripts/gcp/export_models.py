"""BQML 모델을 GCS로 export하고 핵심 테이블을 backup-494808으로 복사"""
from google.cloud import bigquery

SRC_PROJ = "project-8ab6bf05-54d2-4f5d-b8d"
DST_PROJ = "backup-494808"
DATASET  = "bookflow_dw"
LOCATION = "asia-northeast1"
BUCKET   = f"gs://{SRC_PROJ}-bookflow-models"

src_client = bigquery.Client(project=SRC_PROJ, location=LOCATION)
dst_client = bigquery.Client(project=DST_PROJ, location=LOCATION)

# 1. Create dataset in backup project
print("[1/3] backup-494808.bookflow_dw 데이터셋 생성")
ds_ref = bigquery.Dataset(f"{DST_PROJ}.{DATASET}")
ds_ref.location = LOCATION
dst_client.create_dataset(ds_ref, exists_ok=True)
print("  완료")

# 2. Export BQML models to GCS
print("\n[2/3] BQML 모델 GCS 익스포트")
models = ["bookflow_existing_books_forecast", "bookflow_new_books_forecast"]
for m in models:
    uri = f"{BUCKET}/backup/{m}/"
    print(f"  {m} -> {uri}")
    sql = f"EXPORT MODEL `{SRC_PROJ}.{DATASET}.{m}` OPTIONS(URI = '{uri}')"
    src_client.query(sql, location=LOCATION).result()
    print("    완료")

# 3. Copy key tables to backup project
print("\n[3/3] 핵심 테이블 복사")
tables = [
    "books_static",
    "locations_static",
    "store_location_map",
    "new_book_training_dataset",
    "new_book_forecast",
]
for tbl in tables:
    print(f"  {tbl} ...")
    try:
        job = src_client.copy_table(
            f"{SRC_PROJ}.{DATASET}.{tbl}",
            f"{DST_PROJ}.{DATASET}.{tbl}",
            job_config=bigquery.CopyJobConfig(write_disposition="WRITE_TRUNCATE"),
        )
        job.result()
        print("    완료")
    except Exception as e:
        print(f"    스킵 (오류: {e})")

print("\n백업 완료")
