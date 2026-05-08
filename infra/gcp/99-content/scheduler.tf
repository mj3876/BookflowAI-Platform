resource "google_service_account" "daily_existing_books_scheduler" {
  count = var.enable_daily_existing_books_schedule ? 1 : 0

  account_id   = "bookflow-daily-forecast"
  project      = var.project_id
  display_name = "BOOKFLOW daily existing-books forecast scheduler"
}

resource "google_project_iam_member" "daily_existing_books_scheduler_invoker" {
  count = var.enable_daily_existing_books_schedule ? 1 : 0

  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.daily_existing_books_scheduler[0].email}"
}

resource "google_cloud_scheduler_job" "daily_existing_books_workflow" {
  count = var.enable_daily_existing_books_schedule ? 1 : 0

  name        = "bookflow-daily-existing-books-forecast"
  project     = var.project_id
  region      = local.region
  description = "Starts the GCP existing-books Workflows path for daily operations."
  schedule    = var.daily_existing_books_schedule
  time_zone   = var.daily_existing_books_schedule_timezone

  lifecycle {
    precondition {
      condition     = var.daily_existing_books_source_object != null && var.daily_existing_books_source_object != ""
      error_message = "daily_existing_books_source_object is required when enable_daily_existing_books_schedule is true."
    }
  }

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${local.region}/workflows/${google_workflows_workflow.gcs_router.name}/executions"

    headers = {
      Content-Type = "application/json"
    }

    body = base64encode(jsonencode({
      argument = jsonencode({
        bucket = data.google_storage_bucket.staging.name
        name   = var.daily_existing_books_source_object
      })
    }))

    oauth_token {
      service_account_email = google_service_account.daily_existing_books_scheduler[0].email
    }
  }

  depends_on = [
    google_project_service.required["cloudscheduler.googleapis.com"],
    google_project_service.required["workflowexecutions.googleapis.com"],
    google_project_iam_member.daily_existing_books_scheduler_invoker,
    google_workflows_workflow.gcs_router,
  ]
}
