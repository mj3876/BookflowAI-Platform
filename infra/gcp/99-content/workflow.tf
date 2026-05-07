resource "google_workflows_workflow" "gcs_router" {
  name            = "bookflow-gcs-router"
  project         = var.project_id
  region          = local.region
  description     = "Routes GCS finalized objects through BQ load and Vertex AI workflows."
  service_account = google_service_account.workflow.email
  labels          = var.labels

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
        next: skip_pipeline_for_test
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
            endpoint: "${google_vertex_ai_endpoint.forecast.name}"
            features: $${feature_result.body}
        result: vertex_result
    - return_new_book:
        return:
          route: "new_book_realtime_inference"
          bq_load: $${bq_load_result.body}
          vertex: $${vertex_result.body}
    - skip_pipeline_for_test:
        return: "Dry run: Data loaded to BigQuery successfully. Vertex AI pipeline was skipped for cost saving."
    # - start_existing_book_pipeline:
    #     call: googleapis.aiplatform.v1.projects.locations.pipelineJobs.create
    #     args:
    #       parent: "projects/${var.project_id}/locations/${local.region}"
    #       region: "${local.region}"
    #       body:
    #         displayName: "bookflow-existing-books-forecast"
    #         serviceAccount: "${google_service_account.vertex_pipeline.email}"
    #         templateUri: "${local.vertex_pipeline_template_uri}"
    #         runtimeConfig:
    #           gcsOutputDirectory: "${local.vertex_pipeline_root}"
    #           parameterValues:
    #             project_id: "${var.project_id}"
    #             dataset_id: "${local.dataset_id}"
    #             bq_location: "${var.bigquery_location}"
    #             staging_bucket: "${data.google_storage_bucket.staging.name}"
    #             models_bucket: "${data.google_storage_bucket.models.name}"
    #             source_object: $${object_name}
    #             sales_table: "${var.sales_table}"
    #             inventory_table: "${var.inventory_daily_table}"
    #             features_table: "${var.features_table}"
    #             books_table: "${var.books_static_table}"
    #             locations_table: "${var.locations_static_table}"
    #             training_table: "${var.training_table}"
    #             model_name: "${var.existing_books_model_name}"
    #             forecast_table: "${var.forecast_table}"
    #     result: pipeline_result
    # - return_existing_books:
    #     return:
    #       route: "existing_books_batch_pipeline"
    #       bq_load: $${bq_load_result.body}
    #       pipeline: $${pipeline_result}
YAML

  depends_on = [
    google_project_service.required["workflows.googleapis.com"],
    google_cloudfunctions2_function.content,
    google_cloud_run_service_iam_member.workflow_function_invoker,
    google_project_iam_member.workflow_aiplatform_user,
  ]
}
