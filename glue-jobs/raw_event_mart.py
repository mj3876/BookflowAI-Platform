"""
raw_event_mart - Glue ETL Job
S3 Raw events (GZIP NDJSON) -> S3 Mart Parquet (partitioned by event_type)
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    StringType,
    StructField,
    StructType,
)

args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET", "catalog_database"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

SOURCE = f"s3://{args['RAW_BUCKET']}/events/"
TARGET = f"s3://{args['MART_BUCKET']}/calendar_events/"

SCHEMA = StructType([
    StructField("event_id",    StringType(),              True),
    StructField("event_type",  StringType(),              False),
    StructField("title",       StringType(),              True),
    StructField("start_date",  StringType(),              True),
    StructField("end_date",    StringType(),              True),
    StructField("location",    StringType(),              True),
    StructField("isbn13_list", ArrayType(StringType()),   True),
    StructField("synced_at",   StringType(),              True),
])

df = (
    spark.read
    .option("compression", "gzip")
    .option("recursiveFileLookup", "true")
    .schema(SCHEMA)
    .json(SOURCE)
    .withColumn("synced_at",  F.to_timestamp("synced_at"))
    .withColumn("start_date", F.to_date("start_date", "yyyy-MM-dd"))
    .withColumn("end_date",   F.to_date("end_date",   "yyyy-MM-dd"))
    .filter(F.col("event_type").isNotNull() & F.col("start_date").isNotNull())
    .dropDuplicates(["event_id"])
)

(
    df.write
    .mode("overwrite")
    .partitionBy("event_type")
    .parquet(TARGET)
)

print(f"[raw_event_mart] source={SOURCE} target={TARGET} rows={df.count()}")
job.commit()
