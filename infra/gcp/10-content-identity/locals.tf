locals {
  region              = var.region
  dataset_id          = var.dataset_id
  staging_bucket_name = coalesce(var.staging_bucket_name, "${var.project_id}-bookflow-staging")
  models_bucket_name  = coalesce(var.models_bucket_name, "${var.project_id}-bookflow-models")
}
