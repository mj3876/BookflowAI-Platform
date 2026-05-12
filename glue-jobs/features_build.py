"""
features_build.py — BOOKFLOW Glue ETL · features 테이블 빌더 (방향 A: 단일 테이블 전략)

[방향 A 결정 사항]
  GCS로 가는 데이터는 features 하나뿐.
  features_build 안에서 inventory_daily, locations_static, store_location_map, sales_daily
  까지 읽어 feature vector에 통합한다.
  → GCP는 features 테이블 하나만으로 training / forecast 가능.

[출력 단위]  (feature_date × isbn13 × store_id)
[출력 경로]
  S3 Mart    : s3://{MART_BUCKET}/mart/features/feature_date=YYYY-MM-DD/
  GCS staging: gs://{GCS_BUCKET}/mart/features/feature_date=YYYY-MM-DD/
               └→ Eventarc → GCP Workflows → bq-load → BigQuery features 테이블

[입력 테이블 (S3 Mart)]
  mart/calendar_events/     event_date, is_holiday, holiday_name, season, event_nearby_days
  mart/sns_mentions/         mention_date, isbn13, mention_count
  mart/inventory_daily/      snapshot_date, isbn13, location_id, on_hand, reserved_qty, safety_stock
  mart/aladin_books/         isbn13, pub_date, category_id, author, publisher, ...  (없으면 books_static/ 시도)
  mart/locations_static/     location_id, location_type, wh_id, size, is_virtual
  mart/store_location_map/   store_id, location_id, inventory_location_id
  mart/sales_daily/          sale_date, isbn13, store_id, qty_sold, revenue, avg_price, tx_count

[출력 스키마]
  -- grain
  feature_date, isbn13, store_id
  -- 위치 (store_location_map + locations_static)
  location_id, inventory_location_id, location_type, wh_id, size, is_virtual
  -- 서적 속성 (aladin_books)
  category_id, category_name, publisher, author, price_standard, price_sales, price_tier,
  sales_point, item_page, author_past_books_count, author_debut_year, author_experience_years
  book_age_days, is_bestseller_flag
  -- 캘린더 (calendar_events)
  is_holiday, holiday_name, season, day_of_week, is_weekend, month, event_nearby_days
  -- SNS (sns_mentions, 7일 롤링)
  sns_mentions_1d, sns_mentions_7d
  -- 재고 (inventory_daily, 매장별 inventory_location_id 기준)
  on_hand, reserved_qty, safety_stock, on_hand_total, days_since_last_stockout
  -- 실적 레이블 (sales_daily)
  qty_sold, revenue, avg_price, tx_count

[Job 인수]
  --MART_BUCKET       S3 Mart 버킷명
  --GCS_BUCKET        GCS staging 버킷명
  --gcp_secret_arn    AWS Secrets Manager ARN (GCS 서비스 계정 key JSON)
  --catalog_database  Glue 카탈로그 DB명 (선택, 기본: bookflow_mart)

[GCS 연결 경로]
  bookflow-gcs-vpn Glue NETWORK Connection → ENI in bookflow-ai subnet
  → TGW → Site-to-Site VPN → GCP Cloud VPN → Private Service Connect → storage.googleapis.com

[전제 조건]
  s3://{GLUE_SCRIPTS_BUCKET}/jars/gcs-connector-hadoop3-latest.jar 업로드 필요
"""

import json
import sys

import boto3

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType, DateType, DoubleType, IntegerType, LongType, StringType,
)
from pyspark.sql.window import Window

_REGION = "ap-northeast-1"

# 서적 속성 컬럼명 후보 (aladin_books 소스에 따라 이름이 다를 수 있음)
_BOOK_COL_CANDIDATES = {
    "pub_date":                 ("pub_date", "publication_date", "pubDate"),
    "category_id":              ("category_id", "categoryId"),
    "category_name":            ("category_name", "categoryName", "category"),
    "publisher":                ("publisher",),
    "author":                   ("author",),
    "price_standard":           ("price_standard", "priceStandard", "standard_price"),
    "price_sales":              ("price_sales", "priceSales", "sales_price"),
    "price_tier":               ("price_tier", "priceTier"),
    "sales_point":              ("sales_point", "salesPoint"),
    "item_page":                ("item_page", "itemPage", "pages"),
    "is_bestseller_flag":       ("is_bestseller_flag", "bestseller_flag", "isBestseller"),
    "author_past_books_count":  ("author_past_books_count",),
    "author_debut_year":        ("author_debut_year",),
    "author_experience_years":  ("author_experience_years",),
}


