variable "project_id" {
  description = "GCP project ID for BOOKFLOW content identity resources."
  type        = string
}

variable "region" {
  description = "Primary GCP region for content services."
  type        = string
  default     = "asia-northeast1"
}

variable "dataset_id" {
  description = "BigQuery dataset id."
  type        = string
}

variable "staging_bucket_name" {
  description = "Existing GCS staging bucket name. Defaults to the foundation naming convention."
  type        = string
  default     = null
}

variable "models_bucket_name" {
  description = "Existing GCS models bucket name. Defaults to the foundation naming convention."
  type        = string
  default     = null
}
