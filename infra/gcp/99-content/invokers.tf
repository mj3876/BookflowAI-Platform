resource "google_cloud_run_service_iam_member" "workflow_function_invoker" {
  for_each = google_cloudfunctions2_function.content

  project  = var.project_id
  location = local.region
  service  = each.value.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.workflow.email}"
}
