variable "project_id" {
  description = "GCP project ID for BOOKFLOW."
  type        = string
}

variable "region" {
  description = "Primary GCP region."
  type        = string
  default     = "asia-northeast1"
}

variable "vpc_name" {
  description = "Existing GCP VPC name created by the foundation layer."
  type        = string
}

data "google_compute_network" "bookflow_vpc" {
  name    = var.vpc_name
  project = var.project_id
}
