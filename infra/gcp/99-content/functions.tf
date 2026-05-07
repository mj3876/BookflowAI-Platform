data "archive_file" "function_source" {
  for_each = local.function_specs

  type        = "zip"
  source_dir  = "${path.module}/functions/${each.value.source_dir}"
  output_path = "${path.module}/functions/${each.value.zip_name}"
}

resource "google_storage_bucket_object" "function_source" {
  for_each = local.function_specs

  bucket       = data.google_storage_bucket.staging.name
  name         = "functions/${each.value.zip_name}"
  source       = data.archive_file.function_source[each.key].output_path
  content_type = "application/zip"

  detect_md5hash = data.archive_file.function_source[each.key].output_md5
}

resource "google_cloudfunctions2_function" "content" {
  for_each = local.function_specs

  name        = each.value.name
  project     = var.project_id
  location    = local.region
  description = each.value.description
  labels      = var.labels

  build_config {
    runtime     = each.value.runtime
    entry_point = each.value.entry_point

    source {
      storage_source {
        bucket     = google_storage_bucket_object.function_source[each.key].bucket
        object     = google_storage_bucket_object.function_source[each.key].name
        generation = google_storage_bucket_object.function_source[each.key].generation
      }
    }
  }

  service_config {
    min_instance_count = each.value.min_instance
    max_instance_count = each.value.max_instance
    available_memory   = each.value.memory
    timeout_seconds    = each.value.timeout
    ingress_settings   = "ALLOW_INTERNAL_ONLY"
    service_account_email = {
      bq_load          = google_service_account.bq_load.email
      feature_assemble = google_service_account.feature_assemble.email
      vertex_invoke    = google_service_account.vertex_invoke.email
    }[each.key]
    vpc_connector                  = data.google_vpc_access_connector.bookflow.id
    vpc_connector_egress_settings  = "ALL_TRAFFIC"
    all_traffic_on_latest_revision = true
    environment_variables = merge(each.value.env, {
      BOOKFLOW_PROJECT_ID = var.project_id
    })
  }

  depends_on = [
    google_project_service.required["cloudfunctions.googleapis.com"],
    google_project_service.required["run.googleapis.com"],
    google_project_service.required["artifactregistry.googleapis.com"],
    google_project_service.required["cloudbuild.googleapis.com"],
    data.google_vpc_access_connector.bookflow,
    google_storage_bucket_object.function_source,
  ]
}
