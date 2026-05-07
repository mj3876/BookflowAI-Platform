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
    ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET", "catalog_database"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

SOURCE = f"s3://{args['RAW_BUCKET']}/pos-events/"
TARGET = f"s3://{args['MART_BUCKET']}/pos_events/"

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
    .partitionBy("sale_date")
    .parquet(TARGET)
)

print(f"[raw_pos_mart] source={SOURCE} target={TARGET} rows={df.count()}")
job.commit()
