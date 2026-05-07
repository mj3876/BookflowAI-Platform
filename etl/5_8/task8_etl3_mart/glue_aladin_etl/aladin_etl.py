"""
[5/8~5/12] Task8 ETL3 · [Glue] aladin_etl.py
S3 Raw aladin/ (GZIP NDJSON) → S3 Mart aladin_books/ (Parquet)
SCD Type-1: isbn13   synced_at   ()

BookFlowAI-Apps/glue-jobs/raw-aladin-mart/aladin_etl.py 
Args: JOB_NAME, RAW_BUCKET, MART_BUCKET
: isbn13, title, author, publisher, pub_date, category_id, category_name,
      price, cover_url, sales_point, stock_status, synced_at
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import Window
from pyspark.sql import functions as F

args  = getResolvedOptions(sys.argv, ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET"])
sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

RAW_PATH  = f"s3://{args['RAW_BUCKET']}/aladin/"
MART_PATH = f"s3://{args['MART_BUCKET']}/aladin_books/"

df = (
    spark.read
    .option("recursiveFileLookup", "true")
    .json(RAW_PATH)
    .select(
        F.col("isbn13"),
        F.col("title"),
        F.col("author"),
        F.col("publisher"),
        F.col("pub_date"),
        F.col("category_id").cast("int"),
        F.col("category_name"),
        F.col("price").cast("long"),
        F.col("cover_url"),
        F.col("sales_point").cast("long"),
        F.col("stock_status"),
        F.col("synced_at"),
    )
    .filter(F.col("isbn13").rlike(r"^\d{13}$"))
)

# SCD Type-1: isbn13  synced_at  
win = Window.partitionBy("isbn13").orderBy(F.desc("synced_at"))
df_deduped = (
    df
    .withColumn("rn", F.row_number().over(win))
    .filter(F.col("rn") == 1)
    .drop("rn")
)

df_deduped.write.mode("overwrite").parquet(MART_PATH)

print(f"[aladin_etl] raw={RAW_PATH} mart={MART_PATH} books={df_deduped.count()}")
job.commit()
