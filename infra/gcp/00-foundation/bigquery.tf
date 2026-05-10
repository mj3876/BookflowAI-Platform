resource "google_bigquery_dataset" "bookflow_dw" {
  project       = var.project_id
  dataset_id    = var.bigquery_dataset_id
  friendly_name = "BOOKFLOW Data Warehouse"
  description   = "Analytics dataset for BOOKFLOW v6.2."
  location      = var.bigquery_location
  # Allow Terraform destroy to remove tables in the dataset during rebuilds.
  delete_contents_on_destroy = true

  labels = var.labels

  depends_on = [
    google_project_service.required["bigquery.googleapis.com"],
  ]
}

resource "google_bigquery_table" "sales_fact" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.sales_fact

  deletion_protection = false
  description         = "Daily sales aggregate used as the forecasting target."

  clustering = ["isbn13", "store_id"]

  schema = jsonencode([
    { name = "sale_date", type = "STRING", mode = "NULLABLE", description = "Sales date as provided by the historical parquet feed." },
    { name = "isbn13", type = "STRING", mode = "NULLABLE", description = "Book ISBN-13." },
    { name = "store_id", type = "INTEGER", mode = "NULLABLE", description = "Store id, 1-12." },
    { name = "wh_id", type = "INTEGER", mode = "NULLABLE", description = "Warehouse region id." },
    { name = "channel", type = "STRING", mode = "NULLABLE", description = "offline or online." },
    { name = "qty_sold", type = "INTEGER", mode = "NULLABLE", description = "Vertex AI training target." },
    { name = "revenue", type = "NUMERIC", mode = "NULLABLE", description = "Daily revenue." },
    { name = "avg_price", type = "NUMERIC", mode = "NULLABLE", description = "Average selling price." },
    { name = "tx_count", type = "INTEGER", mode = "NULLABLE", description = "Transaction count." },
    { name = "synthetic", type = "BOOLEAN", mode = "NULLABLE", description = "Marks generated historical seed rows." },
  ])
}

resource "google_bigquery_table" "inventory_daily" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.inventory_daily

  deletion_protection = false
  description         = "Daily inventory snapshot imported from the historical seed dataset."

  schema = jsonencode([
    { name = "snapshot_date", type = "DATE", mode = "NULLABLE", description = "Daily inventory snapshot date." },
    { name = "isbn13", type = "STRING", mode = "NULLABLE", description = "Book ISBN-13." },
    { name = "location_id", type = "INTEGER", mode = "NULLABLE", description = "Location id, 1-14." },
    { name = "on_hand", type = "INTEGER", mode = "NULLABLE", description = "On-hand inventory quantity." },
    { name = "reserved_qty", type = "INTEGER", mode = "NULLABLE", description = "Reserved inventory quantity." },
    { name = "safety_stock", type = "INTEGER", mode = "NULLABLE", description = "Safety stock threshold." },
  ])
}

resource "google_bigquery_table" "features" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.features

  deletion_protection = false
  description         = "Per-ISBN daily covariates for demand forecasting."

  clustering = ["isbn13"]

  schema = jsonencode([
    { name = "feature_date", type = "DATE", mode = "NULLABLE", description = "Feature date." },
    { name = "isbn13", type = "STRING", mode = "NULLABLE", description = "Book ISBN-13." },
    { name = "is_holiday", type = "BOOLEAN", mode = "NULLABLE", description = "Whether the date is a public holiday." },
    { name = "holiday_name", type = "STRING", mode = "NULLABLE", description = "Holiday name." },
    { name = "season", type = "STRING", mode = "NULLABLE", description = "SPRING, SUMMER, FALL, or WINTER." },
    { name = "day_of_week", type = "INTEGER", mode = "NULLABLE", description = "Day of week, 1=Monday through 7=Sunday." },
    { name = "is_weekend", type = "BOOLEAN", mode = "NULLABLE", description = "Whether the date is a weekend." },
    { name = "month", type = "INTEGER", mode = "NULLABLE", description = "Month number, 1-12." },
    { name = "event_nearby_days", type = "INTEGER", mode = "NULLABLE", description = "Days until next nearby event or holiday." },
    { name = "sns_mentions_1d", type = "INTEGER", mode = "NULLABLE", description = "SNS mentions from previous day." },
    { name = "sns_mentions_7d", type = "INTEGER", mode = "NULLABLE", description = "SNS mentions over previous 7 days." },
    { name = "book_age_days", type = "INTEGER", mode = "NULLABLE", description = "Days since publication date." },
    { name = "is_bestseller_flag", type = "BOOLEAN", mode = "NULLABLE", description = "Whether listed as an Aladin bestseller." },
    { name = "on_hand_total", type = "INTEGER", mode = "NULLABLE", description = "Company-wide available stock." },
    { name = "days_since_last_stockout", type = "INTEGER", mode = "NULLABLE", description = "Days since last stockout." },
  ])
}

