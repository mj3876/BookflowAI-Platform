output "cloud_function_uris" {
  description = "Internal Cloud Function URIs."
  value = {
    for key, function in google_cloudfunctions2_function.content :
    key => function.service_config[0].uri
  }
}

output "workflow_id" {
  description = "Content routing workflow id."
  value       = google_workflows_workflow.gcs_router.id
}

output "eventarc_trigger_id" {
  description = "GCS finalize Eventarc trigger id."
  value       = google_eventarc_trigger.gcs_finalize.id
}

output "vertex_endpoint_name" {
  description = "Vertex AI private endpoint resource name."
  value       = google_vertex_ai_endpoint.forecast.name
}

output "bigquery_view_ids" {
  description = "Operational BigQuery views used by feature assembly and batch prediction."
  value = {
    existing_books_training_features = google_bigquery_table.existing_books_training_features_view.table_id
    new_book_feature_candidates      = google_bigquery_table.new_book_feature_candidates_view.table_id
    batch_prediction_input           = google_bigquery_table.batch_prediction_input_view.table_id
  }
}

output "storage_transfer_job_name" {
  description = "Storage Transfer job name when the AWS S3 Mart transfer is enabled."
  value       = var.storage_transfer_enabled ? google_storage_transfer_job.aws_mart_to_gcs_staging[0].name : null
}

output "daily_existing_books_scheduler_name" {
  description = "Cloud Scheduler job name when daily existing-books workflow execution is enabled."
  value       = var.enable_daily_existing_books_schedule ? google_cloud_scheduler_job.daily_existing_books_workflow[0].name : null
}
