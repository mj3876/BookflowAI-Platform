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

variable "vpc_connector_cidr" {
  description = "CIDR range for the Serverless VPC Access connector."
  type        = string
  default     = "192.168.254.0/28"
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

variable "store_location_map_table" {
  description = "BigQuery source table mapping sales stores to dashboard and real inventory locations."
  type        = string
}

variable "load_table_aliases" {
  description = "Mapping from incoming GCS object stems to BigQuery load table ids."
  type        = map(string)
  default     = {}
}

variable "load_column_aliases" {
  description = "Per-target-table mapping from BigQuery target columns to incoming parquet source columns or numeric literals."
  type        = map(map(string))
  default     = {}
}

variable "forecast_table" {
  description = "BigQuery table where the Vertex pipeline writes forecast results."
  type        = string
}

variable "wh_forecast_view_id" {
  description = "BigQuery view id for warehouse-level aggregated forecast results."
  type        = string
  default     = "v_wh_forecast"
}

variable "existing_books_training_view_id" {
  description = "BigQuery view id exposing the existing-books training feature set."
  type        = string
  default     = "v_existing_books_training_features"
}

variable "new_book_feature_view_id" {
  description = "BigQuery view id used by feature-assemble for new-book inference features."
  type        = string
  default     = "v_new_book_feature_candidates"
}

variable "batch_prediction_input_view_id" {
  description = "BigQuery view id used as the Vertex AI batch prediction input."
  type        = string
  default     = "v_batch_prediction_input"
}

variable "vertex_feature_columns" {
  description = "Ordered feature columns sent to Vertex AI for real-time new-book inference."
  type        = list(string)
  default = [
    "isbn13",
    "store_id",
    "day_of_week",
    "month",
    "on_hand",
    "holiday_flag",
    "event_nearby_days",
    "sns_mentions_1d",
    "sns_mentions_7d",
  ]
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

variable "vertex_invoke_mode" {
  description = "vertex-invoke behavior. Use real for deployed Vertex endpoints or stub for Phase A dry runs."
  type        = string
  default     = "stub"

  validation {
    condition     = contains(["real", "stub"], var.vertex_invoke_mode)
    error_message = "vertex_invoke_mode must be either real or stub."
  }
}

variable "vertex_endpoint_resource_name" {
  description = "Full deployed Vertex AI Endpoint resource name used by vertex-invoke real mode. Defaults to the endpoint managed in this layer."
  type        = string
  default     = null
}

variable "enable_vertex_endpoint_smoke_test" {
  description = "Whether Workflows performs a real endpoint smoke prediction after feature assembly. This can incur Vertex AI cost."
  type        = bool
  default     = false
}

variable "enable_existing_books_pipeline" {
  description = "Whether Workflows starts the existing-books Vertex AI Pipeline after historical data loads."
  type        = bool
  default     = false
}

variable "existing_books_pipeline_trigger_object_regex" {
  description = "Regex for GCS mart objects allowed to trigger the existing-books Vertex AI Pipeline. Keep narrow to avoid one pipeline run per input table."
  type        = string
  default     = "^mart/features/.+[.]parquet$"
}

variable "enable_vertex_batch_prediction" {
  description = "Whether Workflows starts a Vertex AI BatchPredictionJob after the existing-books pipeline trigger."
  type        = bool
  default     = false
}

variable "vertex_batch_prediction_model" {
  description = "Full Vertex AI model resource name used for BatchPredictionJob. Required when enable_vertex_batch_prediction is true."
  type        = string
  default     = null
}

variable "vertex_batch_prediction_output_dataset_uri" {
  description = "BigQuery destination dataset URI for Vertex AI batch predictions."
  type        = string
  default     = null
}

variable "storage_transfer_enabled" {
  description = "Enable managed AWS S3 Mart to GCS staging Storage Transfer job."
  type        = bool
  default     = false
}

variable "aws_mart_bucket_name" {
  description = "AWS S3 Mart bucket name used by Storage Transfer. Required when storage_transfer_enabled is true."
  type        = string
  default     = null
}

variable "aws_mart_prefix" {
  description = "AWS S3 Mart prefix copied into GCS staging by Storage Transfer."
  type        = string
  default     = "mart/"
}

variable "aws_storage_transfer_role_arn" {
  description = "AWS IAM role ARN assumed by Google Storage Transfer Service for S3 reads."
  type        = string
  default     = null
}

variable "storage_transfer_sink_prefix" {
  description = "GCS staging prefix where Storage Transfer writes Mart objects."
  type        = string
  default     = "mart/"
}

variable "storage_transfer_schedule_start_date" {
  description = "Calendar date for the Storage Transfer daily schedule."
  type = object({
    year  = number
    month = number
    day   = number
  })
  default = null
}

variable "storage_transfer_start_time_of_day" {
  description = "Start time for the Storage Transfer daily schedule."
  type = object({
    hours   = number
    minutes = number
    seconds = number
    nanos   = number
  })
  default = null
}

variable "enable_daily_existing_books_schedule" {
  description = "Enable Cloud Scheduler to start the existing-books workflow path for daily operations."
  type        = bool
  default     = false
}

variable "daily_existing_books_source_object" {
  description = "GCS object path used as the daily scheduled existing-books workflow source object."
  type        = string
  default     = null
}

variable "daily_existing_books_schedule" {
  description = "Cloud Scheduler cron expression for daily existing-books workflow execution."
  type        = string
  default     = "0 4 * * *"
}

variable "new_book_model_name" {
  description = "BigQuery ML model name for the new-book demand forecast (trained on first-30-day demand of existing books)."
  type        = string
  default     = "bookflow_new_books_forecast"
}

variable "new_book_training_table" {
  description = "BigQuery table used as the new-book training dataset."
  type        = string
  default     = "new_book_training_dataset"
}

variable "new_book_forecast_table" {
  description = "BigQuery table where new-book inference results are written by the new-book-inference Cloud Function."
  type        = string
  default     = "new_book_forecast"
}

variable "new_book_pipeline_template_object" {
  description = "Object path in the models bucket for the compiled new-books Vertex AI Pipeline template."
  type        = string
  default     = "pipelines/bookflow-new-books-pipeline.json"
}

variable "daily_existing_books_schedule_timezone" {
  description = "Cloud Scheduler timezone for daily existing-books workflow execution."
  type        = string
  default     = "Asia/Seoul"
}
