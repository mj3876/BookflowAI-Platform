variable "project_id" {
  description = "GCP project ID for BOOKFLOW content pipeline."
  type        = string
}

variable "region" {
  description = "Primary GCP region for content services."
  type        = string
  default     = "asia-northeast1"
}

variable "bigquery_location" {
  description = "BigQuery dataset location."
  type        = string
  default     = "asia-northeast1"
}

variable "labels" {
  description = "Common labels applied to supported resources."
  type        = map(string)
  default = {
    project     = "bookflow"
    environment = "dev"
    owner       = "gcp"
    workload    = "content"
  }
}

variable "vpc_connector_name" {
  description = "Existing Serverless VPC Access connector name attached to bookflow-vpc."
  type        = string
  default     = "bookflow-vpc-conn"
}

variable "vpc_name" {
  description = "Existing BOOKFLOW VPC name."
  type        = string
  default     = "bookflow-vpc"
}

variable "dataset_id" {
  description = "BigQuery dataset id."
  type        = string
}

variable "training_table" {
  description = "BigQuery table used as the Vertex pipeline training dataset."
  type        = string
}

variable "sales_table" {
  description = "BigQuery source table containing sales facts for the existing-books forecast pipeline."
  type        = string
}

variable "inventory_daily_table" {
  description = "BigQuery source table containing daily inventory snapshots for the existing-books forecast pipeline."
  type        = string
}

variable "features_table" {
  description = "BigQuery source table containing feature values for the existing-books forecast pipeline."
  type        = string
}

variable "books_static_table" {
  description = "BigQuery source table containing static book attributes for feature engineering."
  type        = string
}

variable "locations_static_table" {
  description = "BigQuery source table containing static location attributes for feature engineering."
  type        = string
}

variable "load_table_aliases" {
  description = "Mapping from incoming GCS object stems to BigQuery load table ids."
  type        = map(string)
  default     = {}
}

variable "forecast_table" {
  description = "BigQuery table where the Vertex pipeline writes forecast results."
  type        = string
}

variable "existing_books_model_name" {
  description = "BigQuery ML model name created by the existing-books forecast pipeline."
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

variable "vertex_pipeline_template_uri" {
  description = "Vertex AI Pipeline template URI used for existing-book training and batch prediction."
  type        = string
  default     = null
}

variable "vertex_pipeline_template_object" {
  description = "Object path in the models bucket for the compiled existing-books Vertex AI Pipeline template."
  type        = string
}

variable "vertex_pipeline_root" {
  description = "Vertex AI Pipeline root path."
  type        = string
  default     = null
}

variable "function_max_instance_count" {
  description = "Default max instances for Cloud Functions."
  type        = number
  default     = 5
}
