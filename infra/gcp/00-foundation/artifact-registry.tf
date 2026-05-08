resource "google_artifact_registry_repository" "gcf_artifacts" {
  project       = var.project_id
  location      = var.region
  repository_id = "gcf-artifacts"
  description   = "Docker repository for Cloud Functions 2nd gen build artifacts."
  format        = "DOCKER"

  labels = merge(var.labels, {
    purpose = "cloud-functions"
  })

  cleanup_policies {
    id     = "keep-recent-build-images"
    action = "KEEP"

    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old-build-images"
    action = "DELETE"

    condition {
      older_than = "2592000s"
    }
  }

  depends_on = [
    google_project_service.required["artifactregistry.googleapis.com"],
  ]
}
