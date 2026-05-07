"""
[5/11~5/12] Task8 ETL3 · [Glue] event_etl.py
S3 Raw events/ (GZIP NDJSON, 4 ) → S3 Mart calendar_events/ (Parquet)
: event_type
 : book_fair, holiday, publisher_promo, author_signing

BookFlowAI-Apps/glue-jobs/raw-event-mart/event_etl.py 
Args: JOB_NAME, RAW_BUCKET, MART_BUCKET
: event_id, event_type, title, start_date, end_date, event_location, isbn13_list, synced_at
"""
import sys
from functools import reduce

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import DataFrame
from pyspark.sql import functions as F

args  = getResolvedOptions(sys.argv, ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET"])
sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

RAW_PATH  = f"s3://{args['RAW_BUCKET']}/events/"
MART_PATH = f"s3://{args['MART_BUCKET']}/calendar_events/"

EVENT_TYPES = ["book_fair", "holiday", "publisher_promo", "author_signing"]

frames = []
for etype in EVENT_TYPES:
    try:
        df_e = (
            spark.read
            .option("recursiveFileLookup", "true")
            .json(f"{RAW_PATH}{etype}/")
            .withColumn("event_type", F.lit(etype))
        )
        frames.append(df_e)
    except Exception:
        pass

if not frames:
    job.commit()
    sys.exit(0)

df_all = reduce(lambda a, b: a.unionByName(b, allowMissingColumns=True), frames)

df_clean = (
    df_all
    .select(
        F.col("event_id"),
        F.col("event_type"),
        F.col("title"),
        F.col("start_date"),
        F.col("end_date"),
        F.col("location").alias("event_location"),
        F.col("isbn13_list"),
        F.col("synced_at"),
    )
    .dropDuplicates(["event_id"])
)

df_clean.write.mode("overwrite").partitionBy("event_type").parquet(MART_PATH)

print(f"[event_etl] raw={RAW_PATH} mart={MART_PATH} rows={df_clean.count()}")
job.commit()
