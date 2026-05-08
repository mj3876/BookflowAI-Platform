data "google_storage_transfer_project_service_account" "default" {
  project = var.project_id
}

resource "google_storage_bucket_iam_member" "storage_transfer_staging_object_admin" {
  count = var.storage_transfer_enabled ? 1 : 0

  bucket = data.google_storage_bucket.staging.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_storage_transfer_project_service_account.default.email}"
}

resource "google_storage_transfer_job" "aws_mart_to_gcs_staging" {
  count = var.storage_transfer_enabled ? 1 : 0

  project     = var.project_id
  description = "BOOKFLOW AWS S3 Mart to GCS staging transfer"
  status      = "ENABLED"

  lifecycle {
    precondition {
      condition     = var.aws_mart_bucket_name != null && var.aws_mart_bucket_name != ""
      error_message = "aws_mart_bucket_name is required when storage_transfer_enabled is true."
    }

    precondition {
      condition     = var.aws_storage_transfer_role_arn != null && var.aws_storage_transfer_role_arn != ""
      error_message = "aws_storage_transfer_role_arn is required when storage_transfer_enabled is true."
    }

    precondition {
      condition     = var.storage_transfer_schedule_start_date != null
      error_message = "storage_transfer_schedule_start_date is required when storage_transfer_enabled is true."
    }

    precondition {
      condition     = var.storage_transfer_start_time_of_day != null
      error_message = "storage_transfer_start_time_of_day is required when storage_transfer_enabled is true."
    }
  }

  transfer_spec {
    aws_s3_data_source {
      bucket_name = var.aws_mart_bucket_name
      path        = var.aws_mart_prefix
      role_arn    = var.aws_storage_transfer_role_arn
    }

    gcs_data_sink {
      bucket_name = data.google_storage_bucket.staging.name
      path        = var.storage_transfer_sink_prefix
    }

    transfer_options {
      overwrite_objects_already_existing_in_sink = true
      delete_objects_unique_in_sink              = false
      delete_objects_from_source_after_transfer  = false
    }
  }

  schedule {
    schedule_start_date {
      year  = var.storage_transfer_schedule_start_date.year
      month = var.storage_transfer_schedule_start_date.month
      day   = var.storage_transfer_schedule_start_date.day
    }

    start_time_of_day {
      hours   = var.storage_transfer_start_time_of_day.hours
      minutes = var.storage_transfer_start_time_of_day.minutes
      seconds = var.storage_transfer_start_time_of_day.seconds
      nanos   = var.storage_transfer_start_time_of_day.nanos
    }
  }

  depends_on = [
    google_project_service.required["storagetransfer.googleapis.com"],
    google_storage_bucket_iam_member.storage_transfer_staging_object_admin,
  ]
}
