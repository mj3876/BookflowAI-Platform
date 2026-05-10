resource "google_project_service" "storagetransfer" {
  project            = var.project_id
  service            = "storagetransfer.googleapis.com"
  disable_on_destroy = false
}
