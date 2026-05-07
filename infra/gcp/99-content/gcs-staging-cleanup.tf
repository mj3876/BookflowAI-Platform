resource "google_service_account" "staging_cleanup" {
  account_id   = "bookflow-staging-cleanup"
  project      = var.project_id
  display_name = "BOOKFLOW GCS staging cleanup"
}

resource "google_storage_bucket_iam_member" "staging_cleanup_object_admin" {
  bucket = data.google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.staging_cleanup.email}"
}

resource "google_workflows_workflow" "staging_cleanup" {
  name            = "bookflow-staging-cleanup-4h"
  project         = var.project_id
  region          = local.region
  service_account = google_service_account.staging_cleanup.email
  description     = "Deletes BOOKFLOW GCS staging objects older than 4 hours."

  source_contents = <<-YAML
main:
  params: [args]
  steps:
    - init:
        assign:
          - bucket: $${args.bucket}
          - cutoff_epoch: $${sys.now() - 14400}
          - deleted_count: 0
    - list_objects:
        call: googleapis.storage.v1.objects.list
        args:
          bucket: $${bucket}
        result: object_list
    - delete_old_objects:
        for:
          value: object
          in: $${default(map.get(object_list, "items"), [])}
          steps:
            - check_age:
                switch:
                  - condition: $${time.parse(object.timeCreated) < cutoff_epoch}
                    steps:
                      - delete_object:
                          call: googleapis.storage.v1.objects.delete
                          args:
                            bucket: $${bucket}
                            object: $${object.name}
                      - count_deleted:
                          assign:
                            - deleted_count: $${deleted_count + 1}
    - done:
        return:
          bucket: $${bucket}
          deleted_count: $${deleted_count}
YAML

  depends_on = [
    google_project_service.required["workflows.googleapis.com"],
  ]
}

resource "google_project_iam_member" "staging_cleanup_workflows_invoker" {
  project = var.project_id
  role    = "roles/workflows.invoker"
  member  = "serviceAccount:${google_service_account.staging_cleanup.email}"
}

resource "google_cloud_scheduler_job" "staging_cleanup" {
  name        = "bookflow-staging-cleanup-4h"
  project     = var.project_id
  region      = local.region
  description = "Runs BOOKFLOW staging cleanup for objects older than 4 hours."
  schedule    = "0 * * * *"
  time_zone   = "Asia/Tokyo"

  http_target {
    http_method = "POST"
    uri         = "https://workflowexecutions.googleapis.com/v1/projects/${var.project_id}/locations/${local.region}/workflows/${google_workflows_workflow.staging_cleanup.name}/executions"

    headers = {
      Content-Type = "application/json"
    }

    body = base64encode(jsonencode({
      argument = jsonencode({
        bucket = data.google_storage_bucket.staging.name
      })
    }))

    oauth_token {
      service_account_email = google_service_account.staging_cleanup.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.required["cloudscheduler.googleapis.com"],
    google_project_iam_member.staging_cleanup_workflows_invoker,
    google_storage_bucket_iam_member.staging_cleanup_object_admin,
  ]
}