resource "google_bigquery_table" "books_static" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.books_static

  deletion_protection = false
  description         = "Static per-ISBN attributes for Vertex AI forecasting."

  clustering = ["isbn13"]

  schema = jsonencode([
    { name = "isbn13", type = "STRING", mode = "NULLABLE", description = "Book ISBN-13." },
    { name = "author", type = "STRING", mode = "NULLABLE", description = "Primary author." },
    { name = "publisher", type = "STRING", mode = "NULLABLE", description = "Publisher name." },
    { name = "category_id", type = "INTEGER", mode = "NULLABLE", description = "Aladin category id." },
    { name = "category_name", type = "STRING", mode = "NULLABLE", description = "Category path." },
    { name = "price_standard", type = "INTEGER", mode = "NULLABLE", description = "List price in KRW." },
    { name = "price_sales", type = "INTEGER", mode = "NULLABLE", description = "Sales price in KRW." },
    { name = "price_tier", type = "STRING", mode = "NULLABLE", description = "LOW, MID, or HIGH." },
    { name = "sales_point", type = "INTEGER", mode = "NULLABLE", description = "Aladin sales point." },
    { name = "item_page", type = "INTEGER", mode = "NULLABLE", description = "Page count." },
    { name = "is_bestseller_flag", type = "BOOLEAN", mode = "NULLABLE", description = "Whether listed as an Aladin bestseller." },
    { name = "author_debut_year", type = "INTEGER", mode = "NULLABLE", description = "Author debut year." },
    { name = "author_experience_years", type = "INTEGER", mode = "NULLABLE", description = "Derived author experience years." },
    { name = "author_past_books_count", type = "INTEGER", mode = "NULLABLE", description = "Author's past published book count." },
  ])
}

resource "google_bigquery_table" "locations_static" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.locations_static

  deletion_protection = false
  description         = "Static per-location attributes for Vertex AI forecasting."

  clustering = ["location_id"]

  schema = jsonencode([
    { name = "location_id", type = "INTEGER", mode = "NULLABLE", description = "Location id." },
    { name = "location_type", type = "STRING", mode = "NULLABLE", description = "WH, STORE_OFFLINE, or STORE_ONLINE." },
    { name = "wh_id", type = "INTEGER", mode = "NULLABLE", description = "Warehouse region id, 1 or 2." },
    { name = "size", type = "STRING", mode = "NULLABLE", description = "Offline store size: L, M, or S." },
    { name = "is_virtual", type = "BOOLEAN", mode = "NULLABLE", description = "Whether this is a virtual online store location." },
  ])
}

resource "google_bigquery_table" "store_location_map" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.store_location_map

  deletion_protection = false
  description         = "Maps sales store ids to dashboard locations and real inventory locations."

  clustering = ["store_id"]

  schema = jsonencode([
    { name = "store_id", type = "INTEGER", mode = "NULLABLE", description = "Sales store id, 1-12." },
    { name = "location_id", type = "INTEGER", mode = "NULLABLE", description = "Dashboard or sales location id." },
    { name = "inventory_location_id", type = "INTEGER", mode = "NULLABLE", description = "Real inventory location id used for stock joins." },
    { name = "mapping_rule", type = "STRING", mode = "NULLABLE", description = "Human-readable mapping rule." },
  ])
}

resource "google_bigquery_table" "forecast_results" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.forecast_results

  deletion_protection = false
  description         = "Vertex AI forecast output."

  time_partitioning {
    type  = "DAY"
    field = "prediction_date"
  }

  clustering = ["isbn13", "store_id"]

  schema = jsonencode([
    { name = "prediction_date", type = "DATE", mode = "REQUIRED", description = "Prediction execution date." },
    { name = "target_date", type = "DATE", mode = "REQUIRED", description = "Forecast target date, D+1 through D+5." },
    { name = "isbn13", type = "STRING", mode = "REQUIRED", description = "Forecast target ISBN-13." },
    { name = "store_id", type = "INTEGER", mode = "REQUIRED", description = "Forecast target store id." },
    { name = "predicted_demand", type = "NUMERIC", mode = "NULLABLE", description = "Predicted sales quantity." },
    { name = "confidence_low", type = "NUMERIC", mode = "NULLABLE", description = "80 percent confidence interval lower bound." },
    { name = "confidence_high", type = "NUMERIC", mode = "NULLABLE", description = "80 percent confidence interval upper bound." },
    { name = "model_version", type = "STRING", mode = "NULLABLE", description = "Vertex AI Model Registry version." },
    { name = "inference_ms", type = "INTEGER", mode = "NULLABLE", description = "Inference latency in milliseconds." },
  ])
}

