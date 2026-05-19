-- Write current D+1..D+5 forecast_results from the retrained real-Aladin model.

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
WITH wh_predictions AS (
  WITH latest_features AS (
    SELECT * EXCEPT(row_num)
    FROM (
      SELECT
        *,
        ROW_NUMBER() OVER (
          PARTITION BY isbn13, wh_id
          ORDER BY feature_date DESC
        ) AS row_num
      FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset`
    )
    WHERE row_num = 1
  ),
  future_features AS (
    SELECT
      DATE_ADD(feature_date, INTERVAL offset DAY) AS feature_date,
      isbn13,
      wh_id,
      EXTRACT(DAYOFWEEK FROM DATE_ADD(feature_date, INTERVAL offset DAY)) AS day_of_week,
      EXTRACT(MONTH FROM DATE_ADD(feature_date, INTERVAL offset DAY)) AS month,
      CAST(EXTRACT(DAYOFWEEK FROM DATE_ADD(feature_date, INTERVAL offset DAY)) IN (1, 7) AS INT64) AS weekend_flag,
      CAST(FALSE AS INT64) AS holiday_flag,
      GREATEST(COALESCE(event_nearby_days, 0) - offset, 0) AS event_nearby_days,
      COALESCE(book_age_days, 0) + offset AS book_age_days,
      COALESCE(sales_point, 0) AS sales_point,
      bestseller_flag,
      COALESCE(author_experience_years, 0) AS author_experience_years
    FROM latest_features
    CROSS JOIN UNNEST(GENERATE_ARRAY(1, 10)) AS offset
  )
  SELECT
    CURRENT_DATE('Asia/Seoul') AS prediction_date,
    DATE(forecast_timestamp) AS target_date,
    isbn13,
    wh_id,
    GREATEST(CAST(forecast_value AS FLOAT64), 0) AS predicted_wh_demand,
    GREATEST(CAST(prediction_interval_lower_bound AS FLOAT64), 0) AS confidence_low,
    GREATEST(CAST(prediction_interval_upper_bound AS FLOAT64), 0) AS confidence_high
  FROM ML.FORECAST(
    MODEL `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.bookflow_existing_books_forecast`,
    STRUCT(10 AS horizon, 0.8 AS confidence_level),
    (
      SELECT
        isbn13,
        wh_id,
        feature_date,
        day_of_week,
        month,
        holiday_flag,
        weekend_flag,
        event_nearby_days,
        book_age_days,
        sales_point,
        bestseller_flag,
        author_experience_years
      FROM future_features
    )
  )
  WHERE DATE(forecast_timestamp)
    BETWEEN DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL 1 DAY)
        AND DATE_ADD(CURRENT_DATE('Asia/Seoul'), INTERVAL 5 DAY)
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
