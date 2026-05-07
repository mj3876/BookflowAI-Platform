resource "google_eventarc_trigger" "gcs_finalize" {
  name            = "bookflow-gcs-finalize-content"
  project         = var.project_id
  location        = local.region
  service_account = google_service_account.eventarc.email
  labels          = var.labels

  matching_criteria {
    attribute = "type"
    value     = "google.cloud.storage.object.v1.finalized"
  }

  matching_criteria {
    attribute = "bucket"
    value     = data.google_storage_bucket.staging.name
  }

  destination {
    workflow = google_workflows_workflow.gcs_router.id
  }

  depends_on = [
    google_project_service.required["eventarc.googleapis.com"],
    google_project_service.required["pubsub.googleapis.com"],
    google_project_iam_member.eventarc_event_receiver,
    google_project_iam_member.eventarc_workflows_invoker,
    google_project_iam_member.gcs_pubsub_publisher,
  ]
}
