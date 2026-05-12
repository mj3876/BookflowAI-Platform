resource "google_service_account" "new_book_inference" {
  account_id   = "bookflow-new-book-infer"
  display_name = "BookFlow New-Book Inference"
  description  = "Runs BigQuery ML.PREDICT for new-book demand inference and writes results to new_book_forecast."
  project      = var.project_id
}

resource "google_project_iam_member" "new_book_inference_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.new_book_inference.email}"
}

resource "google_bigquery_dataset_iam_member" "new_book_inference_bq_data_editor" {
  project    = var.project_id
  dataset_id = local.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.new_book_inference.email}"
}
