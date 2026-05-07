"""
raw_sns_mart · Glue ETL Job
S3 Raw sns (GZIP NDJSON) → S3 Mart Parquet (partitioned by mention_date)
Job bookmark enabled →   
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    IntegerType,
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

SOURCE = f"s3://{args['RAW_BUCKET']}/sns/"
TARGET = f"s3://{args['MART_BUCKET']}/sns_mentions/"

SNS_SCHEMA = StructType([
    StructField("isbn13",        StringType(),  False),
    StructField("platform",      StringType(),  True),
    StructField("content",       StringType(),  True),
    StructField("sentiment",     StringType(),  True),
    StructField("mention_count", IntegerType(), True),
    StructField("is_spike_seed", BooleanType(), True),
    StructField("collected_at",  StringType(),  True),
    StructField("is_synthetic",  BooleanType(), True),
])

df = (
    spark.read
    .option("compression", "gzip")
    .option("recursiveFileLookup", "true")
    .schema(SNS_SCHEMA)
    .json(SOURCE)
)

df = (
    df
    .withColumn("created_at",   F.to_timestamp("collected_at"))
    .withColumn("mention_date", F.to_date("collected_at"))
    .filter(F.col("isbn13").isNotNull())
)

(
    df.write
    .mode("append")
    .partitionBy("mention_date")
    .parquet(TARGET)
)

print(f"[raw_sns_mart] source={SOURCE} target={TARGET} rows={df.count()}")
job.commit()
