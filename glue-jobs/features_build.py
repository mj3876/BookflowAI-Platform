"""
features_build · Glue ETL Job
Mart 4 (sales_daily · sns_mentions · aladin_books · calendar_events) JOIN
→ Vertex AI  feature vector  → S3 Mart features/
Step Functions ETL3   
"""
import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql import Window

args = getResolvedOptions(
    sys.argv,
    ["JOB_NAME", "MART_BUCKET", "catalog_database"],
)

sc    = SparkContext()
glue  = GlueContext(sc)
spark = glue.spark_session
job   = Job(glue)
job.init(args["JOB_NAME"], args)

MART = f"s3://{args['MART_BUCKET']}"

# ──   ──────────────────────────────────────────────────────────────
sales   = spark.read.parquet(f"{MART}/sales_daily/")
sns     = spark.read.parquet(f"{MART}/sns_mentions/")
aladin  = spark.read.parquet(f"{MART}/aladin_books/")
events  = spark.read.parquet(f"{MART}/calendar_events/")

# ── SNS   (isbn13 × date) ─────────────────────────────────────────
sns_daily = (
    sns
    .groupBy("isbn13", F.col("mention_date").alias("date"))
    .agg(
        F.count("*").alias("sns_mention_cnt"),
        F.sum(F.when(F.col("sentiment") == "positive", 1).otherwise(0)).alias("sns_pos_cnt"),
        F.sum(F.when(F.col("sentiment") == "negative", 1).otherwise(0)).alias("sns_neg_cnt"),
        F.sum(F.col("is_spike_seed").cast("int")).alias("sns_spike_cnt"),
    )
)

# ──  /  (date ) ──────────────────────────────────────────
# event_type: "holiday" (lowercase, from event-sync lambda)
# start_date is already DateType from raw_event_mart
holiday = (
    events
    .filter(F.col("event_type") == "holiday")
    .select(
        F.col("start_date").alias("date"),
        F.lit(True).alias("is_holiday"),
        F.lit(1.0).alias("season_idx"),
    )
    .dropDuplicates(["date"])
)

# ──     (duration_days ) ───────────────────────────
# duration_days derived from start_date/end_date (raw schema has no duration_days field)
book_fair = (
    events
    .filter(F.col("event_type") == "book_fair")
    .select(
        F.col("start_date").alias("fair_start"),
        (F.datediff(F.col("end_date"), F.col("start_date")) + 1).alias("duration_days"),
    )
)
#     explosion 
book_fair_dates = (
    book_fair
    .withColumn("day_offset", F.explode(F.sequence(F.lit(0), F.col("duration_days") - 1)))
    .withColumn("fair_date", F.date_add("fair_start", F.col("day_offset")))
    .select(F.col("fair_date").alias("date"), F.lit(1).alias("is_book_fair"))
    .dropDuplicates(["date"])
)

# ── 14 rolling  (isbn13 × location_id) ──────────────────────────────
w14 = (
    Window
    .partitionBy("isbn13", "location_id")
    .orderBy(F.col("date").cast("timestamp").cast("long"))
    .rangeBetween(-14 * 86400, 0)
)

sales_rolling = (
    sales
    .withColumn("rolling_14d_qty",     F.sum("total_qty").over(w14))
    .withColumn("rolling_14d_revenue", F.sum("total_revenue").over(w14))
)

# ──    (isbn13  LEFT JOIN) ──────────────────────────────
aladin_static = aladin.select("isbn13", "price", "rating", "category")

# ──  feature  ─────────────────────────────────────────────────────
features = (
    sales_rolling
    .join(sns_daily,     on=["isbn13", "date"], how="left")
    .join(holiday,       on="date",             how="left")
    .join(book_fair_dates, on="date",           how="left")
    .join(aladin_static, on="isbn13",           how="left")
    .withColumn("is_holiday",   F.coalesce(F.col("is_holiday"),   F.lit(False)))
    .withColumn("is_book_fair", F.coalesce(F.col("is_book_fair"), F.lit(0)))
    .withColumn("season_idx",   F.coalesce(F.col("season_idx"),   F.lit(1.0)))
    .withColumn("sns_mention_cnt",  F.coalesce(F.col("sns_mention_cnt"),  F.lit(0)))
    .withColumn("sns_pos_cnt",      F.coalesce(F.col("sns_pos_cnt"),      F.lit(0)))
    .withColumn("sns_neg_cnt",      F.coalesce(F.col("sns_neg_cnt"),      F.lit(0)))
    .withColumn("sns_spike_cnt",    F.coalesce(F.col("sns_spike_cnt"),    F.lit(0)))
    .withColumn("built_at", F.current_timestamp())
    .select(
        "date", "isbn13", "location_id", "channel",
        "total_qty", "total_revenue", "tx_count",
        "rolling_14d_qty", "rolling_14d_revenue",
        "sns_mention_cnt", "sns_pos_cnt", "sns_neg_cnt", "sns_spike_cnt",
        "is_holiday", "is_book_fair", "season_idx",
        "price", "rating", "category",
        "built_at",
    )
)

TARGET = f"{MART}/features/"
(
    features.write
    .mode("overwrite")
    .partitionBy("date")
    .parquet(TARGET)
)

print(f"[features_build] target={TARGET} rows={features.count()}")
job.commit()
