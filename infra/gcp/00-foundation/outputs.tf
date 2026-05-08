output "vpc_name" {
  description = "BOOKFLOW GCP VPC name."
  value       = google_compute_network.bookflow_vpc.name
}

output "main_subnet_name" {
  description = "Primary BOOKFLOW GCP subnet name."
  value       = google_compute_subnetwork.bookflow_main.name
}

output "vpc_connector_name" {
  description = "Serverless VPC Access connector name used by 99-content."
  value       = google_vpc_access_connector.bookflow.name
}

output "staging_bucket_name" {
  description = "GCS staging bucket name."
  value       = google_storage_bucket.staging.name
}

output "models_bucket_name" {
  description = "GCS models bucket name."
  value       = google_storage_bucket.models.name
}

output "bigquery_dataset_id" {
  description = "BOOKFLOW BigQuery dataset id."
  value       = google_bigquery_dataset.bookflow_dw.dataset_id
}

output "artifact_registry_repository" {
  description = "Artifact Registry repository for Cloud Functions build artifacts."
  value       = google_artifact_registry_repository.gcf_artifacts.name
}

output "terraform_deployer_service_account_email" {
  description = "Federated GitHub Actions Terraform deployer service account email."
  value       = google_service_account.terraform_deployer.email
}

output "github_workload_identity_provider" {
  description = "Full Workload Identity Provider resource name for GitHub Actions auth."
  value       = google_iam_workload_identity_pool_provider.github.name
}
