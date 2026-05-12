from google.cloud import aiplatform

aiplatform.init(project="project-8ab6bf05-54d2-4f5d-b8d", location="asia-northeast1")
job = aiplatform.PipelineJob(
    display_name="bookflow-existing-books-forecast",
    template_path="gs://project-8ab6bf05-54d2-4f5d-b8d-bookflow-models/pipelines/bookflow-existing-books-pipeline.json",
    pipeline_root="gs://project-8ab6bf05-54d2-4f5d-b8d-bookflow-models/pipeline-root",
    parameter_values={
        "project_id": "project-8ab6bf05-54d2-4f5d-b8d",
        "dataset_id": "bookflow_dw",
        "bq_location": "asia-northeast1",
        "sales_table": "sales_fact",
        "inventory_table": "inventory_daily",
        "features_table": "features",
        "books_table": "books_static",
        "locations_table": "locations_static",
        "store_location_map_table": "store_location_map",
        "training_table": "training_dataset",
        "model_name": "bookflow_existing_books_forecast",
        "forecast_table": "forecast_results",
        "staging_bucket": "project-8ab6bf05-54d2-4f5d-b8d-bookflow-staging",
        "models_bucket": "project-8ab6bf05-54d2-4f5d-b8d-bookflow-models",
        "source_object": "mart/features/e2e-001/part-0.parquet",
    },
)
job.submit(
    service_account="bookflow-vertex-pipeline@project-8ab6bf05-54d2-4f5d-b8d.iam.gserviceaccount.com"
)
print("Pipeline job submitted:", job.resource_name)
print("State:", job.state)
print("Console:", f"https://console.cloud.google.com/vertex-ai/locations/asia-northeast1/pipelines/runs/{job.resource_name.split('/')[-1]}?project=476598540719")
