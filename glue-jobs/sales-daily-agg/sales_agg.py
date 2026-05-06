"""
BOOKFLOW V6.2 Glue ETL: sales-daily-agg

Aggregates S3 Mart POS events into a daily sales fact shape compatible with the
GCP BigQuery sales fact table. All environment-specific values are supplied as
Glue job arguments; this script intentionally does not embed project IDs,
bucket names, dataset names, table names, account IDs, or ARNs.

Expected deployment object:
  s3://<GLUE_SCRIPTS_BUCKET>/sales-daily-agg/sales_agg.py
"""

import json
import sys
from datetime import datetime, timezone

import boto3
from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType


REQUIRED_ARGS = [
    "JOB_NAME",
    "MART_BUCKET",
    "catalog_database",
]

OPTIONAL_DEFAULTS = {
    "POS_EVENTS_PREFIX": "pos_events",
    "SALES_DAILY_PREFIX": "sales_daily",
    "SALES_FACT_PREFIX": "sales_fact",
    "GLUE_SALES_DAILY_TABLE": "sales_daily",
    "GLUE_SALES_FACT_TABLE": "sales_fact",
    "GCP_PROJECT_ID": "",
    "BIGQUERY_DATASET": "",
    "BIGQUERY_SALES_FACT_TABLE": "",
    "WRITE_MODE": "overwrite",
    "BQ_MAPPING_PREFIX": "",
    "STORE_WAREHOUSE_MAPPING_JSON": "{}",
}


def _resolve_args():
    resolved = getResolvedOptions(sys.argv, REQUIRED_ARGS)

    present = {
        token[2:].replace("-", "_")
        for token in sys.argv
        if token.startswith("--") and len(token) > 2
    }
    optional_keys = [key for key in OPTIONAL_DEFAULTS if key in present]
    if optional_keys:
        resolved.update(getResolvedOptions(sys.argv, optional_keys))

    for key, default in OPTIONAL_DEFAULTS.items():
        resolved.setdefault(key, default)

    return resolved


def _s3_uri(bucket, prefix):
    clean_prefix = prefix.strip("/")
    return f"s3://{bucket}/{clean_prefix}/"


def _put_mapping_manifest(args, source_uri, daily_uri, sales_fact_uri, output_rows):
    prefix = args["BQ_MAPPING_PREFIX"].strip("/")
    if not prefix:
        return

    bigquery_mapping = None
    if all(
        args[key]
        for key in ["GCP_PROJECT_ID", "BIGQUERY_DATASET", "BIGQUERY_SALES_FACT_TABLE"]
    ):
        bigquery_mapping = {
            "project_id": args["GCP_PROJECT_ID"],
            "dataset": args["BIGQUERY_DATASET"],
            "table": args["BIGQUERY_SALES_FACT_TABLE"],
            "source_format": "PARQUET",
            "write_disposition": "WRITE_TRUNCATE",
        }

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "s3_uri": source_uri,
            "glue_database": args["catalog_database"],
            "glue_table": args["GLUE_SALES_DAILY_TABLE"],
        },
        "outputs": {
            "sales_daily": {
                "s3_uri": daily_uri,
                "glue_database": args["catalog_database"],
                "glue_table": args["GLUE_SALES_DAILY_TABLE"],
            },
            "sales_fact": {
                "s3_uri": sales_fact_uri,
                "glue_database": args["catalog_database"],
                "glue_table": args["GLUE_SALES_FACT_TABLE"],
                "bigquery": bigquery_mapping,
            },
        },
        "output_rows": output_rows,
    }

    key = f"{prefix}/sales_agg_bq_mapping.json"
    boto3.client("s3").put_object(
        Bucket=args["MART_BUCKET"],
        Key=key,
        Body=json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
        ContentType="application/json",
    )


args = _resolve_args()

sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)

source_uri = _s3_uri(args["MART_BUCKET"], args["POS_EVENTS_PREFIX"])
sales_daily_uri = _s3_uri(args["MART_BUCKET"], args["SALES_DAILY_PREFIX"])
sales_fact_uri = _s3_uri(args["MART_BUCKET"], args["SALES_FACT_PREFIX"])