# ── 인수 파싱 ──────────────────────────────────────────────────────────────────
def _get_args() -> dict:
    args = getResolvedOptions(
        sys.argv,
        ["JOB_NAME", "MART_BUCKET", "GCS_BUCKET", "gcp_secret_arn"],
    )
    if "catalog_database" not in args:
        args["catalog_database"] = "bookflow_mart"
    return args


# ── GCS 인증 설정 ──────────────────────────────────────────────────────────────
def _fetch_sa_key(secret_arn: str) -> dict:
    sm = boto3.client("secretsmanager", region_name=_REGION)
    return json.loads(sm.get_secret_value(SecretId=secret_arn)["SecretString"])


def _configure_gcs(spark, sc, key_json: dict) -> None:
    """GCS Hadoop Connector 인증 설정.

    spark.hadoop.* 설정은 Spark가 태스크 실행 시 executor Hadoop 설정으로 전파한다.
    반드시 첫 번째 Spark action 호출 전에 실행해야 executor 에 반영된다.
    """
    settings = {
        "fs.gs.impl":                                       "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem",
        "fs.AbstractFileSystem.gs.impl":                    "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS",
        "google.cloud.auth.service.account.enable":         "true",
        "google.cloud.auth.service.account.email":          key_json["client_email"],
        "google.cloud.auth.service.account.private.key.id": key_json["private_key_id"],
        "google.cloud.auth.service.account.private.key":    key_json["private_key"],
        "fs.gs.project.id":                                 key_json["project_id"],
    }
    for k, v in settings.items():
        spark.conf.set(f"spark.hadoop.{k}", v)
    hc = sc._jsc.hadoopConfiguration()
    for k, v in settings.items():
        hc.set(k, v)


# ── 읽기 헬퍼 ─────────────────────────────────────────────────────────────────
def _try_read(spark, path: str):
    try:
        df = spark.read.parquet(path)
        print(f"[features_build] read OK: {path}")
        return df
    except Exception as exc:
        print(f"[features_build] WARN: {path} 읽기 실패 — {exc}")
        return None


def _find_col(df, candidates: tuple):
    """df 에서 candidates 중 첫 번째로 존재하는 컬럼명 반환."""
    cols = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in cols:
            return cols[name.lower()]
    return None


