resource "google_service_account" "bq_load" {
  account_id   = "bookflow-bq-load"
  project      = var.project_id
  display_name = "BOOKFLOW BigQuery load function"
}

resource "google_service_account" "feature_assemble" {
  account_id   = "bookflow-feature-assemble"
  project      = var.project_id
  display_name = "BOOKFLOW feature assembly function"
}

resource "google_service_account" "vertex_invoke" {
  account_id   = "bookflow-vertex-invoke"
  project      = var.project_id
  display_name = "BOOKFLOW Vertex endpoint invocation function"
}

resource "google_service_account" "workflow" {
  account_id   = "bookflow-gcs-router"
  project      = var.project_id
  display_name = "BOOKFLOW content workflow"
}

resource "google_service_account" "eventarc" {
  account_id   = "bookflow-eventarc-content"
  project      = var.project_id
  display_name = "BOOKFLOW content Eventarc trigger"
}

resource "google_service_account" "vertex_pipeline" {
  account_id   = "bookflow-vertex-pipeline"
  project      = var.project_id
  display_name = "BOOKFLOW Vertex AI pipeline runner"
}

resource "google_project_iam_member" "bq_load_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.bq_load.email}"
}

resource "google_bigquery_dataset_iam_member" "bq_load_data_editor" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.bq_load.email}"
}

resource "google_storage_bucket_iam_member" "bq_load_staging_viewer" {
  bucket = data.google_storage_bucket.staging.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.bq_load.email}"
}

resource "google_project_iam_member" "feature_assemble_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.feature_assemble.email}"
}

resource "google_bigquery_dataset_iam_member" "feature_assemble_data_viewer" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.feature_assemble.email}"
}

resource "google_project_iam_member" "vertex_invoke_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.vertex_invoke.email}"
}

resource "google_project_iam_member" "vertex_invoke_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.vertex_invoke.email}"
}

resource "google_bigquery_dataset_iam_member" "vertex_invoke_data_editor" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.vertex_invoke.email}"
}

resource "google_project_iam_member" "workflow_aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

resource "google_project_iam_member" "eventarc_event_receiver" {
  project = var.project_id
  role    = "roles/eventarc.eventReceiver"
  member  = "serviceAccount:${google_service_account.eventarc.email}"
}

resource "google_project_iam_member" "eventarc_workflows_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.eventarc.email}"
}

resource "google_project_iam_member" "gcs_pubsub_publisher" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_project_iam_member" "vertex_pipeline_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.vertex_pipeline.email}"
}

resource "google_project_iam_member" "vertex_pipeline_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.vertex_pipeline.email}"
}

resource "google_bigquery_dataset_iam_member" "vertex_pipeline_bq_editor" {
  project    = var.project_id
  dataset_id = data.google_bigquery_dataset.bookflow_dw.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.vertex_pipeline.email}"
}

resource "google_storage_bucket_iam_member" "vertex_pipeline_models_admin" {
  bucket = data.google_storage_bucket.models.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.vertex_pipeline.email}"
}
