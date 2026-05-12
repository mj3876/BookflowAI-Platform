from google.cloud import aiplatform

PROJECT = "project-8ab6bf05-54d2-4f5d-b8d"
REGION  = "asia-northeast1"

aiplatform.init(project=PROJECT, location=REGION)

job = aiplatform.PipelineJob(
    display_name="bookflow-new-books-forecast",
    template_path=f"gs://{PROJECT}-bookflow-models/pipelines/bookflow-new-books-pipeline.json",
    pipeline_root=f"gs://{PROJECT}-bookflow-models/pipeline-root",
    parameter_values={
        "project_id":            PROJECT,
        "dataset_id":            "bookflow_dw",
        "bq_location":           REGION,
        "sales_table":           "sales_fact",
        "books_table":           "books_static",
        "new_book_training_table": "new_book_training_dataset",
        "model_name":            "bookflow_new_books_forecast",
        "staging_bucket":        f"{PROJECT}-bookflow-staging",
        "models_bucket":         f"{PROJECT}-bookflow-models",
    },
)
job.submit(
    service_account=f"bookflow-vertex-pipeline@{PROJECT}.iam.gserviceaccount.com"
)
print("Pipeline job submitted:", job.resource_name)
print("State:", job.state)
print(f"Console: https://console.cloud.google.com/vertex-ai/locations/{REGION}/pipelines/runs/{job.resource_name.split('/')[-1]}?project=476598540719")
