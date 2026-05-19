WITH base AS (
  SELECT
    feature_date,
    isbn13,
    qty_sold,
    store_id,
    wh_id,
    channel,
    location_type,
    store_size,
    region,
    on_hand,
    reserved_qty,
    safety_stock,
    holiday_flag,
    day_of_week,
    month,
    weekend_flag,
    event_nearby_days,
    sns_mentions_1d,
    sns_mentions_7d,
    book_age_days,
    days_since_last_stockout,
    category_id,
    price_tier,
    sales_point,
    bestseller_flag,
    author_experience_years,
    qty_lag_1,
    qty_lag_7,
    qty_rolling_7d,
    qty_rolling_28d,
    demand_segment
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset_store`
  WHERE feature_date > DATE_SUB(
      (SELECT MAX(feature_date) FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset_store`),
      INTERVAL 134 DAY
    )
    AND feature_date <= (
      SELECT MAX(feature_date)
      FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset_store`
    )
    AND demand_segment IN ('high', 'medium')
    AND qty_sold IS NOT NULL
),
split AS (
  SELECT
    *,
    IF(
      feature_date > DATE_SUB(
        (SELECT MAX(feature_date) FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset_store`),
        INTERVAL 14 DAY
      ),
      'holdout',
      'train'
    ) AS split_name
  FROM base
)
SELECT *
FROM split
WHERE split_name = 'holdout'
   OR MOD(ABS(FARM_FINGERPRINT(CONCAT(isbn13, '#', CAST(store_id AS STRING), '#', CAST(feature_date AS STRING)))), 100) < 25;