# ── 피처 조립 ──────────────────────────────────────────────────────────────────
def build_features(spark, mart_bucket: str):  # noqa: C901
    base = f"s3://{mart_bucket}"

    # ── 1. calendar_events → 날짜별 캘린더 피처 ───────────────────────────────
    # raw_event_mart(Apps)는 mart/ 없이 {MART_BUCKET}/calendar_events/ 에 씀
    cal_raw = (
        _try_read(spark, f"{base}/mart/calendar_events/")
        or _try_read(spark, f"{base}/calendar_events/")
    )
    if cal_raw is None:
        print("[features_build] ERROR: calendar_events 없음 — 중단")
        return None

    date_col = _find_col(cal_raw, ("event_date", "start_date", "date", "feature_date"))
    if date_col is None:
        print(f"[features_build] ERROR: calendar_events 날짜 컬럼 없음 — 컬럼 목록: {cal_raw.columns}")
        return None
    cal_cols = set(c.lower() for c in cal_raw.columns)
    cal = (
        cal_raw
        .withColumn("feature_date",      F.to_date(F.col(date_col).cast("string")))
        .withColumn("is_holiday",        F.coalesce(F.col("is_holiday").cast(BooleanType())          if "is_holiday"        in cal_cols else F.lit(False), F.lit(False)))
        .withColumn("holiday_name",      F.coalesce(F.col("holiday_name").cast(StringType())         if "holiday_name"      in cal_cols else F.lit(""),    F.lit("")))
        .withColumn("season",            F.coalesce(F.col("season").cast(StringType())               if "season"            in cal_cols else F.lit(""),    F.lit("")))
        .withColumn("event_nearby_days", F.coalesce(F.col("event_nearby_days").cast(IntegerType())   if "event_nearby_days" in cal_cols else F.lit(0),     F.lit(0)))
        .withColumn("day_of_week",       F.dayofweek(F.col("feature_date")))   # 1=일 .. 7=토
        .withColumn("is_weekend",        F.dayofweek(F.col("feature_date")).isin(1, 7))
        .withColumn("month",             F.month(F.col("feature_date")))
        .select(
            "feature_date", "is_holiday", "holiday_name", "season",
            "day_of_week", "is_weekend", "month", "event_nearby_days",
        )
        .dropDuplicates(["feature_date"])
    )

    # ── 2. locations_static + store_location_map → 매장별 위치 정보 ───────────
    locs_raw = _try_read(spark, f"{base}/mart/locations_static/")
    slm_raw  = _try_read(spark, f"{base}/mart/store_location_map/")

    if slm_raw is not None:
        slm = (
            slm_raw
            .withColumn("store_id",              F.col("store_id").cast(IntegerType()))
            .withColumn("location_id",            F.col("location_id").cast(IntegerType()))
            .withColumn("inventory_location_id",  F.col("inventory_location_id").cast(IntegerType()))
            .select("store_id", "location_id", "inventory_location_id")
            .dropDuplicates(["store_id"])
        )
        if locs_raw is not None:
            locs = (
                locs_raw
                .withColumn("location_id",   F.col("location_id").cast(IntegerType()))
                .withColumn("location_type", F.col("location_type").cast(StringType()))
                .withColumn("wh_id",         F.col("wh_id").cast(StringType()))
                .withColumn("size",          F.col("size").cast(StringType()))
                .withColumn("is_virtual",    F.col("is_virtual").cast(BooleanType()))
                .select("location_id", "location_type", "wh_id", "size", "is_virtual")
            )
            stores = slm.join(locs, on="location_id", how="left")
        else:
            stores = (
                slm
                .withColumn("location_type", F.lit(""))
                .withColumn("wh_id",         F.lit(""))
                .withColumn("size",          F.lit(""))
                .withColumn("is_virtual",    F.lit(False))
            )
    else:
        # store_location_map 없음: store_id = 0 단일 가상 매장으로 대체
        print("[features_build] WARN: store_location_map 없음 — store_id=0 가상 매장 사용")
        stores = spark.createDataFrame(
            [(0, 0, 0, "", "", "", False)],
            ["store_id", "location_id", "inventory_location_id", "location_type", "wh_id", "size", "is_virtual"],
        )

    # ── 3. aladin_books / books_static → isbn13 목록 + 서적 속성 ─────────────
    books_raw = (
        _try_read(spark, f"{base}/mart/aladin_books/")
        or _try_read(spark, f"{base}/mart/books_static/")
    )
    if books_raw is None:
        # sales_daily 에서 isbn13 목록만 추출
        sales_tmp = _try_read(spark, f"{base}/mart/sales_daily/")
        if sales_tmp is None:
            print("[features_build] ERROR: isbn13 소스(books/sales_daily) 없음 — 중단")
            return None
        books_raw = (
            sales_tmp
            .select(F.col("isbn13").cast(StringType()).alias("isbn13"))
            .distinct()
        )

    # 서적 속성 컬럼 추출 (존재하는 것만)
    def _book_col_expr(target, candidates, cast_type, default):
        found = _find_col(books_raw, candidates)
        if found:
            return F.coalesce(F.col(found).cast(cast_type), default).alias(target)
        return default.alias(target)

    books = (
        books_raw
        .withColumn("isbn13", F.col("isbn13").cast(StringType()))
        .select(
            "isbn13",
            _book_col_expr("pub_date",               _BOOK_COL_CANDIDATES["pub_date"],               StringType(), F.lit(None).cast(StringType())),
            _book_col_expr("category_id",            _BOOK_COL_CANDIDATES["category_id"],            IntegerType(), F.lit(None).cast(IntegerType())),
            _book_col_expr("category_name",          _BOOK_COL_CANDIDATES["category_name"],          StringType(), F.lit("")),
            _book_col_expr("publisher",              _BOOK_COL_CANDIDATES["publisher"],              StringType(), F.lit("")),
            _book_col_expr("author",                 _BOOK_COL_CANDIDATES["author"],                 StringType(), F.lit("")),
            _book_col_expr("price_standard",         _BOOK_COL_CANDIDATES["price_standard"],         DoubleType(), F.lit(None).cast(DoubleType())),
            _book_col_expr("price_sales",            _BOOK_COL_CANDIDATES["price_sales"],            DoubleType(), F.lit(None).cast(DoubleType())),
            _book_col_expr("price_tier",             _BOOK_COL_CANDIDATES["price_tier"],             StringType(), F.lit("")),
            _book_col_expr("sales_point",            _BOOK_COL_CANDIDATES["sales_point"],            IntegerType(), F.lit(None).cast(IntegerType())),
            _book_col_expr("item_page",              _BOOK_COL_CANDIDATES["item_page"],              IntegerType(), F.lit(None).cast(IntegerType())),
            _book_col_expr("is_bestseller_flag",     _BOOK_COL_CANDIDATES["is_bestseller_flag"],     BooleanType(), F.lit(False)),
            _book_col_expr("author_past_books_count",_BOOK_COL_CANDIDATES["author_past_books_count"],IntegerType(), F.lit(None).cast(IntegerType())),
            _book_col_expr("author_debut_year",      _BOOK_COL_CANDIDATES["author_debut_year"],      IntegerType(), F.lit(None).cast(IntegerType())),
            _book_col_expr("author_experience_years",_BOOK_COL_CANDIDATES["author_experience_years"],IntegerType(), F.lit(None).cast(IntegerType())),
        )
        .dropDuplicates(["isbn13"])
    )

    # ── 4. 피처 그리드: feature_date × isbn13 × store_id ─────────────────────
    # date × books 크로스조인 → × stores 크로스조인
    # 1000권 × 365일 × 15매장 = 약 5.5M rows → G.1X 4노드 처리 가능
    grid = (
        cal.select("feature_date")
        .crossJoin(books)
        .crossJoin(stores)
    )

    # book_age_days (pub_date → feature_date 경과일, 음수=미출간)
    grid = grid.withColumn(
        "book_age_days",
        F.when(
            F.col("pub_date").isNotNull(),
            F.datediff(
                F.col("feature_date"),
                F.to_date(F.col("pub_date").cast("string")),
            ).cast(IntegerType()),
        ).otherwise(F.lit(None).cast(IntegerType())),
    )

    # ── 5. SNS 언급량: 1일 · 7일 롤링 합계 ───────────────────────────────────
    # raw_sns_mart(Apps)는 mart/ 없이 {MART_BUCKET}/sns_mentions/ 에 씀
    sns_raw = (
        _try_read(spark, f"{base}/mart/sns_mentions/")
        or _try_read(spark, f"{base}/sns_mentions/")
    )
    sns_feat = None
    if sns_raw is not None:
        date_col_s    = _find_col(sns_raw, ("mention_date", "date", "event_date"))
        mention_col   = _find_col(sns_raw, ("mention_count", "count", "mentions"))
        if date_col_s and mention_col:
            sns_daily = (
                sns_raw
                .withColumn("sdate",  F.to_date(F.col(date_col_s).cast("string")))
                .withColumn("isbn13", F.col("isbn13").cast(StringType()))
                .withColumn("cnt",    F.coalesce(F.col(mention_col).cast(DoubleType()), F.lit(0.0)))
                .groupBy("sdate", "isbn13")
                .agg(F.sum("cnt").alias("sns_mentions_1d"))
            )
            # DateType → long = days since epoch → rangeBetween(-6, 0) = 7일 윈도우
            w7 = (
                Window.partitionBy("isbn13")
                .orderBy(F.col("sdate").cast(LongType()))
                .rangeBetween(-6, 0)
            )
            sns_feat = (
                sns_daily
                .withColumn("sns_mentions_7d", F.sum("sns_mentions_1d").over(w7))
                .withColumnRenamed("sdate", "feature_date")
                .select("feature_date", "isbn13", "sns_mentions_1d", "sns_mentions_7d")
            )

    # ── 6. 재고: 매장별 on_hand + 전사 on_hand_total + days_since_last_stockout ─
    inv_raw = _try_read(spark, f"{base}/mart/inventory_daily/")
    inv_feat = None
    if inv_raw is not None:
        snap_col = _find_col(inv_raw, ("snapshot_date", "date", "snap_date"))
        oh_col   = _find_col(inv_raw, ("on_hand",))
        rq_col   = _find_col(inv_raw, ("reserved_qty", "reserved"))
        ss_col   = _find_col(inv_raw, ("safety_stock",))
        loc_col  = _find_col(inv_raw, ("location_id", "loc_id"))

        if snap_col and oh_col and loc_col:
            inv = (
                inv_raw
                .withColumn("snap",                F.to_date(F.col(snap_col).cast("string")))
                .withColumn("isbn13",              F.col("isbn13").cast(StringType()))
                .withColumn("inventory_location_id", F.col(loc_col).cast(IntegerType()))
                .withColumn("on_hand",             F.coalesce(F.col(oh_col).cast(DoubleType()), F.lit(0.0)))
                .withColumn("reserved_qty",        F.coalesce(F.col(rq_col).cast(DoubleType()) if rq_col else F.lit(0.0), F.lit(0.0)))
                .withColumn("safety_stock",        F.coalesce(F.col(ss_col).cast(DoubleType()) if ss_col else F.lit(0.0), F.lit(0.0)))
            )

            # 매장별 재고 (inventory_location_id 기준)
            inv_per_loc = (
                inv
                .groupBy("snap", "isbn13", "inventory_location_id")
                .agg(
                    F.sum("on_hand").alias("on_hand"),
                    F.sum("reserved_qty").alias("reserved_qty"),
                    F.sum("safety_stock").alias("safety_stock"),
                )
            )

            # 전사 재고 합계 (isbn13 단위)
            inv_total = (
                inv
                .groupBy("snap", "isbn13")
                .agg(F.sum("on_hand").alias("on_hand_total"))
            )

            # days_since_last_stockout (inventory_location_id 기준)
            stockout = (
                inv_per_loc
                .withColumn("had_stockout", F.col("on_hand") <= 0)
            )
            w_hist = (
                Window.partitionBy("isbn13", "inventory_location_id")
                .orderBy(F.col("snap").cast(LongType()))
                .rowsBetween(Window.unboundedPreceding, 0)
            )
            inv_feat = (
                stockout
                .withColumn(
                    "last_stockout",
                    F.last(
                        F.when(F.col("had_stockout"), F.col("snap")),
                        ignorenulls=True,
                    ).over(w_hist),
                )
                .withColumn(
                    "days_since_last_stockout",
                    F.datediff(F.col("snap"), F.col("last_stockout")).cast(IntegerType()),
                )
                .join(inv_total, on=["snap", "isbn13"], how="left")
                .select(
                    F.col("snap").alias("feature_date"),
                    "isbn13",
                    "inventory_location_id",
                    "on_hand",
                    "reserved_qty",
                    "safety_stock",
                    "on_hand_total",
                    "days_since_last_stockout",
                )
            )

    # ── 7. sales_daily → qty_sold (학습 레이블) ───────────────────────────────
    # sales_daily_agg(Apps)는 mart/ 없이 {MART_BUCKET}/sales_daily/ 에 씀
    sales_raw = (
        _try_read(spark, f"{base}/mart/sales_daily/")
        or _try_read(spark, f"{base}/sales_daily/")
    )
    sales_feat = None
    if sales_raw is not None:
        sd_col  = _find_col(sales_raw, ("sale_date", "date", "sdate"))
        qty_col = _find_col(sales_raw, ("qty_sold", "quantity", "qty"))
        rev_col = _find_col(sales_raw, ("revenue",))
        ap_col  = _find_col(sales_raw, ("avg_price",))
        tx_col  = _find_col(sales_raw, ("tx_count", "transaction_count"))
        sid_col = _find_col(sales_raw, ("store_id",))

        if sd_col and qty_col and sid_col:
            sales_feat = (
                sales_raw
                .withColumn("feature_date", F.to_date(F.col(sd_col).cast("string")))
                .withColumn("isbn13",       F.col("isbn13").cast(StringType()))
                .withColumn("store_id",     F.col(sid_col).cast(IntegerType()))
                .withColumn("qty_sold",     F.coalesce(F.col(qty_col).cast(DoubleType()), F.lit(0.0)))
                .withColumn("revenue",      F.coalesce(F.col(rev_col).cast(DoubleType()) if rev_col else F.lit(None).cast(DoubleType()), F.lit(None).cast(DoubleType())))
                .withColumn("avg_price",    F.coalesce(F.col(ap_col).cast(DoubleType())  if ap_col  else F.lit(None).cast(DoubleType()), F.lit(None).cast(DoubleType())))
                .withColumn("tx_count",     F.coalesce(F.col(tx_col).cast(IntegerType()) if tx_col  else F.lit(None).cast(IntegerType()), F.lit(None).cast(IntegerType())))
                .groupBy("feature_date", "isbn13", "store_id")
                .agg(
                    F.sum("qty_sold").alias("qty_sold"),
                    F.sum("revenue").alias("revenue"),
                    F.first("avg_price").alias("avg_price"),
                    F.sum("tx_count").alias("tx_count"),
                )
            )

    # ── 8. 조립 ───────────────────────────────────────────────────────────────
    features = grid.join(cal, on="feature_date", how="left")

    # SNS
    if sns_feat is not None:
        features = features.join(sns_feat, on=["feature_date", "isbn13"], how="left")
    else:
        features = features.withColumn("sns_mentions_1d", F.lit(0.0)).withColumn("sns_mentions_7d", F.lit(0.0))

    # 재고 (inventory_location_id 기준 조인)
    if inv_feat is not None:
        features = features.join(
            inv_feat,
            on=["feature_date", "isbn13", "inventory_location_id"],
            how="left",
        )
    else:
        features = (
            features
            .withColumn("on_hand",                 F.lit(0.0))
            .withColumn("reserved_qty",             F.lit(0.0))
            .withColumn("safety_stock",             F.lit(0.0))
            .withColumn("on_hand_total",            F.lit(0.0))
            .withColumn("days_since_last_stockout", F.lit(None).cast(IntegerType()))
        )

    # 매출 레이블 (store_id 기준 조인)
    if sales_feat is not None:
        features = features.join(sales_feat, on=["feature_date", "isbn13", "store_id"], how="left")
        # 미출간 날짜(book_age_days < 0)는 qty_sold = NULL 처리
        features = features.withColumn(
            "qty_sold",
            F.when(
                F.col("book_age_days").isNotNull() & (F.col("book_age_days") >= 0),
                F.coalesce(F.col("qty_sold"), F.lit(0.0)),
            ).otherwise(F.lit(None).cast(DoubleType())),
        )
    else:
        features = (
            features
            .withColumn("qty_sold",   F.lit(None).cast(DoubleType()))
            .withColumn("revenue",    F.lit(None).cast(DoubleType()))
            .withColumn("avg_price",  F.lit(None).cast(DoubleType()))
            .withColumn("tx_count",   F.lit(None).cast(IntegerType()))
        )

    # ── 9. 최종 스키마 정리 ───────────────────────────────────────────────────
    return (
        features
        .select(
            # grain
            F.col("feature_date"),
            F.col("isbn13"),
            F.col("store_id").cast(IntegerType()),
            # 위치
            F.col("location_id").cast(IntegerType()),
            F.col("inventory_location_id").cast(IntegerType()),
            F.coalesce(F.col("location_type"), F.lit("")).cast(StringType()).alias("location_type"),
            F.coalesce(F.col("wh_id"),         F.lit("")).cast(StringType()).alias("wh_id"),
            F.coalesce(F.col("size"),          F.lit("")).cast(StringType()).alias("size"),
            F.coalesce(F.col("is_virtual"),    F.lit(False)).cast(BooleanType()).alias("is_virtual"),
            # 서적 속성
            F.col("category_id").cast(IntegerType()),
            F.coalesce(F.col("category_name"), F.lit("")).cast(StringType()).alias("category_name"),
            F.coalesce(F.col("publisher"),     F.lit("")).cast(StringType()).alias("publisher"),
            F.coalesce(F.col("author"),        F.lit("")).cast(StringType()).alias("author"),
            F.col("price_standard").cast(DoubleType()),
            F.col("price_sales").cast(DoubleType()),
            F.coalesce(F.col("price_tier"),    F.lit("")).cast(StringType()).alias("price_tier"),
            F.col("sales_point").cast(IntegerType()),
            F.col("item_page").cast(IntegerType()),
            F.col("author_past_books_count").cast(IntegerType()),
            F.col("author_debut_year").cast(IntegerType()),
            F.col("author_experience_years").cast(IntegerType()),
            F.col("book_age_days").cast(IntegerType()),
            F.coalesce(F.col("is_bestseller_flag"), F.lit(False)).cast(BooleanType()).alias("is_bestseller_flag"),
            # 캘린더
            F.coalesce(F.col("is_holiday"),        F.lit(False)).cast(BooleanType()).alias("is_holiday"),
            F.coalesce(F.col("holiday_name"),       F.lit("")).cast(StringType()).alias("holiday_name"),
            F.coalesce(F.col("season"),             F.lit("")).cast(StringType()).alias("season"),
            F.coalesce(F.col("day_of_week"),        F.lit(0)).cast(IntegerType()).alias("day_of_week"),
            F.coalesce(F.col("is_weekend"),         F.lit(False)).cast(BooleanType()).alias("is_weekend"),
            F.coalesce(F.col("month"),              F.lit(0)).cast(IntegerType()).alias("month"),
            F.coalesce(F.col("event_nearby_days"),  F.lit(0)).cast(IntegerType()).alias("event_nearby_days"),
            # SNS
            F.coalesce(F.col("sns_mentions_1d"),    F.lit(0.0)).cast(DoubleType()).alias("sns_mentions_1d"),
            F.coalesce(F.col("sns_mentions_7d"),    F.lit(0.0)).cast(DoubleType()).alias("sns_mentions_7d"),
            # 재고
            F.coalesce(F.col("on_hand"),            F.lit(0.0)).cast(DoubleType()).alias("on_hand"),
            F.coalesce(F.col("reserved_qty"),       F.lit(0.0)).cast(DoubleType()).alias("reserved_qty"),
            F.coalesce(F.col("safety_stock"),       F.lit(0.0)).cast(DoubleType()).alias("safety_stock"),
            F.coalesce(F.col("on_hand_total"),      F.lit(0.0)).cast(DoubleType()).alias("on_hand_total"),
            F.col("days_since_last_stockout").cast(IntegerType()),
            # 매출 레이블
            F.col("qty_sold").cast(DoubleType()),
            F.col("revenue").cast(DoubleType()),
            F.col("avg_price").cast(DoubleType()),
            F.col("tx_count").cast(IntegerType()),
        )
        .dropDuplicates(["feature_date", "isbn13", "store_id"])
    )


