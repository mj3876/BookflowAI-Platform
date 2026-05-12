"""
Vertex AI AutoML Forecasting 학습 job 제출 (비동기)
enriched 피처: sales_fact + books_static + locations_static + features + lag
"""
import json
from pathlib import Path
from google.cloud import bigquery, aiplatform

PROJECT  = "project-8ab6bf05-54d2-4f5d-b8d"
DATASET  = "bookflow_dw"
LOCATION = "asia-northeast1"

bq = bigquery.Client(project=PROJECT, location=LOCATION)
aiplatform.init(project=PROJECT, location=LOCATION)

# ── 1. Enriched 학습 뷰 생성 ───────────────────────────────────────────────────
print("[1/3] Enriched 학습 뷰 생성 ...")
bq.query(f"""
CREATE OR REPLACE VIEW `{PROJECT}.{DATASET}.v_automl_forecast_input` AS
WITH daily_sales AS (
  SELECT
    CAST(s.sale_date AS DATE)                              AS sale_date,
    s.isbn13,
    s.store_id,
    SUM(COALESCE(CAST(s.qty_sold  AS FLOAT64), 0))        AS qty_sold,
    SUM(COALESCE(CAST(s.revenue   AS FLOAT64), 0))        AS revenue,
    AVG(COALESCE(CAST(s.avg_price AS FLOAT64), 0))        AS avg_price
  FROM `{PROJECT}.{DATASET}.sales_fact` s
  WHERE s.sale_date IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT
  -- 시계열 식별자
  CONCAT(d.isbn13, '_', CAST(d.store_id AS STRING))       AS series_id,
  d.sale_date,

  -- 타겟
  d.qty_sold,

  -- 과거에만 알 수 있는 값 (unavailable_at_forecast)
  d.revenue,
  d.avg_price,
  COALESCE(f.sns_mentions_1d, 0)                          AS sns_mentions_1d,
  COALESCE(f.sns_mentions_7d, 0)                          AS sns_mentions_7d,

  -- 미래에도 알 수 있는 값 (available_at_forecast)
  COALESCE(f.is_holiday,        FALSE)                    AS is_holiday,
  COALESCE(f.event_nearby_days, 0)                        AS event_nearby_days,
  f.season,
  f.day_of_week,
  f.month,
  COALESCE(f.is_weekend, FALSE)                           AS is_weekend,

  -- 시계열 정적 속성 (time_series_attribute)
  d.store_id,
  ls.wh_id,
  ls.size                                                 AS store_size,
  ls.region,
  b.category_id,
  b.price_tier,
  CAST(COALESCE(b.is_bestseller_flag, FALSE) AS INT64)    AS is_bestseller_flag,
  COALESCE(b.author_experience_years, 0)                  AS author_experience_years

FROM daily_sales d
LEFT JOIN `{PROJECT}.{DATASET}.features` f
       ON f.isbn13 = d.isbn13
      AND CAST(f.feature_date AS DATE) = d.sale_date
LEFT JOIN `{PROJECT}.{DATASET}.books_static` b
       ON b.isbn13 = d.isbn13
LEFT JOIN `{PROJECT}.{DATASET}.store_location_map` slm
       ON slm.store_id = d.store_id
LEFT JOIN `{PROJECT}.{DATASET}.locations_static` ls
       ON ls.location_id = slm.location_id
WHERE d.qty_sold IS NOT NULL
""", location=LOCATION).result()
print("  완료")

# ── 2. Vertex AI TimeSeriesDataset 생성 ────────────────────────────────────────
print("\n[2/3] Vertex AI Dataset 생성 ...")
ts_dataset = aiplatform.TimeSeriesDataset.create(
    display_name="bookflow-sales-timeseries-v2",
    bq_source=f"bq://{PROJECT}.{DATASET}.v_automl_forecast_input",
)
print(f"  {ts_dataset.resource_name}")

# ── 3. AutoML Forecasting job 제출 (비동기) ────────────────────────────────────
print("\n[3/3] AutoML Forecasting 학습 job 제출 ...")
job = aiplatform.AutoMLForecastingTrainingJob(
    display_name="bookflow-sales-forecast-v2",
    optimization_objective="minimize-rmse",
    column_transformations=[
        {"timestamp":   {"column_name": "sale_date"}},
        {"numeric":     {"column_name": "qty_sold"}},
        {"numeric":     {"column_name": "revenue"}},
        {"numeric":     {"column_name": "avg_price"}},
        {"numeric":     {"column_name": "sns_mentions_1d"}},
        {"numeric":     {"column_name": "sns_mentions_7d"}},
        {"categorical": {"column_name": "is_holiday"}},
        {"numeric":     {"column_name": "event_nearby_days"}},
        {"categorical": {"column_name": "season"}},
        {"numeric":     {"column_name": "day_of_week"}},
        {"numeric":     {"column_name": "month"}},
        {"categorical": {"column_name": "is_weekend"}},
        {"numeric":     {"column_name": "store_id"}},
        {"numeric":     {"column_name": "wh_id"}},
        {"categorical": {"column_name": "store_size"}},
        {"categorical": {"column_name": "region"}},
        {"categorical": {"column_name": "category_id"}},
        {"categorical": {"column_name": "price_tier"}},
        {"numeric":     {"column_name": "is_bestseller_flag"}},
        {"numeric":     {"column_name": "author_experience_years"}},
    ],
)

model = job.run(
    dataset=ts_dataset,
    target_column="qty_sold",
    time_column="sale_date",
    time_series_identifier_column="series_id",
    time_series_attribute_columns=[
        "store_id", "wh_id", "store_size", "region",
        "category_id", "price_tier", "is_bestseller_flag", "author_experience_years",
    ],
    available_at_forecast_columns=[
        "sale_date",
        "is_holiday", "event_nearby_days", "season",
        "day_of_week", "month", "is_weekend",
    ],
    unavailable_at_forecast_columns=[
        "qty_sold", "revenue", "avg_price", "sns_mentions_1d", "sns_mentions_7d",
    ],
    data_granularity_unit="day",
    data_granularity_count=1,
    forecast_horizon=30,
    context_window=60,
    budget_milli_node_hours=1000,
    model_display_name="bookflow-sales-forecast-v2",
    sync=True,  # GCP에서 학습 실행, 로컬은 폴링만
)

print(f"\n학습 완료!")
print(f"  Model : {model.resource_name}")

state_file = Path(r"D:\gcp\BookFlowAI-Platform\scripts\automl_job_state.json")
state_file.write_text(json.dumps({
    "model_resource_name": model.resource_name,
    "dataset_resource_name": ts_dataset.resource_name,
}, indent=2), encoding="utf-8")
print(f"  job 정보 저장: {state_file}")
