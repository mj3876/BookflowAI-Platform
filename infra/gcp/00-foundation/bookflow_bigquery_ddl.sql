-- BOOKFLOW BigQuery DDL generated from V3_BOOKFLOW_Data_Schema.xlsx.
-- Source workbook: C:\Users\1\Downloads\V3_BOOKFLOW_Data_Schema.xlsx
-- Project: project-8ab6bf05-54d2-4f5d-b8d
-- Dataset: bookflow_dw
-- Workbook location: Tokyo (asia-northeast1)

CREATE SCHEMA IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw`
OPTIONS (
  location = "asia-northeast1",
  description = "BOOKFLOW analytics data warehouse"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact` (
  sale_date DATE NOT NULL OPTIONS(description = "Sales date"),
  isbn13 STRING NOT NULL OPTIONS(description = "Book ISBN-13"),
  store_id INT64 NOT NULL OPTIONS(description = "Store id, 1-12"),
  wh_id INT64 OPTIONS(description = "Warehouse region id"),
  channel STRING OPTIONS(description = "offline or online"),
  qty_sold INT64 OPTIONS(description = "Vertex AI training target"),
  revenue NUMERIC OPTIONS(description = "Daily revenue"),
  avg_price NUMERIC OPTIONS(description = "Average selling price"),
  tx_count INT64 OPTIONS(description = "Transaction count")
)
PARTITION BY sale_date
CLUSTER BY isbn13, store_id
OPTIONS (
  description = "Daily sales aggregate used as the forecasting target"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily` (
  snapshot_date DATE NOT NULL OPTIONS(description = "Daily inventory snapshot date at 00:00 KST"),
  isbn13 STRING NOT NULL OPTIONS(description = "Book ISBN-13"),
  location_id INT64 NOT NULL OPTIONS(description = "Location id, 1-14; virtual locations excluded for real stock"),
  on_hand INT64 OPTIONS(description = "On-hand inventory quantity"),
  reserved_qty INT64 OPTIONS(description = "Reserved inventory quantity"),
  safety_stock INT64 OPTIONS(description = "Safety stock threshold")
)
PARTITION BY snapshot_date
CLUSTER BY isbn13
OPTIONS (
  description = "Daily inventory snapshot"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features` (
  feature_date DATE NOT NULL OPTIONS(description = "Feature date"),
  isbn13 STRING NOT NULL OPTIONS(description = "Book ISBN-13"),
  is_holiday BOOL OPTIONS(description = "Whether the date is a public holiday"),
  holiday_name STRING OPTIONS(description = "Holiday name"),
  season STRING OPTIONS(description = "SPRING, SUMMER, FALL, or WINTER"),
  day_of_week INT64 OPTIONS(description = "Day of week, 1=Monday through 7=Sunday"),
  is_weekend BOOL OPTIONS(description = "Whether the date is a weekend"),
  month INT64 OPTIONS(description = "Month number, 1-12"),
  event_nearby_days INT64 OPTIONS(description = "Days until next nearby event or holiday"),
  sns_mentions_1d INT64 OPTIONS(description = "SNS mentions from previous day"),
  sns_mentions_7d INT64 OPTIONS(description = "SNS mentions over previous 7 days"),
  book_age_days INT64 OPTIONS(description = "Days since publication date"),
  is_bestseller_flag BOOL OPTIONS(description = "Whether listed as an Aladin bestseller"),
  on_hand_total INT64 OPTIONS(description = "Company-wide available stock"),
  days_since_last_stockout INT64 OPTIONS(description = "Days since last stockout")
)
PARTITION BY feature_date
CLUSTER BY isbn13
OPTIONS (
  description = "Per-ISBN daily covariates for demand forecasting"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static` (
  isbn13 STRING NOT NULL OPTIONS(description = "Book ISBN-13"),
  category_id INT64 OPTIONS(description = "Aladin category id"),
  category_name STRING OPTIONS(description = "Category path"),
  publisher STRING OPTIONS(description = "Publisher name"),
  author STRING OPTIONS(description = "Primary author"),
  price_standard INT64 OPTIONS(description = "List price in KRW"),
  price_sales INT64 OPTIONS(description = "Sales price in KRW"),
  price_tier STRING OPTIONS(description = "LOW, MID, or HIGH"),
  sales_point INT64 OPTIONS(description = "Aladin sales point"),
  item_page INT64 OPTIONS(description = "Page count"),
  is_bestseller_flag BOOL OPTIONS(description = "Whether listed as an Aladin bestseller"),
  author_past_books_count INT64 OPTIONS(description = "Author's past published book count"),
  author_debut_year INT64 OPTIONS(description = "Author debut year"),
  author_experience_years INT64 OPTIONS(description = "Derived author experience years"),
  PRIMARY KEY (isbn13) NOT ENFORCED
)
CLUSTER BY isbn13
OPTIONS (
  description = "Static per-ISBN attributes for Vertex AI forecasting"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` (
  location_id INT64 NOT NULL OPTIONS(description = "Location id"),
  location_type STRING OPTIONS(description = "WH, STORE_OFFLINE, or STORE_ONLINE"),
  wh_id INT64 OPTIONS(description = "Warehouse region id, 1 or 2"),
  size STRING OPTIONS(description = "Offline store size: L, M, or S"),
  is_virtual BOOL OPTIONS(description = "Whether this is a virtual online store location"),
  PRIMARY KEY (location_id) NOT ENFORCED
)
CLUSTER BY location_id
OPTIONS (
  description = "Static per-location attributes for Vertex AI forecasting"
);

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_results` (
  prediction_date DATE NOT NULL OPTIONS(description = "Prediction execution date"),
  target_date DATE NOT NULL OPTIONS(description = "Forecast target date, D+1 through D+5"),
  isbn13 STRING NOT NULL OPTIONS(description = "Forecast target ISBN-13"),
  store_id INT64 NOT NULL OPTIONS(description = "Forecast target store id"),
  predicted_demand NUMERIC OPTIONS(description = "Predicted sales quantity"),
  confidence_low NUMERIC OPTIONS(description = "80 percent confidence interval lower bound"),
  confidence_high NUMERIC OPTIONS(description = "80 percent confidence interval upper bound"),
  model_version STRING OPTIONS(description = "Vertex AI Model Registry version"),
  inference_ms INT64 OPTIONS(description = "Inference latency in milliseconds")
)
PARTITION BY prediction_date
CLUSTER BY isbn13
OPTIONS (
  description = "Vertex AI forecast output"
);

CREATE OR REPLACE VIEW `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.training_dataset` AS
SELECT
  s.sale_date AS sale_date,
  s.isbn13 AS isbn13,
  s.store_id AS store_id,
  s.qty_sold AS qty_sold,
  bs.category_id,
  bs.category_name,
  bs.publisher,
  bs.author,
  bs.price_tier,
  bs.sales_point,
  bs.item_page,
  ls.location_type,
  ls.wh_id,
  ls.size,
  ls.is_virtual,
  f.is_holiday,
  f.season,
  f.day_of_week,
  f.is_weekend,
  f.month,
  f.event_nearby_days,
  f.sns_mentions_1d,
  f.sns_mentions_7d,
  f.on_hand_total,
  f.days_since_last_stockout,
  f.book_age_days,
  f.is_bestseller_flag,
  i.on_hand,
  i.reserved_qty
FROM `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_fact` AS s
LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.features` AS f
  ON s.isbn13 = f.isbn13
  AND s.sale_date = f.feature_date
LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books_static` AS bs
  ON s.isbn13 = bs.isbn13
LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations_static` AS ls
  ON s.store_id = ls.location_id
LEFT JOIN `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_daily` AS i
  ON s.isbn13 = i.isbn13
  AND s.sale_date = i.snapshot_date
  AND ls.location_id = i.location_id;

-- Converted RDS master and operational tables from sheet 03.
-- These definitions are BigQuery-compatible mirrors of the workbook's RDS schema.

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.books` (
  isbn13 STRING NOT NULL,
  isbn10 STRING,
  aladin_item_id INT64,
  title STRING NOT NULL,
  author STRING,
  publisher STRING,
  pub_date DATE,
  category_id INT64,
  category_name STRING,
  price_standard INT64,
  price_sales INT64,
  cover_url STRING,
  description STRING,
  active BOOL NOT NULL,
  discontinue_mode STRING,
  discontinue_reason STRING,
  discontinue_at TIMESTAMP,
  discontinue_by STRING,
  reactivated_at TIMESTAMP,
  expected_soldout_at DATE,
  source STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (isbn13) NOT ENFORCED
)
CLUSTER BY isbn13;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.authors` (
  author_id INT64 NOT NULL,
  name STRING NOT NULL,
  debut_year INT64,
  past_books_count INT64 NOT NULL,
  PRIMARY KEY (author_id) NOT ENFORCED
)
CLUSTER BY author_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.publishers` (
  publisher_id INT64 NOT NULL,
  name STRING NOT NULL,
  contact_email STRING,
  PRIMARY KEY (publisher_id) NOT ENFORCED
)
CLUSTER BY publisher_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.warehouses` (
  wh_id INT64 NOT NULL,
  name STRING NOT NULL,
  region STRING,
  capacity INT64,
  PRIMARY KEY (wh_id) NOT ENFORCED
)
CLUSTER BY wh_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.locations` (
  location_id INT64 NOT NULL,
  location_type STRING NOT NULL,
  wh_id INT64 NOT NULL,
  name STRING,
  size STRING,
  region STRING,
  is_virtual BOOL NOT NULL,
  active BOOL NOT NULL,
  PRIMARY KEY (location_id) NOT ENFORCED
)
CLUSTER BY location_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory` (
  isbn13 STRING NOT NULL,
  location_id INT64 NOT NULL,
  on_hand INT64 NOT NULL,
  reserved_qty INT64 NOT NULL,
  safety_stock INT64,
  updated_at TIMESTAMP NOT NULL,
  updated_by STRING,
  PRIMARY KEY (isbn13, location_id) NOT ENFORCED
)
CLUSTER BY isbn13, location_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.reservations` (
  reservation_id STRING NOT NULL,
  isbn13 STRING NOT NULL,
  location_id INT64 NOT NULL,
  qty INT64 NOT NULL,
  reason STRING NOT NULL,
  status STRING NOT NULL,
  ttl TIMESTAMP,
  created_by STRING,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (reservation_id) NOT ENFORCED
)
CLUSTER BY reservation_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.pending_orders` (
  order_id STRING NOT NULL,
  order_type STRING NOT NULL,
  isbn13 STRING NOT NULL,
  source_location_id INT64,
  target_location_id INT64,
  qty INT64 NOT NULL,
  est_lead_time_hours INT64,
  est_cost INT64,
  forecast_rationale JSON,
  urgency_level STRING NOT NULL,
  auto_execute_eligible BOOL NOT NULL,
  stock_days_remaining NUMERIC,
  demand_confidence_ratio NUMERIC,
  demand_cv NUMERIC,
  status STRING,
  execution_reason STRING,
  reject_reason STRING,
  reject_count INT64 NOT NULL,
  created_at TIMESTAMP NOT NULL,
  approved_at TIMESTAMP,
  executed_at TIMESTAMP,
  PRIMARY KEY (order_id) NOT ENFORCED
)
CLUSTER BY order_id, isbn13;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.order_approvals` (
  approval_id STRING NOT NULL,
  order_id STRING NOT NULL,
  approver_id STRING NOT NULL,
  approver_role STRING,
  approver_wh_id INT64,
  approval_side STRING NOT NULL,
  decision STRING NOT NULL,
  reject_reason STRING,
  decided_at TIMESTAMP NOT NULL,
  PRIMARY KEY (approval_id) NOT ENFORCED
)
CLUSTER BY order_id, approval_side;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.returns` (
  return_id STRING NOT NULL,
  isbn13 STRING NOT NULL,
  location_id INT64 NOT NULL,
  qty INT64 NOT NULL,
  reason STRING NOT NULL,
  status STRING NOT NULL,
  requested_at TIMESTAMP NOT NULL,
  hq_approved_at TIMESTAMP,
  executed_at TIMESTAMP,
  PRIMARY KEY (return_id) NOT ENFORCED
)
CLUSTER BY return_id, isbn13;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.audit_log` (
  log_id INT64 NOT NULL,
  ts TIMESTAMP NOT NULL,
  actor_type STRING,
  actor_id STRING,
  action STRING,
  entity_type STRING,
  entity_id STRING,
  before_state JSON,
  after_state JSON,
  source_ip STRING,
  request_id STRING,
  PRIMARY KEY (log_id) NOT ENFORCED
)
PARTITION BY DATE(ts)
CLUSTER BY entity_type, entity_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.users` (
  user_id STRING NOT NULL,
  email STRING,
  display_name STRING,
  role STRING NOT NULL,
  scope_wh_id INT64,
  scope_store_id INT64,
  last_login_at TIMESTAMP,
  PRIMARY KEY (user_id) NOT ENFORCED
)
CLUSTER BY user_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.forecast_cache` (
  snapshot_date DATE NOT NULL,
  isbn13 STRING NOT NULL,
  store_id INT64 NOT NULL,
  predicted_demand NUMERIC,
  confidence_low NUMERIC,
  confidence_high NUMERIC,
  model_version STRING,
  synced_at TIMESTAMP,
  PRIMARY KEY (snapshot_date, isbn13, store_id) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY isbn13, store_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.new_book_requests` (
  id INT64 NOT NULL,
  publisher_id STRING NOT NULL,
  isbn13 STRING NOT NULL,
  title STRING,
  author STRING,
  genre STRING,
  expected_pub_date DATE,
  estimated_initial_sales INT64,
  marketing_plan STRING,
  similar_books JSON,
  target_segments JSON,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  fetched_at TIMESTAMP,
  approved_at TIMESTAMP,
  PRIMARY KEY (id) NOT ENFORCED
)
CLUSTER BY id, isbn13;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.spike_events` (
  event_id STRING NOT NULL,
  detected_at TIMESTAMP NOT NULL,
  isbn13 STRING NOT NULL,
  z_score NUMERIC,
  mentions_count INT64,
  triggered_order_id STRING,
  resolved_at TIMESTAMP,
  PRIMARY KEY (event_id) NOT ENFORCED
)
PARTITION BY DATE(detected_at)
CLUSTER BY isbn13;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.notifications_log` (
  notification_id STRING NOT NULL,
  event_type STRING NOT NULL,
  correlation_id STRING,
  severity STRING,
  recipients JSON,
  channels STRING,
  payload_summary JSON,
  sent_at TIMESTAMP NOT NULL,
  status STRING NOT NULL,
  PRIMARY KEY (notification_id) NOT ENFORCED
)
PARTITION BY DATE(sent_at)
CLUSTER BY event_type, status;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.sales_realtime` (
  txn_id STRING NOT NULL,
  event_ts TIMESTAMP NOT NULL,
  store_id INT64 NOT NULL,
  wh_id INT64 NOT NULL,
  channel STRING NOT NULL,
  isbn13 STRING NOT NULL,
  qty INT64 NOT NULL,
  unit_price INT64 NOT NULL,
  discount INT64 NOT NULL,
  revenue INT64 NOT NULL,
  payment_method STRING,
  created_at TIMESTAMP NOT NULL,
  PRIMARY KEY (txn_id) NOT ENFORCED
)
PARTITION BY DATE(event_ts)
CLUSTER BY isbn13, store_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.inventory_snapshot_daily` (
  snapshot_date DATE NOT NULL,
  isbn13 STRING NOT NULL,
  location_id INT64 NOT NULL,
  on_hand INT64 NOT NULL,
  reserved_qty INT64 NOT NULL,
  available INT64 NOT NULL,
  safety_stock INT64,
  snapshot_taken_at TIMESTAMP NOT NULL,
  PRIMARY KEY (snapshot_date, isbn13, location_id) NOT ENFORCED
)
PARTITION BY snapshot_date
CLUSTER BY isbn13, location_id;

CREATE TABLE IF NOT EXISTS `project-8ab6bf05-54d2-4f5d-b8d.bookflow_dw.kpi_daily` (
  kpi_date DATE NOT NULL,
  store_id INT64 NOT NULL,
  category_id INT64 NOT NULL,
  channel STRING NOT NULL,
  qty_sold INT64 NOT NULL,
  revenue INT64 NOT NULL,
  tx_count INT64 NOT NULL,
  avg_price INT64,
  unique_isbn_count INT64,
  top_isbn STRING,
  synced_from_bq_at TIMESTAMP NOT NULL,
  PRIMARY KEY (kpi_date, store_id, category_id, channel) NOT ENFORCED
)
PARTITION BY kpi_date
CLUSTER BY store_id, category_id, channel;