resource "google_bigquery_table" "training_dataset" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.bigquery_table_ids.training_dataset

  deletion_protection = false
  description         = "Materialized feature set for existing-book demand forecasting."

  time_partitioning {
    type  = "DAY"
    field = "sale_date"
  }

  clustering = ["isbn13", "store_id"]

  schema = jsonencode([
    { name = "sale_date", type = "DATE", mode = "REQUIRED", description = "Sales date." },
    { name = "isbn13", type = "STRING", mode = "REQUIRED", description = "Book ISBN-13." },
    { name = "store_id", type = "INTEGER", mode = "REQUIRED", description = "Store id." },
    { name = "qty_sold", type = "FLOAT", mode = "NULLABLE", description = "Training label for demand forecasting." },
    { name = "revenue", type = "FLOAT", mode = "NULLABLE", description = "Daily revenue at training grain." },
    { name = "avg_price", type = "FLOAT", mode = "NULLABLE", description = "Average selling price at training grain." },
    { name = "tx_count", type = "FLOAT", mode = "NULLABLE", description = "Transaction count at training grain." },
    { name = "category_id", type = "INTEGER", mode = "NULLABLE", description = "Book category id." },
    { name = "category_name", type = "STRING", mode = "NULLABLE", description = "Book category path." },
    { name = "publisher", type = "STRING", mode = "NULLABLE", description = "Publisher name." },
    { name = "author", type = "STRING", mode = "NULLABLE", description = "Primary author." },
    { name = "price_standard", type = "INTEGER", mode = "NULLABLE", description = "List price in KRW." },
    { name = "price_sales", type = "INTEGER", mode = "NULLABLE", description = "Sales price in KRW." },
    { name = "price_tier", type = "STRING", mode = "NULLABLE", description = "LOW, MID, or HIGH." },
    { name = "sales_point", type = "INTEGER", mode = "NULLABLE", description = "Aladin sales point." },
    { name = "item_page", type = "INTEGER", mode = "NULLABLE", description = "Page count." },
    { name = "book_is_bestseller_flag", type = "BOOLEAN", mode = "NULLABLE", description = "Static bestseller flag from books_static." },
    { name = "author_past_books_count", type = "INTEGER", mode = "NULLABLE", description = "Author's past published book count." },
    { name = "author_debut_year", type = "INTEGER", mode = "NULLABLE", description = "Author debut year." },
    { name = "author_experience_years", type = "INTEGER", mode = "NULLABLE", description = "Derived author experience years." },
    { name = "location_id", type = "INTEGER", mode = "NULLABLE", description = "Sales or dashboard location id." },
    { name = "inventory_location_id", type = "INTEGER", mode = "NULLABLE", description = "Real inventory location id used for stock joins." },
    { name = "location_type", type = "STRING", mode = "NULLABLE", description = "WH, STORE_OFFLINE, or STORE_ONLINE." },
    { name = "wh_id", type = "INTEGER", mode = "NULLABLE", description = "Warehouse region id." },
    { name = "size", type = "STRING", mode = "NULLABLE", description = "Location size." },
    { name = "is_virtual", type = "BOOLEAN", mode = "NULLABLE", description = "Whether this is a virtual online location." },
    { name = "is_holiday", type = "BOOLEAN", mode = "NULLABLE", description = "Whether the sale date is a public holiday." },
    { name = "holiday_name", type = "STRING", mode = "NULLABLE", description = "Holiday name." },
    { name = "season", type = "STRING", mode = "NULLABLE", description = "SPRING, SUMMER, FALL, or WINTER." },
    { name = "day_of_week", type = "INTEGER", mode = "NULLABLE", description = "Day of week, 1=Monday through 7=Sunday." },
    { name = "is_weekend", type = "BOOLEAN", mode = "NULLABLE", description = "Whether the sale date is a weekend." },
    { name = "month", type = "INTEGER", mode = "NULLABLE", description = "Month number, 1-12." },
    { name = "event_nearby_days", type = "FLOAT", mode = "NULLABLE", description = "Days until next nearby event or holiday." },
    { name = "sns_mentions_1d", type = "FLOAT", mode = "NULLABLE", description = "SNS mentions from previous day." },
    { name = "sns_mentions_7d", type = "FLOAT", mode = "NULLABLE", description = "SNS mentions over previous 7 days." },
    { name = "on_hand_total", type = "FLOAT", mode = "NULLABLE", description = "Company-wide available stock." },
    { name = "days_since_last_stockout", type = "FLOAT", mode = "NULLABLE", description = "Days since last stockout." },
    { name = "book_age_days", type = "FLOAT", mode = "NULLABLE", description = "Days since publication date." },
    { name = "is_bestseller_flag", type = "BOOLEAN", mode = "NULLABLE", description = "Whether listed as an Aladin bestseller." },
    { name = "on_hand", type = "FLOAT", mode = "NULLABLE", description = "On-hand quantity at location." },
    { name = "reserved_qty", type = "FLOAT", mode = "NULLABLE", description = "Reserved quantity at location." },
    { name = "safety_stock", type = "FLOAT", mode = "NULLABLE", description = "Safety stock threshold at location." },
  ])
}
