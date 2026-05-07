resource "google_vertex_ai_endpoint" "forecast" {
  name         = "bookflow-forecast-endpoint"
  display_name = "bookflow-forecast-endpoint"
  description  = "Private endpoint for BOOKFLOW demand forecasting inference."
  project      = var.project_id
  location     = local.region
  labels       = var.labels
  network      = "projects/${data.google_project.project.number}/global/networks/${data.google_compute_network.bookflow_vpc.name}"

  depends_on = [
    google_project_service.required["aiplatform.googleapis.com"],
  ]
}
