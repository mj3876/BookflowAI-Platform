variable "project_id" {
  description = "GCP project ID for BOOKFLOW."
  type        = string
}

variable "project_name" {
  description = "Human-readable project name."
  type        = string
  default     = "BOOKFLOW v6.2"
}

variable "region" {
  description = "Primary GCP region."
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "Primary GCP zone."
  type        = string
  default     = "asia-northeast1-a"
}

variable "bigquery_location" {
  description = "BigQuery dataset location for BOOKFLOW data warehouse resources."
  type        = string
}

variable "bigquery_dataset_id" {
  description = "BigQuery dataset id for BOOKFLOW analytics resources."
  type        = string
}

variable "bigquery_table_ids" {
  description = "Logical BigQuery table ids used by the BOOKFLOW analytics and Vertex AI pipelines."
  type        = map(string)
}

variable "vpc_cidr" {
  description = "GCP internal CIDR range allowed by the BOOKFLOW foundation firewall."
  type        = string
}

variable "main_subnet_cidr" {
  description = "Primary GCP subnet CIDR advertised to AWS through the cross-cloud route tables."
  type        = string
}

variable "vpc_connector_cidr" {
  description = "Serverless VPC Access connector CIDR. Must not overlap PSC endpoint IP allocation."
  type        = string
}

variable "aws_allowed_cidrs" {
  description = "AWS CIDR ranges allowed to reach the GCP foundation VPC over TGW/VPN."
  type        = list(string)
}

variable "github_owner" {
  description = "GitHub owner or organization allowed to federate into the GCP Terraform deployer service account."
  type        = string
}

variable "github_repositories" {
  description = "GitHub repositories allowed to federate into the GCP Terraform deployer service account."
  type        = list(string)

  validation {
    condition     = length(var.github_repositories) > 0
    error_message = "github_repositories must contain at least one repository name."
  }
}

variable "terraform_deployer_roles" {
  description = "Project IAM roles granted to the GitHub OIDC Terraform deployer service account."
  type        = set(string)
  default = [
    "roles/aiplatform.admin",
    "roles/artifactregistry.admin",
    "roles/bigquery.admin",
    "roles/cloudbuild.builds.editor",
    "roles/cloudfunctions.admin",
    "roles/compute.networkAdmin",
    "roles/eventarc.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/pubsub.admin",
    "roles/run.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/storage.admin",
    "roles/vpcaccess.admin",
    "roles/workflows.admin",
  ]
}

variable "labels" {
  description = "Common labels applied to supported resources."
  type        = map(string)
  default = {
    project     = "bookflow"
    environment = "dev"
    owner       = "gcp"
    platform    = "multi-cloud"
  }
}

locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudscheduler.googleapis.com",
    "compute.googleapis.com",
    "eventarc.googleapis.com",
    "iam.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
    "servicenetworking.googleapis.com",
  ])
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
