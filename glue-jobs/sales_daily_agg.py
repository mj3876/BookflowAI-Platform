"""
sales_daily_agg · Glue ETL Job
Mart pos_events →    (isbn13 × location_id × channel × date)
Step Functions ETL3  2 · raw_pos_mart   
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "MART_BUCKET", "catalog_database"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

POS_PATH    = f"s3://{args['MART_BUCKET']}/pos_events/"
TARGET_PATH = f"s3://{args['MART_BUCKET']}/sales_daily/"

pos = spark.read.parquet(POS_PATH)

daily = (
    pos
    .groupBy(
        F.col("sale_date").alias("date"),
        "isbn13",
        "location_id",
        "channel",
    )
    .agg(
        F.sum("qty").alias("total_qty"),
        F.sum("total_price").alias("total_revenue"),
        F.count("tx_id").alias("tx_count"),
        F.max("ts").alias("last_tx_at"),
    )
    .withColumn("aggregated_at", F.current_timestamp())
)

(
    daily.write
    .mode("overwrite")
    .partitionBy("date")
    .parquet(TARGET_PATH)
)

print(f"[sales_daily_agg] target={TARGET_PATH} rows={daily.count()}")
job.commit()
