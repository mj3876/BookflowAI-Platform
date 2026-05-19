-- Remap existing synthetic training data to real Aladin book metadata.
-- Run with:
--   bq query --use_legacy_sql=false "$(Get-Content -Raw BookFlowAI-Platform/scripts/gcp/2-tasks/fix_real_aladin_training_data.sql)"

DECLARE project_id STRING DEFAULT 'project-8ab6bf05-54d2-4f5d-b8d';
DECLARE dataset_id STRING DEFAULT 'bookflow_dw';
DECLARE suffix STRING DEFAULT '20260519_real_aladin_fix';

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static_backup_20260519_real_aladin_fix`
CLONE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static`;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact_backup_20260519_real_aladin_fix`
CLONE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact`;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features_backup_20260519_real_aladin_fix`
CLONE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features`;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily_backup_20260519_real_aladin_fix`
CLONE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily`;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results_backup_20260519_real_aladin_fix`
CLONE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results`;

DROP TABLE IF EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static`;
DROP TABLE IF EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact`;
DROP TABLE IF EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily`;
DROP TABLE IF EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features`;
DROP TABLE IF EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books`;

CREATE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static`
CLUSTER BY isbn13 AS
SELECT
  CAST(seed.isbn13 AS STRING) AS isbn13,
  seed.title,
  seed.category_id,
  seed.category_name,
  seed.publisher,
  seed.author,
  seed.pub_date,
  seed.price_standard,
  seed.price_sales,
  seed.price_tier,
  seed.sales_point,
  seed.item_page,
  seed.is_bestseller_flag,
  COALESCE(seed.author_past_books_count, 0) AS author_past_books_count,
  seed.author_debut_year,
  COALESCE(seed.author_experience_years, 0) AS author_experience_years,
  seed.cover_url,
  seed.description,
  seed.source
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._aladin_books_seed` seed
JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._isbn_map` map
  ON map.real_isbn = CAST(seed.isbn13 AS STRING);

CREATE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact`
PARTITION BY sale_date
CLUSTER BY isbn13, store_id AS
SELECT
  SAFE_CAST(s.sale_date AS DATE) AS sale_date,
  map.real_isbn AS isbn13,
  s.store_id,
  s.wh_id,
  s.channel,
  s.qty_sold,
  s.revenue,
  s.avg_price,
  s.tx_count
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact_backup_20260519_real_aladin_fix` s
JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._isbn_map` map
  ON s.isbn13 = map.synthetic_isbn;

CREATE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily`
PARTITION BY snapshot_date
CLUSTER BY isbn13 AS
SELECT
  SAFE_CAST(i.snapshot_date AS DATE) AS snapshot_date,
  map.real_isbn AS isbn13,
  i.location_id,
  i.on_hand,
  i.reserved_qty,
  i.safety_stock
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily_backup_20260519_real_aladin_fix` i
JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._isbn_map` map
  ON i.isbn13 = map.synthetic_isbn;

CREATE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features`
PARTITION BY feature_date
CLUSTER BY isbn13 AS
SELECT
  SAFE_CAST(feature_date AS DATE) AS feature_date,
  isbn13,
  ANY_VALUE(is_holiday) AS is_holiday,
  ANY_VALUE(holiday_name) AS holiday_name,
  ANY_VALUE(season) AS season,
  ANY_VALUE(day_of_week) AS day_of_week,
  ANY_VALUE(is_weekend) AS is_weekend,
  ANY_VALUE(month) AS month,
  ANY_VALUE(event_nearby_days) AS event_nearby_days,
  ANY_VALUE(sns_mentions_1d) AS sns_mentions_1d,
  ANY_VALUE(sns_mentions_7d) AS sns_mentions_7d,
  ANY_VALUE(book_age_days) AS book_age_days,
  ANY_VALUE(is_bestseller_flag) AS is_bestseller_flag,
  ANY_VALUE(on_hand_total) AS on_hand_total,
  ANY_VALUE(days_since_last_stockout) AS days_since_last_stockout
FROM (
  SELECT
    SAFE_CAST(f.feature_date AS DATE) AS feature_date,
    map.real_isbn AS isbn13,
    f.is_holiday,
    f.holiday_name,
    f.season,
    f.day_of_week,
    f.is_weekend,
    f.month,
    f.event_nearby_days,
    f.sns_mentions_1d,
    f.sns_mentions_7d,
    f.book_age_days,
    f.is_bestseller_flag,
    f.on_hand_total,
    f.days_since_last_stockout
  FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features_backup_20260519_real_aladin_fix` f
  JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._isbn_map` map
    ON f.isbn13 = map.synthetic_isbn
)
GROUP BY feature_date, isbn13;

CREATE TABLE `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books`
CLUSTER BY isbn13 AS
SELECT
  CAST(seed.isbn13 AS STRING) AS isbn13,
  seed.isbn10,
  seed.aladin_item_id,
  seed.title,
  seed.author,
  seed.publisher,
  seed.pub_date,
  seed.category_id,
  seed.category_name,
  seed.price_standard,
  seed.price_sales,
  seed.cover_url,
  seed.description,
  TRUE AS active,
  'NONE' AS discontinue_mode,
  CAST(NULL AS STRING) AS discontinue_reason,
  CAST(NULL AS TIMESTAMP) AS discontinue_at,
  CAST(NULL AS STRING) AS discontinue_by,
  CAST(NULL AS TIMESTAMP) AS reactivated_at,
  CAST(NULL AS DATE) AS expected_soldout_at,
  'ALADIN' AS source,
  CURRENT_TIMESTAMP() AS created_at,
  CURRENT_TIMESTAMP() AS updated_at
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._aladin_books_seed` seed
JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw._isbn_map` map
  ON map.real_isbn = CAST(seed.isbn13 AS STRING);

DELETE FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results`
WHERE TRUE;
