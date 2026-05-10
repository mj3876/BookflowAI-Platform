output "service_account_emails" {
  description = "Content runtime service account emails."
  value = {
    bq_load          = google_service_account.bq_load.email
    feature_assemble = google_service_account.feature_assemble.email
    vertex_invoke    = google_service_account.vertex_invoke.email
    workflow         = google_service_account.workflow.email
    eventarc         = google_service_account.eventarc.email
    vertex_pipeline  = google_service_account.vertex_pipeline.email
    staging_cleanup  = google_service_account.staging_cleanup.email
    daily_forecast   = google_service_account.daily_existing_books_scheduler.email
  }
}
