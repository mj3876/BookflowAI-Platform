"""
mart_table_gcs · Glue ETL Job
S3 Mart 단일 테이블 → GCS dual-write
TABLE_NAME arg로 inventory_daily / locations_static / store_location_map 구분
"""
import json
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "MART_BUCKET", "gcp_secret_arn", "GCS_BUCKET", "TABLE_NAME"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

# GCS connector: SA key → /tmp/sa-key.json → Spark conf
_sa_key_json = json.loads(
    boto3.client("secretsmanager").get_secret_value(
        SecretId=args["gcp_secret_arn"]
    )["SecretString"]
)
_sa_key_path = "/tmp/sa-key.json"
with open(_sa_key_path, "w") as _f:
    json.dump(_sa_key_json, _f)

spark.conf.set("spark.hadoop.google.cloud.auth.service.account.enable", "true")
spark.conf.set("spark.hadoop.google.cloud.auth.service.account.json.keyfile", _sa_key_path)
spark.conf.set("spark.sql.parquet.enableVectorizedReader",        "false")
spark.conf.set("spark.sql.parquet.datetimeRebaseModeInRead",      "CORRECTED")
spark.conf.set("spark.sql.parquet.datetimeRebaseModeInWrite",     "CORRECTED")
spark.conf.set("spark.sql.legacy.parquet.int96RebaseModeInRead",  "CORRECTED")
spark.conf.set("spark.sql.legacy.parquet.int96RebaseModeInWrite", "CORRECTED")

TABLE      = args["TABLE_NAME"]
S3_SOURCE  = f"s3://{args['MART_BUCKET']}/mart/{TABLE}/"
GCS_TARGET = f"gs://{args['GCS_BUCKET']}/mart/{TABLE}/"

df = spark.read.parquet(S3_SOURCE)
print(f"[mart_table_gcs] table={TABLE} rows={df.count()}")

df.write.mode("overwrite").parquet(GCS_TARGET)
print(f"[mart_table_gcs] s3={S3_SOURCE} → gcs={GCS_TARGET}")

job.commit()