# ── 메인 ───────────────────────────────────────────────────────────────────────
def main() -> None:
    args = _get_args()
    mart_bucket = args["MART_BUCKET"]
    gcs_bucket  = args["GCS_BUCKET"]
    secret_arn  = args["gcp_secret_arn"]

    sc       = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark    = glue_ctx.spark_session

    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)

    # GCS 설정: 첫 번째 Spark action 전에 호출해야 executor에 반영됨
    if gcs_bucket:
        try:
            _configure_gcs(spark, sc, _fetch_sa_key(secret_arn))
            print(f"[features_build] GCS 인증 설정 완료 → {gcs_bucket}")
        except Exception as exc:
            print(f"[features_build] WARN: GCS 인증 실패 — {exc}  GCS write 스킵")
            gcs_bucket = ""

    # dynamic partition overwrite: 기존 다른 날짜 파티션 보존
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    features_df = build_features(spark, mart_bucket)
    if features_df is None:
        job.commit()
        return

    # 재사용을 위해 캐시 (S3 write → GCS write 재스캔 방지)
    features_df.cache()
    row_count = features_df.count()
    print(f"[features_build] 총 {row_count:,} feature 행 (date × isbn13 × store_id)")

    # ── S3 Mart write ─────────────────────────────────────────────────────────
    s3_out = f"s3://{mart_bucket}/mart/features/"
    (
        features_df
        .repartition(8, "feature_date")
        .write
        .mode("overwrite")
        .partitionBy("feature_date")
        .parquet(s3_out)
    )
    print(f"[features_build] S3 write 완료 → {s3_out}")

    # ── GCS staging write (방향 A: features 만 GCS로) ─────────────────────────
    if gcs_bucket:
        gcs_out = f"gs://{gcs_bucket}/mart/features/"
        (
            features_df
            .repartition(8, "feature_date")
            .write
            .mode("overwrite")
            .partitionBy("feature_date")
            .parquet(gcs_out)
        )
        print(f"[features_build] GCS write 완료 → {gcs_out}")

    features_df.unpersist()
    job.commit()


if __name__ == "__main__":
    main()
