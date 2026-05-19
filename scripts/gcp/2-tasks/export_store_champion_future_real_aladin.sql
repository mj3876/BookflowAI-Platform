WITH latest_features AS (
  SELECT * EXCEPT(row_num)
  FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY isbn13, store_id
        ORDER BY feature_date DESC
      ) AS row_num
    FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset_store`
  )
  WHERE row_num = 1
),
future_features AS (
  SELECT
    CURRENT_DATE('Asia/Seoul') AS prediction_date,
    DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL offset DAY) AS target_date,
    isbn13,
    store_id,
    wh_id,
    channel,
    location_type,
    store_size,
    region,
    on_hand,
    reserved_qty,
    safety_stock,
    CAST(FALSE AS INT64) AS holiday_flag,
    EXTRACT(DAYOFWEEK FROM DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL offset DAY)) AS day_of_week,
    EXTRACT(MONTH FROM DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL offset DAY)) AS month,
    CAST(EXTRACT(DAYOFWEEK FROM DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL offset DAY)) IN (1, 7) AS INT64) AS weekend_flag,
    GREATEST(COALESCE(event_nearby_days, 0) - offset, 0) AS event_nearby_days,
    COALESCE(sns_mentions_1d, 0) AS sns_mentions_1d,
    COALESCE(sns_mentions_7d, 0) AS sns_mentions_7d,
    COALESCE(book_age_days, 0) + offset AS book_age_days,
    COALESCE(days_since_last_stockout, 0) + offset AS days_since_last_stockout,
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
  FROM latest_features
  CROSS JOIN UNNEST(GENERATE_ARRAY(1, 5)) AS offset
)
SELECT *
FROM future_features;
