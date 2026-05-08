resource "google_workflows_workflow" "gcs_router" {
  name            = "bookflow-gcs-router"
  project         = var.project_id
  region          = local.region
  description     = "Routes GCS finalized objects through BQ load and Vertex AI workflows."
  service_account = google_service_account.workflow.email
  labels          = var.labels

  lifecycle {
    precondition {
      condition     = !var.enable_vertex_batch_prediction || (var.vertex_batch_prediction_model != null && var.vertex_batch_prediction_model != "")
      error_message = "vertex_batch_prediction_model is required when enable_vertex_batch_prediction is true."
    }

    precondition {
      condition     = !var.enable_vertex_endpoint_smoke_test || var.vertex_invoke_mode == "real"
      error_message = "vertex_invoke_mode must be real when enable_vertex_endpoint_smoke_test is true."
    }
  }

  source_contents = <<-YAML
main:
  params: [event]
  steps:
    - init:
        assign:
          - event_data: $${default(map.get(event, "data"), event)}
          - bucket: $${default(map.get(event, "bucket"), default(map.get(event_data, "bucket"), "${data.google_storage_bucket.staging.name}"))}
          - object_name: $${default(map.get(event, "name"), default(map.get(event_data, "name"), ""))}
          - request:
              bucket: $${bucket}
              object: $${object_name}
              dataset_id: "${local.dataset_id}"
              project_id: "${var.project_id}"
              bq_location: "${var.bigquery_location}"
    - filter_internal_artifacts:
        switch:
          - condition: $${len(text.find_all_regex(object_name, "^functions/|^pipelines/|\\.zip$|\\.json$")) > 0}
            next: return_ignored_artifact
        next: load_bigquery
    - return_ignored_artifact:
        return:
          route: "ignored_internal_artifact"
          object: $${object_name}
          status: "Ignored non-data object in staging bucket."
    - load_bigquery:
        call: http.post
        args:
          url: "${google_cloudfunctions2_function.content["bq_load"].service_config[0].uri}"
          auth:
            type: OIDC
          headers:
            Content-Type: "application/json"
          body: $${request}
        result: bq_load_result
    - choose_route:
        switch:
          - condition: $${len(text.find_all_regex(object_name, "new[-_]book|publisher|new_book")) > 0}
            next: assemble_new_book_features
        next: route_existing_books_pipeline
    - route_existing_books_pipeline:
        switch:
          - condition: $${${var.enable_existing_books_pipeline}}
            next: start_existing_book_pipeline
        next: return_historical_load_success
    - return_historical_load_success:
        return:
          route: "historical_data_load"
          status: "Success"
          bq_load: $${bq_load_result.body}
    - assemble_new_book_features:
        call: http.post
        args:
          url: "${google_cloudfunctions2_function.content["feature_assemble"].service_config[0].uri}"
          auth:
            type: OIDC
          headers:
            Content-Type: "application/json"
          body:
            project_id: "${var.project_id}"
            dataset_id: "${local.dataset_id}"
            bucket: $${bucket}
            object: $${object_name}
        result: feature_result
    - invoke_existing_endpoint:
        call: http.post
        args:
          url: "${google_cloudfunctions2_function.content["vertex_invoke"].service_config[0].uri}"
          auth:
            type: OIDC
          headers:
            Content-Type: "application/json"
          body:
            endpoint: "${local.vertex_endpoint_resource}"
            mode: "${var.vertex_invoke_mode}"
            features: $${feature_result.body}
        result: vertex_result
    - maybe_vertex_endpoint_smoke_test:
        switch:
          - condition: $${${var.enable_vertex_endpoint_smoke_test}}
            next: vertex_endpoint_smoke_test
        next: return_new_book
    - vertex_endpoint_smoke_test:
        call: http.post
        args:
          url: "${google_cloudfunctions2_function.content["vertex_invoke"].service_config[0].uri}"
          auth:
            type: OIDC
          headers:
            Content-Type: "application/json"
          body:
            endpoint: "${local.vertex_endpoint_resource}"
            mode: "real"
            features: $${feature_result.body}
        result: vertex_smoke_result
    - return_new_book_with_smoke_test:
        return:
          route: "new_book_realtime_inference_smoke_test"
          bq_load: $${bq_load_result.body}
          vertex: $${vertex_result.body}
          smoke_test: $${vertex_smoke_result.body}
    - return_new_book:
        return:
          route: "new_book_realtime_inference"
          bq_load: $${bq_load_result.body}
          vertex: $${vertex_result.body}
    - start_existing_book_pipeline:
        call: googleapis.aiplatform.v1.projects.locations.pipelineJobs.create
        args:
          parent: "projects/${var.project_id}/locations/${local.region}"
          region: "${local.region}"
          body:
            displayName: "bookflow-existing-books-forecast"
            serviceAccount: "${google_service_account.vertex_pipeline.email}"
            templateUri: "${local.vertex_pipeline_template_uri}"
            runtimeConfig:
              gcsOutputDirectory: "${local.vertex_pipeline_root}"
              parameterValues:
                project_id: "${var.project_id}"
                dataset_id: "${local.dataset_id}"
                bq_location: "${var.bigquery_location}"
                staging_bucket: "${data.google_storage_bucket.staging.name}"
                models_bucket: "${data.google_storage_bucket.models.name}"
                source_object: $${object_name}
                sales_table: "${var.sales_table}"
                inventory_table: "${var.inventory_daily_table}"
                features_table: "${var.features_table}"
                books_table: "${var.books_static_table}"
                locations_table: "${var.locations_static_table}"
                store_location_map_table: "${var.store_location_map_table}"
                training_table: "${var.training_table}"
                model_name: "${var.existing_books_model_name}"
                forecast_table: "${var.forecast_table}"
        result: pipeline_result
    - maybe_start_vertex_batch_prediction:
        switch:
          - condition: $${${var.enable_vertex_batch_prediction}}
            next: start_vertex_batch_prediction
        next: return_existing_books
    - start_vertex_batch_prediction:
        call: googleapis.aiplatform.v1.projects.locations.batchPredictionJobs.create
        args:
          parent: "projects/${var.project_id}/locations/${local.region}"
          region: "${local.region}"
          body:
            displayName: "bookflow-existing-books-batch-prediction"
            model: "${coalesce(var.vertex_batch_prediction_model, "")}"
            inputConfig:
              instancesFormat: "bigquery"
              bigquerySource:
                inputUri: "bq://${var.project_id}.${local.dataset_id}.${var.batch_prediction_input_view_id}"
            outputConfig:
              predictionsFormat: "bigquery"
              bigqueryDestination:
                outputUri: "${coalesce(var.vertex_batch_prediction_output_dataset_uri, "bq://${var.project_id}.${local.dataset_id}")}"
        result: batch_prediction_result
    - return_existing_books_with_batch_prediction:
        return:
          route: "existing_books_pipeline_and_vertex_batch_prediction"
          bq_load: $${bq_load_result.body}
          pipeline: $${pipeline_result}
          batch_prediction: $${batch_prediction_result}
    - return_existing_books:
        return:
          route: "existing_books_batch_pipeline"
          bq_load: $${bq_load_result.body}
          pipeline: $${pipeline_result}
YAML

  depends_on = [
    google_project_service.required["workflows.googleapis.com"],
    google_project_service.required["workflowexecutions.googleapis.com"],
    google_cloudfunctions2_function.content,
    google_cloud_run_service_iam_member.workflow_function_invoker,
    google_project_iam_member.workflow_aiplatform_user,
    google_storage_bucket_object.vertex_pipeline_template,
    google_bigquery_table.batch_prediction_input_view,
  ]
}
