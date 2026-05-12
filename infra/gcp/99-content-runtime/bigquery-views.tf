resource "google_bigquery_table" "new_book_forecast" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.new_book_forecast_table

  deletion_protection = false
  description         = "Store-level demand predictions for new books, written by the new-book-inference Cloud Function."
  labels              = var.labels

  schema = jsonencode([
    { name = "isbn13",                 type = "STRING",  mode = "REQUIRED" },
    { name = "store_id",               type = "INTEGER", mode = "REQUIRED" },
    { name = "wh_id",                  type = "INTEGER", mode = "NULLABLE" },
    { name = "predicted_daily_demand", type = "FLOAT",   mode = "REQUIRED" },
    { name = "predicted_30d_qty",      type = "INTEGER", mode = "REQUIRED" },
    { name = "prediction_date",        type = "DATE",    mode = "REQUIRED" },
    { name = "model_version",          type = "STRING",  mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "wh_forecast_view" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.wh_forecast_view_id

  deletion_protection = false
  description         = "Warehouse-level aggregated forecast: joins forecast_results with store_location_map to roll up store predictions by wh_id."
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        ls.wh_id,
        f.isbn13,
        f.target_date,
        f.prediction_date,
        SUM(f.predicted_demand)   AS predicted_demand,
        SUM(f.confidence_low)     AS confidence_low,
        SUM(f.confidence_high)    AS confidence_high
      FROM `${var.project_id}.${local.dataset_id}.${var.forecast_table}` f
      JOIN `${var.project_id}.${local.dataset_id}.${var.store_location_map_table}` slm
        ON slm.store_id = f.store_id
      JOIN `${var.project_id}.${local.dataset_id}.${var.locations_static_table}` ls
        ON ls.location_id = slm.inventory_location_id
      GROUP BY ls.wh_id, f.isbn13, f.target_date, f.prediction_date
    SQL
  }
}

resource "google_bigquery_table" "existing_books_training_features_view" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.existing_books_training_view_id

  deletion_protection = false
  description         = "Feature view for existing-book training and operational inspection."
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT *
      FROM `${var.project_id}.${local.dataset_id}.${var.training_table}`
    SQL
  }
}

resource "google_bigquery_table" "new_book_feature_candidates_view" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.new_book_feature_view_id

  deletion_protection = false
  description         = "New-book feature candidates assembled from BOOKFLOW warehouse tables for Vertex real-time inference."
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        f.isbn13,
        slm.store_id,
        f.day_of_week,
        f.month,
        COALESCE(inv.on_hand, 0) AS on_hand,
        CAST(COALESCE(f.is_holiday, FALSE) AS INT64) AS holiday_flag,
        COALESCE(f.event_nearby_days, 0) AS event_nearby_days,
        COALESCE(f.sns_mentions_1d, 0) AS sns_mentions_1d,
        COALESCE(f.sns_mentions_7d, 0) AS sns_mentions_7d
      FROM `${var.project_id}.${local.dataset_id}.${var.features_table}` f
      CROSS JOIN `${var.project_id}.${local.dataset_id}.${var.store_location_map_table}` slm
      LEFT JOIN `${var.project_id}.${local.dataset_id}.${var.inventory_daily_table}` inv
        ON inv.isbn13 = f.isbn13
        AND inv.location_id = slm.inventory_location_id
        AND inv.snapshot_date = SAFE_CAST(f.feature_date AS DATE)
      WHERE SAFE_CAST(f.feature_date AS DATE) = CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")
    SQL
  }
}

resource "google_bigquery_table" "batch_prediction_input_view" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  table_id   = var.batch_prediction_input_view_id

  deletion_protection = false
  description         = "BigQuery source view for Vertex AI BatchPredictionJob."
  labels              = var.labels

  view {
    use_legacy_sql = false
    query          = <<-SQL
      SELECT
        isbn13,
        store_id,
        day_of_week,
        month,
        on_hand,
        holiday_flag,
        event_nearby_days,
        sns_mentions_1d,
        sns_mentions_7d
      FROM `${var.project_id}.${local.dataset_id}.${var.new_book_feature_view_id}`
    SQL
  }

  depends_on = [
    google_bigquery_table.new_book_feature_candidates_view,
  ]
}
