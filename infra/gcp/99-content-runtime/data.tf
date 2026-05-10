#    (Project Number)    
data "google_project" "project" {
  project_id = var.project_id
}

data "google_compute_network" "bookflow_vpc" {
  name    = local.vpc_name
  project = var.project_id
}

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

data "google_service_account" "bq_load" {
  account_id = "bookflow-bq-load"
  project    = var.project_id
}

data "google_service_account" "feature_assemble" {
  account_id = "bookflow-feature-assemble"
  project    = var.project_id
}

data "google_service_account" "vertex_invoke" {
  account_id = "bookflow-vertex-invoke"
  project    = var.project_id
}

data "google_service_account" "workflow" {
  account_id = "bookflow-gcs-router"
  project    = var.project_id
}

data "google_service_account" "eventarc" {
  account_id = "bookflow-eventarc-content"
  project    = var.project_id
}

data "google_service_account" "vertex_pipeline" {
  account_id = "bookflow-vertex-pipeline"
  project    = var.project_id
}

data "google_service_account" "staging_cleanup" {
  account_id = "bookflow-staging-cleanup"
  project    = var.project_id
}

data "google_service_account" "daily_existing_books_scheduler" {
  count = var.enable_daily_existing_books_schedule ? 1 : 0

  account_id = "bookflow-daily-forecast"
  project    = var.project_id
}
