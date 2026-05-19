-- Rebuild the existing-books BigQuery ML model and forecast_results
-- after remapping training data to real Aladin ISBNs and metadata.

CREATE OR REPLACE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset` AS
WITH date_spine AS (
  SELECT feature_date
  FROM UNNEST(GENERATE_DATE_ARRAY(
    (SELECT MIN(SAFE_CAST(sale_date AS DATE)) FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact`),
    (SELECT MAX(SAFE_CAST(sale_date AS DATE)) FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact`)
  )) AS feature_date
),
series AS (
  SELECT DISTINCT
    s.isbn13,
    COALESCE(ls.wh_id, s.wh_id) AS wh_id
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact` s
  LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.store_location_map` slm
    ON slm.store_id = s.store_id
  LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` ls
    ON ls.location_id = slm.location_id
  WHERE s.sale_date IS NOT NULL
    AND COALESCE(ls.wh_id, s.wh_id) IS NOT NULL
),
all_combinations AS (
  SELECT series.isbn13, series.wh_id, date_spine.feature_date
  FROM series
  CROSS JOIN date_spine
),
daily_wh_sales AS (
  SELECT
    SAFE_CAST(s.sale_date AS DATE) AS feature_date,
    s.isbn13,
    COALESCE(ls.wh_id, s.wh_id) AS wh_id,
    SUM(COALESCE(CAST(s.qty_sold AS FLOAT64), 0)) AS qty_sold,
    SUM(COALESCE(CAST(s.revenue AS FLOAT64), 0)) AS revenue,
    SUM(COALESCE(CAST(s.tx_count AS FLOAT64), 0)) AS tx_count
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact` s
  LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.store_location_map` slm
    ON slm.store_id = s.store_id
  LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` ls
    ON ls.location_id = slm.location_id
  WHERE s.sale_date IS NOT NULL
  GROUP BY 1, 2, 3
),
daily_wh_inventory AS (
  SELECT
    SAFE_CAST(i.snapshot_date AS DATE) AS feature_date,
    i.isbn13,
    ls.wh_id,
    SUM(COALESCE(CAST(i.on_hand AS FLOAT64), 0)) AS on_hand,
    SUM(COALESCE(CAST(i.reserved_qty AS FLOAT64), 0)) AS reserved_qty
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily` i
  JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` ls
    ON ls.location_id = i.location_id
  WHERE i.snapshot_date IS NOT NULL
  GROUP BY 1, 2, 3
),
features_dedup AS (
  SELECT * EXCEPT(row_num)
  FROM (
    SELECT
      *,
      ROW_NUMBER() OVER (
        PARTITION BY isbn13, SAFE_CAST(feature_date AS DATE)
        ORDER BY SAFE_CAST(feature_date AS DATE)
      ) AS row_num
    FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features`
  )
  WHERE row_num = 1
),
enriched AS (
  SELECT
    c.feature_date,
    c.isbn13,
    c.wh_id,
    COALESCE(s.qty_sold, 0) AS qty_sold,
    COALESCE(inv.on_hand, 0) AS on_hand,
    COALESCE(inv.reserved_qty, 0) AS reserved_qty,
    CAST(COALESCE(f.is_holiday, FALSE) AS INT64) AS holiday_flag,
    COALESCE(f.day_of_week, EXTRACT(DAYOFWEEK FROM c.feature_date)) AS day_of_week,
    COALESCE(f.month, EXTRACT(MONTH FROM c.feature_date)) AS month,
    CAST(COALESCE(f.is_weekend, EXTRACT(DAYOFWEEK FROM c.feature_date) IN (1, 7)) AS INT64) AS weekend_flag,
    COALESCE(f.event_nearby_days, 0) AS event_nearby_days,
    COALESCE(f.sns_mentions_1d, 0) AS sns_mentions_1d,
    COALESCE(f.sns_mentions_7d, 0) AS sns_mentions_7d,
    COALESCE(f.book_age_days, 0) AS book_age_days,
    COALESCE(f.days_since_last_stockout, 0) AS days_since_last_stockout,
    b.category_id,
    b.price_tier,
    COALESCE(b.sales_point, 0) AS sales_point,
    CAST(COALESCE(b.is_bestseller_flag, FALSE) AS INT64) AS bestseller_flag,
    COALESCE(b.author_experience_years, 0) AS author_experience_years
  FROM all_combinations c
  LEFT JOIN daily_wh_sales s
    ON s.feature_date = c.feature_date
   AND s.isbn13 = c.isbn13
   AND s.wh_id = c.wh_id
  LEFT JOIN daily_wh_inventory inv
    ON inv.feature_date = c.feature_date
   AND inv.isbn13 = c.isbn13
   AND inv.wh_id = c.wh_id
  LEFT JOIN features_dedup f
    ON f.isbn13 = c.isbn13
   AND SAFE_CAST(f.feature_date AS DATE) = c.feature_date
  LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static` b
    ON b.isbn13 = c.isbn13
)
SELECT
  *,
  LAG(qty_sold, 1) OVER (PARTITION BY isbn13, wh_id ORDER BY feature_date) AS qty_lag_1,
  LAG(qty_sold, 7) OVER (PARTITION BY isbn13, wh_id ORDER BY feature_date) AS qty_lag_7,
  AVG(qty_sold) OVER (
    PARTITION BY isbn13, wh_id
    ORDER BY feature_date
    ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
  ) AS qty_rolling_7d,
  AVG(qty_sold) OVER (
    PARTITION BY isbn13, wh_id
    ORDER BY feature_date
    ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING
  ) AS qty_rolling_28d
FROM enriched
WHERE qty_sold IS NOT NULL;

CREATE OR REPLACE MODEL `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.bookflow_existing_books_forecast`
OPTIONS(
  MODEL_TYPE = 'ARIMA_PLUS_XREG',
  TIME_SERIES_TIMESTAMP_COL = 'feature_date',
  TIME_SERIES_DATA_COL = 'qty_sold',
  TIME_SERIES_ID_COL = ['isbn13', 'wh_id'],
  HORIZON = 10,
  DATA_FREQUENCY = 'DAILY',
  AUTO_ARIMA = TRUE,
  AUTO_ARIMA_MAX_ORDER = 2,
  CLEAN_SPIKES_AND_DIPS = TRUE,
  ADJUST_STEP_CHANGES = TRUE,
  HOLIDAY_REGION = 'KR',
  TIME_SERIES_LENGTH_FRACTION = 0.6,
  MIN_TIME_SERIES_LENGTH = 90
) AS
SELECT
  feature_date,
  isbn13,
  wh_id,
  qty_sold,
  day_of_week,
  month,
  holiday_flag,
  weekend_flag,
  COALESCE(event_nearby_days, 0) AS event_nearby_days,
  COALESCE(book_age_days, 0) AS book_age_days,
  COALESCE(sales_point, 0) AS sales_point,
  bestseller_flag,
  COALESCE(author_experience_years, 0) AS author_experience_years
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset`
WHERE qty_sold IS NOT NULL;

DELETE FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results`
WHERE prediction_date = CURRENT_DATE('Asia/Seoul');

INSERT INTO `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results` (
  prediction_date,
  target_date,
  isbn13,
  store_id,
  predicted_demand,
  confidence_low,
  confidence_high,
  model_version,
  inference_ms
)
WITH params AS (
  SELECT
    CURRENT_DATE('Asia/Seoul') AS run_date,
    (SELECT MAX(feature_date) FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset`) AS max_feature_date
),
wh_predictions AS (
  SELECT
    params.run_date AS prediction_date,
    DATE(forecast_timestamp) AS target_date,
    isbn13,
    wh_id,
    GREATEST(CAST(forecast_value AS FLOAT64), 0) AS predicted_wh_demand,
    GREATEST(CAST(prediction_interval_lower_bound AS FLOAT64), 0) AS confidence_low,
    GREATEST(CAST(prediction_interval_upper_bound AS FLOAT64), 0) AS confidence_high
  FROM params,
  ML.FORECAST(
    MODEL `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.bookflow_existing_books_forecast`,
    STRUCT(
      CAST(DATE_DIFF(DATE_ADD(params.run_date, INTERVAL 5 DAY), params.max_feature_date, DAY) AS INT64) AS horizon,
      0.8 AS confidence_level
    )
  )
  WHERE DATE(forecast_timestamp)
    BETWEEN DATE_ADD(params.run_date, INTERVAL 1 DAY)
        AND DATE_ADD(params.run_date, INTERVAL 5 DAY)
),
stores AS (
  SELECT
    store_id,
    wh_id,
    COUNT(*) OVER (PARTITION BY wh_id) AS store_count
  FROM (
    SELECT DISTINCT
      slm.store_id,
      ls.wh_id
    FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.store_location_map` slm
    JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` ls
      ON ls.location_id = slm.location_id
    WHERE ls.location_type LIKE 'STORE%'
      AND ls.wh_id IS NOT NULL
  )
),
recent_store_sales AS (
  SELECT
    s.isbn13,
    st.wh_id,
    s.store_id,
    SUM(COALESCE(CAST(s.qty_sold AS FLOAT64), 0)) AS store_qty
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact` s
  JOIN stores st
    ON st.store_id = s.store_id
  WHERE SAFE_CAST(s.sale_date AS DATE) >= DATE_SUB(CURRENT_DATE('Asia/Seoul'), INTERVAL 28 DAY)
  GROUP BY 1, 2, 3
),
store_shares AS (
  SELECT
    st.wh_id,
    wp.isbn13,
    st.store_id,
    COALESCE(
      SAFE_DIVIDE(
        rss.store_qty,
        SUM(rss.store_qty) OVER (PARTITION BY wp.isbn13, st.wh_id)
      ),
      SAFE_DIVIDE(1, st.store_count)
    ) AS demand_share
  FROM wh_predictions wp
  JOIN stores st
    ON st.wh_id = wp.wh_id
  LEFT JOIN recent_store_sales rss
    ON rss.isbn13 = wp.isbn13
   AND rss.wh_id = st.wh_id
   AND rss.store_id = st.store_id
),
allocated_forecasts AS (
  SELECT
    wp.prediction_date,
    wp.target_date,
    wp.isbn13,
    ss.store_id,
    wp.predicted_wh_demand * ss.demand_share AS predicted_demand,
    wp.confidence_low * ss.demand_share AS confidence_low,
    wp.confidence_high * ss.demand_share AS confidence_high
  FROM wh_predictions wp
  JOIN store_shares ss
    ON ss.isbn13 = wp.isbn13
   AND ss.wh_id = wp.wh_id
)
SELECT
  prediction_date,
  target_date,
  isbn13,
  store_id,
  CAST(SUM(predicted_demand) AS NUMERIC) AS predicted_demand,
  CAST(SUM(confidence_low) AS NUMERIC) AS confidence_low,
  CAST(SUM(confidence_high) AS NUMERIC) AS confidence_high,
  'bookflow_existing_books_forecast-real-aladin-20260519' AS model_version,
  CAST(NULL AS INT64) AS inference_ms
FROM allocated_forecasts
GROUP BY 1, 2, 3, 4;
