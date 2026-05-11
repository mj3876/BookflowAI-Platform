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
        isbn13,
        store_id,
        day_of_week,
        month,
        COALESCE(on_hand, 0) AS on_hand,
        CAST(COALESCE(is_holiday, FALSE) AS INT64) AS holiday_flag,
        COALESCE(event_nearby_days, 0) AS event_nearby_days,
        COALESCE(sns_mentions_1d, 0) AS sns_mentions_1d,
        COALESCE(sns_mentions_7d, 0) AS sns_mentions_7d
      FROM `${var.project_id}.${local.dataset_id}.${var.features_table}`
      WHERE SAFE_CAST(feature_date AS DATE) = CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")
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
