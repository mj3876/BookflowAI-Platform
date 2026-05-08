"""
raw_aladin_mart · Glue ETL Job
S3 Raw aladin (GZIP NDJSON) → S3 Mart Parquet (SCD Type1 · isbn13  )
Job bookmark enabled →    ·  mart UNION DISTINCT

[에러 원인 및 수정 이력]
에러: An error occurred while calling o143.parquet. File not present on S3
발생 위치: deduped.write.mode("overwrite").parquet(TARGET) 실행 시점
파일: glue-jobs/raw_aladin_mart.py

원인 - Spark lazy evaluation:
  spark.read.parquet(TARGET) 는 호출 즉시 S3를 읽지 않고 실행 계획만 생성(lazy).
  unionByName() 도 마찬가지로 lazy.
  따라서 기존 try/except 블록 안에서는 S3 파일 접근이 일어나지 않아 예외가 잡히지 않음.
  실제 S3 파일 읽기는 try 블록 밖의 .write.parquet() 액션이 트리거될 때 비로소 발생.
  이 시점에 mart/aladin_books/ 안의 특정 parquet 파일이 없거나 깨져 있으면
  try/except 바깥에서 예외가 터지므로 Job이 FAILED 처리됨.

수정: try 블록 안에서 .cache() + .count() 로 eager evaluation 강제
  .count() 가 실제 S3 파일을 읽는 액션이므로,
  파일이 없을 경우 예외가 try 블록 안에서 발생 → except 로 fallback.
  .cache() 는 count() 이후 write 시 중복 S3 스캔 방지.
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import Window, functions as F
from pyspark.sql.types import (
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET", "catalog_database", "GCP_SECRET_ID", "BQ_TABLE"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

from datetime import datetime, timezone
_batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

SOURCE      = f"s3://{args['RAW_BUCKET']}/aladin/"
_INTERNAL   = f"s3://{args['MART_BUCKET']}/aladin_books/"   # SCD 내부용
TARGET      = f"s3://{args['MART_BUCKET']}/mart/books_static/{_batch_id}/"

SCHEMA = StructType([
    StructField("isbn13",      StringType(),  False),
    StructField("title",       StringType(),  True),
    StructField("author",      StringType(),  True),
    StructField("publisher",   StringType(),  True),
    StructField("pub_date",    StringType(),  True),
    StructField("price",         IntegerType(), True),
    StructField("cover_url",     StringType(),  True),
    StructField("query_type",    StringType(),  True),
    StructField("category_id",   IntegerType(), True),
    StructField("category_name", StringType(),  True),
    StructField("rating",        DoubleType(),  True),
    StructField("synced_at",   StringType(),  True),
])

incoming = (
    spark.read
    .option("compression", "gzip")
    .option("recursiveFileLookup", "true")
    .schema(SCHEMA)
    .json(SOURCE)
    .withColumnRenamed("category_name", "category")
    .withColumn("synced_at", F.to_timestamp("synced_at"))
    .filter(F.col("isbn13").isNotNull())
)

# SCD Type1: 내부 경로(_INTERNAL)에서 기존 데이터 읽어 병합
try:
    existing = spark.read.parquet(_INTERNAL)
    existing.cache()
    existing.count()
    combined = existing.unionByName(incoming, allowMissingColumns=True)
except Exception:
    combined = incoming

# isbn13   synced_at   (SCD Type1)
window = Window.partitionBy("isbn13").orderBy(F.col("synced_at").desc())
deduped = (
    combined
    .withColumn("_rn", F.row_number().over(window))
    .filter(F.col("_rn") == 1)
    .drop("_rn")
)

# 내부 경로에 SCD 결과 저장 (다음 실행 때 기존 데이터로 사용)
deduped.cache()
deduped.write.mode("overwrite").parquet(_INTERNAL)

# GCS export 경로에 저장 → EventBridge → mart-to-gcs Lambda → GCS → BigQuery
deduped.write.mode("overwrite").parquet(TARGET)

book_count = deduped.count()
print(f"[raw_aladin_mart] source={SOURCE} target={TARGET} books={book_count}")

# BigQuery 적재 (google-cloud-bigquery)
import boto3, json
from google.oauth2 import service_account
from google.cloud import bigquery as bq

_sm  = boto3.client("secretsmanager")
_key = json.loads(_sm.get_secret_value(SecretId=args["GCP_SECRET_ID"])["SecretString"])
_creds = service_account.Credentials.from_service_account_info(_key)
_bq    = bq.Client(project=_key["project_id"], credentials=_creds)

_table_id = f"{_key['project_id']}.{args['BQ_TABLE']}"

# 타임스탬프 → string 변환 (Arrow 타입 오류 방지)
_df_bq = deduped.withColumn("synced_at", F.col("synced_at").cast("string"))
_pdf = _df_bq.toPandas()

# BQ 테이블 스키마 가져와서 컬럼 정렬
# - BQ에만 있는 컬럼 → None 추가
# - DataFrame에만 있는 컬럼 → 제거
_bq_table  = _bq.get_table(_table_id)
_bq_fields = [f.name for f in _bq_table.schema]
for _col in _bq_fields:
    if _col not in _pdf.columns:
        _pdf[_col] = None
_pdf = _pdf[_bq_fields]

_job_config = bq.LoadJobConfig(write_disposition="WRITE_APPEND", schema=_bq_table.schema)
_bq.load_table_from_dataframe(_pdf, _table_id, job_config=_job_config).result()
print(f"[raw_aladin_mart] BigQuery {_table_id} 적재 완료 books={book_count}")
job.commit()
