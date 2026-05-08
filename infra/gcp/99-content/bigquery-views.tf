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
      SELECT
        sale_date,
        isbn13,
        store_id,
        qty_sold,
        revenue,
        avg_price,
        tx_count,
        category_id,
        category_name,
        publisher,
        author,
        price_standard,
        price_sales,
        price_tier,
        sales_point,
        item_page,
        book_is_bestseller_flag,
        author_past_books_count,
        author_debut_year,
        author_experience_years,
        location_id,
        inventory_location_id,
        location_type,
        wh_id,
        size,
        is_virtual,
        is_holiday,
        holiday_name,
        season,
        day_of_week,
        is_weekend,
        month,
        event_nearby_days,
        sns_mentions_1d,
        sns_mentions_7d,
        on_hand_total,
        days_since_last_stockout,
        book_age_days,
        is_bestseller_flag,
        on_hand,
        reserved_qty,
        safety_stock
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
        bs.isbn13,
        COALESCE(map.store_id, 0) AS store_id,
        EXTRACT(DAYOFWEEK FROM CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")) AS day_of_week,
        EXTRACT(MONTH FROM CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")) AS month,
        COALESCE(inv.on_hand, 0) AS on_hand,
        CAST(COALESCE(feat.is_holiday, FALSE) AS INT64) AS holiday_flag,
        COALESCE(feat.event_nearby_days, 0) AS event_nearby_days,
        COALESCE(feat.sns_mentions_1d, 0) AS sns_mentions_1d,
        COALESCE(feat.sns_mentions_7d, 0) AS sns_mentions_7d
      FROM `${var.project_id}.${local.dataset_id}.${var.books_static_table}` AS bs
      CROSS JOIN `${var.project_id}.${local.dataset_id}.${var.store_location_map_table}` AS map
      LEFT JOIN `${var.project_id}.${local.dataset_id}.${var.inventory_daily_table}` AS inv
        ON inv.isbn13 = bs.isbn13
       AND inv.location_id = map.inventory_location_id
       AND SAFE_CAST(inv.snapshot_date AS DATE) = CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")
      LEFT JOIN `${var.project_id}.${local.dataset_id}.${var.features_table}` AS feat
        ON feat.isbn13 = bs.isbn13
       AND SAFE_CAST(feat.feature_date AS DATE) = CURRENT_DATE("${var.daily_existing_books_schedule_timezone}")
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
