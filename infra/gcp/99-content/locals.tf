locals {
  required_services = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "bigquery.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "compute.googleapis.com",
    "eventarc.googleapis.com",
    "iam.googleapis.com",
    "pubsub.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
    "storagetransfer.googleapis.com",
    "vpcaccess.googleapis.com",
    "workflowexecutions.googleapis.com",
    "workflows.googleapis.com",
  ])

  region             = var.region
  vpc_name           = var.vpc_name
  vpc_connector_name = var.vpc_connector_name
  dataset_id         = var.dataset_id

  staging_bucket_name          = coalesce(var.staging_bucket_name, "${var.project_id}-bookflow-staging")
  models_bucket_name           = coalesce(var.models_bucket_name, "${var.project_id}-bookflow-models")
  vertex_pipeline_template_uri = coalesce(var.vertex_pipeline_template_uri, "gs://${local.models_bucket_name}/${var.vertex_pipeline_template_object}")
  vertex_pipeline_root         = coalesce(var.vertex_pipeline_root, "gs://${var.project_id}-bookflow-models/pipeline-root")
  vertex_endpoint_resource     = coalesce(var.vertex_endpoint_resource_name, google_vertex_ai_endpoint.forecast.name)

  function_specs = {
    bq_load = {
      name         = "bookflow-bq-load"
      description  = "Loads finalized GCS staging objects into BigQuery tables."
      entry_point  = "handler"
      runtime      = "python312"
      memory       = "512M"
      timeout      = 540
      min_instance = 0
      max_instance = 3
      source_dir   = "bq-load"
      zip_name     = "bookflow-bq-load.zip"
      env = {
        BOOKFLOW_DATASET_ID     = var.dataset_id
        BOOKFLOW_BQ_LOCATION    = var.bigquery_location
        BOOKFLOW_STAGING_BUCKET = local.staging_bucket_name
        BOOKFLOW_LOAD_TABLES = join(",", [
          var.sales_table,
          var.inventory_daily_table,
          var.features_table,
          var.books_static_table,
          var.locations_static_table,
          var.store_location_map_table,
        ])
        BOOKFLOW_LOAD_TABLE_ALIASES = join(",", [
          for source_name, table_name in var.load_table_aliases : "${source_name}:${table_name}"
        ])
        BOOKFLOW_WRITE_DISPOSITION = "WRITE_APPEND"
      }
    }
    feature_assemble = {
      name         = "bookflow-feature-assemble"
      description  = "Assembles new-book inference features from BigQuery."
      entry_point  = "handler"
      runtime      = "python312"
      memory       = "512M"
      timeout      = 540
      min_instance = 0
      max_instance = 3
      source_dir   = "feature-assemble"
      zip_name     = "bookflow-feature-assemble.zip"
      env = {
        BOOKFLOW_DATASET_ID      = var.dataset_id
        BOOKFLOW_BQ_LOCATION     = var.bigquery_location
        BOOKFLOW_FEATURE_TABLE   = var.new_book_feature_view_id
        BOOKFLOW_FEATURE_COLUMNS = join(",", var.vertex_feature_columns)
        BOOKFLOW_FEATURE_TABLES = join(",", [
          var.sales_table,
          var.books_static_table,
          var.features_table,
          var.store_location_map_table,
        ])
      }
    }
    vertex_invoke = {
      name         = "bookflow-vertex-invoke"
      description  = "Invokes the existing Vertex AI private endpoint for new-book inference."
      entry_point  = "handler"
      runtime      = "python312"
      memory       = "1024M"
      timeout      = 540
      min_instance = 0
      max_instance = var.function_max_instance_count
      source_dir   = "vertex-invoke"
      zip_name     = "bookflow-vertex-invoke.zip"
      env = {
        BOOKFLOW_VERTEX_ENDPOINT    = local.vertex_endpoint_resource
        BOOKFLOW_VERTEX_LOCATION    = local.region
        BOOKFLOW_DATASET_ID         = var.dataset_id
        BOOKFLOW_VERTEX_INVOKE_MODE = var.vertex_invoke_mode
      }
    }
  }
}