warehouse_mapping = json.loads(args["STORE_WAREHOUSE_MAPPING_JSON"])
warehouse_mapping_expr = F.create_map(
    *[
        item
        for key, value in warehouse_mapping.items()
        for item in (F.lit(str(key)), F.lit(int(value)))
    ]
) if warehouse_mapping else None

raw_pos_events = (
    spark.read
    .option("recursiveFileLookup", "true")
    .parquet(source_uri)
)

if "sale_date" in raw_pos_events.columns:
    sale_date_col = F.to_date(F.col("sale_date"))
elif "ts" in raw_pos_events.columns:
    sale_date_col = F.to_date(F.col("ts"))
else:
    raise ValueError("pos_events input must contain either sale_date or ts")

pos_events = (
    raw_pos_events
    .select(
        sale_date_col.alias("sale_date"),
        F.col("isbn13").cast("string").alias("isbn13"),
        F.col("location_id").cast("int").alias("store_id"),
        F.col("channel").cast("string").alias("channel"),
        F.col("qty").cast("long").alias("qty"),
        F.col("total_price").cast("decimal(18,2)").alias("total_price"),
        F.col("tx_id").cast("string").alias("tx_id"),
    )
    .filter(F.col("sale_date").isNotNull())
    .filter(F.col("isbn13").rlike(r"^\d{13}$"))
    .filter(F.col("store_id").isNotNull())
    .filter(F.col("qty") > 0)
)

sales_fact = (
    pos_events
    .groupBy("sale_date", "isbn13", "store_id", "channel")
    .agg(
        F.sum("qty").cast("long").alias("qty_sold"),
        F.sum("total_price").cast("decimal(18,2)").alias("revenue"),
        F.countDistinct("tx_id").cast("long").alias("tx_count"),
    )
    .withColumn(
        "avg_price",
        F.when(F.col("qty_sold") > 0, F.col("revenue") / F.col("qty_sold"))
        .otherwise(F.lit(None).cast("decimal(18,2)"))
        .cast("decimal(18,2)"),
    )
)

if warehouse_mapping_expr is not None:
    sales_fact = sales_fact.withColumn(
        "wh_id",
        warehouse_mapping_expr[F.col("store_id").cast(StringType())].cast(IntegerType()),
    )
else:
    sales_fact = sales_fact.withColumn("wh_id", F.lit(None).cast(IntegerType()))

sales_fact = sales_fact.select(
    "sale_date",
    "isbn13",
    "store_id",
    "wh_id",
    "channel",
    "qty_sold",
    "revenue",
    "avg_price",
    "tx_count",
)

write_mode = args["WRITE_MODE"]

(
    sales_fact.write
    .mode(write_mode)
    .partitionBy("sale_date")
    .parquet(sales_daily_uri)
)

if sales_fact_uri != sales_daily_uri:
    (
        sales_fact.write
        .mode(write_mode)
        .partitionBy("sale_date")
        .parquet(sales_fact_uri)
    )

spark.sql(f"CREATE DATABASE IF NOT EXISTS `{args['catalog_database']}`")
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{args['catalog_database']}`.`{args['GLUE_SALES_DAILY_TABLE']}`
    USING PARQUET
    LOCATION '{sales_daily_uri}'
    """
)
spark.sql(
    f"""
    CREATE TABLE IF NOT EXISTS `{args['catalog_database']}`.`{args['GLUE_SALES_FACT_TABLE']}`
    USING PARQUET
    LOCATION '{sales_fact_uri}'
    """
)

row_count = sales_fact.count()
_put_mapping_manifest(args, source_uri, sales_daily_uri, sales_fact_uri, row_count)

print(
    "[sales_agg] "
    f"source={source_uri} "
    f"sales_daily={sales_daily_uri} "
    f"sales_fact={sales_fact_uri} "
    f"bq_mapping_configured={bool(args['GCP_PROJECT_ID'] and args['BIGQUERY_DATASET'] and args['BIGQUERY_SALES_FACT_TABLE'])} "
    f"rows={row_count}"
)

job.commit()
