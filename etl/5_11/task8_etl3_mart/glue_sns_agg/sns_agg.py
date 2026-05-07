"""
[5/11~5/13] Task8 ETL3 · [Glue] sns_agg.py
S3 Raw sns/ (GZIP NDJSON) → S3 Mart sns_mentions/ (Parquet)
mention_count ≥ 10 → is_spike_seed = True
: mention_date

BookFlowAI-Apps/glue-jobs/raw-sns-mart/sns_agg.py 
Args: JOB_NAME, RAW_BUCKET, MART_BUCKET
: mention_id, isbn13, platform, mention_count, sentiment_score, collected_at
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args  = getResolvedOptions(sys.argv, ["JOB_NAME", "RAW_BUCKET", "MART_BUCKET"])
sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

RAW_PATH  = f"s3://{args['RAW_BUCKET']}/sns/"
MART_PATH = f"s3://{args['MART_BUCKET']}/sns_mentions/"

SPIKE_THRESHOLD = 10

df = (
    spark.read
    .option("recursiveFileLookup", "true")
    .json(RAW_PATH)
    .select(
        F.col("mention_id"),
        F.col("isbn13"),
        F.col("platform"),
        F.col("mention_count").cast("int"),
        F.col("sentiment_score").cast("double"),
        F.col("collected_at"),
    )
    .filter(F.col("isbn13").rlike(r"^\d{13}$"))
    .withColumn("mention_date",  F.to_date("collected_at"))
    .withColumn("is_spike_seed", F.col("mention_count") >= SPIKE_THRESHOLD)
    .dropDuplicates(["mention_id"])
)

df.write.mode("overwrite").partitionBy("mention_date").parquet(MART_PATH)

print(
    f"[sns_agg] raw={RAW_PATH} mart={MART_PATH} "
    f"rows={df.count()} spike_threshold={SPIKE_THRESHOLD}"
)
job.commit()
