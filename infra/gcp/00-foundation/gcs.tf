resource "google_storage_bucket" "staging" {
  name                        = "${var.project_id}-bookflow-staging"
  project                     = var.project_id
  location                    = "ASIA-NORTHEAST1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false

  labels = var.labels

  versioning {
    enabled = true
  }

  public_access_prevention = "enforced"

  depends_on = [
    google_project_service.required["storage.googleapis.com"],
  ]
}

resource "google_storage_bucket" "models" {
  name                        = "${var.project_id}-bookflow-models"
  project                     = var.project_id
  location                    = "ASIA-NORTHEAST1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = false

  labels = merge(var.labels, {
    purpose = "vertex-models"
  })

  versioning {
    enabled = true
  }

  public_access_prevention = "enforced"

  depends_on = [
    google_project_service.required["storage.googleapis.com"],
  ]
}
