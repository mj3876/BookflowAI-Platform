data "google_storage_bucket" "staging" {
  name = local.staging_bucket_name
}

data "google_storage_bucket" "models" {
  name = local.models_bucket_name
}

data "google_bigquery_dataset" "bookflow_dw" {
  project    = var.project_id
  dataset_id = local.dataset_id
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id
}

data "google_storage_transfer_project_service_account" "default" {
  project = var.project_id

  depends_on = [
    google_project_service.storagetransfer,
  ]
}
