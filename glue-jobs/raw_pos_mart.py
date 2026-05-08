"""
raw_pos_mart · Glue ETL Job
S3 Raw pos-events (Firehose GZIP) → S3 Mart Parquet (partitioned by date)
Job bookmark enabled →   
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import (
    IntegerType,
    LongType,
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

SOURCE = f"s3://{args['RAW_BUCKET']}/pos-events/"
TARGET = f"s3://{args['MART_BUCKET']}/mart/sales_fact/{_batch_id}/"

# ECS sim (online-sim / offline-sim) actual output schema
POS_SCHEMA = StructType([
    StructField("tx_id",       StringType(),  False),
    StructField("isbn13",      StringType(),  False),
    StructField("qty",         IntegerType(), False),
    StructField("unit_price",  IntegerType(), False),
    StructField("total_price", LongType(),    False),
    StructField("channel",     StringType(),  True),
    StructField("location_id", IntegerType(), True),
    StructField("ts",          StringType(),  True),
])

df = (
    spark.read
    .option("compression", "gzip")
    .schema(POS_SCHEMA)
    .json(SOURCE)
)

df = (
    df
    .withColumn("ts",        F.to_timestamp("ts"))
    .withColumn("sale_date", F.to_date("ts"))
    .withColumn("sale_hour", F.hour("ts"))
    .filter(F.col("tx_id").isNotNull() & F.col("isbn13").isNotNull())
    .dropDuplicates(["tx_id"])
)

(
    df.write
    .mode("overwrite")
    .parquet(TARGET)
)

row_count = df.count()
print(f"[raw_pos_mart] source={SOURCE} target={TARGET} rows={row_count}")

# BigQuery 적재 (google-cloud-bigquery)
import boto3, json
from google.oauth2 import service_account
from google.cloud import bigquery as bq

_sm  = boto3.client("secretsmanager")
_key = json.loads(_sm.get_secret_value(SecretId=args["GCP_SECRET_ID"])["SecretString"])
_creds = service_account.Credentials.from_service_account_info(_key)
_bq    = bq.Client(project=_key["project_id"], credentials=_creds)

_table_id = f"{_key['project_id']}.{args['BQ_TABLE']}"

# 날짜/타임스탬프 → string 변환 (Arrow 타입 오류 방지)
_df_bq = df.withColumn("sale_date", F.col("sale_date").cast("string")) \
            .withColumn("ts",        F.col("ts").cast("string"))
_pdf = _df_bq.toPandas()

# BQ 테이블 스키마 가져와서 컬럼 정렬
# - BQ에만 있는 컬럼 → None 추가
# - DataFrame에만 있는 컬럼 → 제거
_bq_table  = _bq.get_table(_table_id)
_bq_fields = [f.name for f in _bq_table.schema]
for _col in _bq_fields:
    if _col not in _pdf.columns:
        _pdf[_col] = None          # BQ 스키마에 있지만 DataFrame에 없는 컬럼 채우기
_pdf = _pdf[_bq_fields]            # BQ 스키마 순서대로 정렬 (여분 컬럼 제거)

_job_config = bq.LoadJobConfig(write_disposition="WRITE_APPEND", schema=_bq_table.schema)
_bq.load_table_from_dataframe(_pdf, _table_id, job_config=_job_config).result()
print(f"[raw_pos_mart] BigQuery {_table_id} 적재 완료 rows={row_count}")

job.commit()
