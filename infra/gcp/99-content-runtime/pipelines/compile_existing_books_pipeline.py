"""Compile the BOOKFLOW existing-books Vertex AI Pipeline as a KFP v2 spec.

All deployment-specific values are provided by CLI flags or environment
variables. The script intentionally avoids embedding project IDs, bucket names,
or dataset names in the pipeline definition.
"""

import argparse
import os
from pathlib import Path

from kfp import compiler, dsl


ENV_OUTPUT_JSON = "BOOKFLOW_PIPELINE_JSON"


@dsl.component(base_image="python:3.12-slim")
def validate_runtime_config(
    project_id: str,
    dataset_id: str,
    staging_bucket: str,
    models_bucket: str,
    source_object: str,
) -> str:
    values = {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "staging_bucket": staging_bucket,
        "models_bucket": models_bucket,
        "source_object": source_object,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"Missing required pipeline values: {', '.join(missing)}")
    return models_bucket


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-bigquery"],
)
def build_training_dataset(
    project_id: str,
    dataset_id: str,
    features_table: str,
    location: str,
    training_table: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    table_id = f"{project_id}.{dataset_id}.{training_table}"

    query = f"""
    CREATE OR REPLACE TABLE `{table_id}` AS
    SELECT *
    FROM `{project_id}.{dataset_id}.{features_table}`
    WHERE book_age_days >= 0
      AND qty_sold IS NOT NULL
    """

    client.query(query).result()
    return table_id


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-bigquery"],
)
def train_demand_model(
    project_id: str,
    dataset_id: str,
    location: str,
    training_table: str,
    model_name: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    model_id = f"{project_id}.{dataset_id}.{model_name}"
    source_table_id = f"{project_id}.{dataset_id}.{training_table}"

    query = f"""
    CREATE OR REPLACE MODEL `{model_id}`
    OPTIONS(
      MODEL_TYPE = 'BOOSTED_TREE_REGRESSOR',
      INPUT_LABEL_COLS = ['qty_sold'],
      MAX_ITERATIONS = 25
    ) AS
    SELECT
      qty_sold,
      day_of_week,
      month,
      COALESCE(on_hand, 0) AS on_hand,
      CAST(COALESCE(is_holiday, FALSE) AS INT64) AS holiday_flag,
      COALESCE(event_nearby_days, 0) AS event_nearby_days,
      COALESCE(sns_mentions_1d, 0) AS sns_mentions_1d,
      COALESCE(sns_mentions_7d, 0) AS sns_mentions_7d
    FROM `{source_table_id}`
    WHERE qty_sold IS NOT NULL
    """

    client.query(query).result()
    return model_id


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-bigquery"],
)
def evaluate_demand_model(
    project_id: str,
    dataset_id: str,
    location: str,
    model_name: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    model_id = f"{project_id}.{dataset_id}.{model_name}"
    eval_table_id = f"{project_id}.{dataset_id}.{model_name}_evaluation"

    query = f"""
    CREATE OR REPLACE TABLE `{eval_table_id}` AS
    SELECT *
    FROM ML.EVALUATE(MODEL `{model_id}`)
    """

    client.query(query).result()
    return eval_table_id


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["google-cloud-bigquery"],
)
def write_batch_forecast(
    project_id: str,
    dataset_id: str,
    location: str,
    training_table: str,
    model_name: str,
    forecast_table: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    model_id = f"{project_id}.{dataset_id}.{model_name}"
    source_table_id = f"{project_id}.{dataset_id}.{training_table}"
    forecast_table_id = f"{project_id}.{dataset_id}.{forecast_table}"

    query = f"""
    DELETE FROM `{forecast_table_id}`
    WHERE prediction_date = CURRENT_DATE();

    INSERT INTO `{forecast_table_id}` (
      prediction_date,
      target_date,
      isbn13,
      store_id,
      predicted_demand,
      confidence_low,
      confidence_high,
      model_version,
      inference_ms
    )
    SELECT
      CURRENT_DATE() AS prediction_date,
      DATE_ADD(feature_date, INTERVAL 1 DAY) AS target_date,
      isbn13,
      store_id,
      CAST(predicted_qty_sold AS NUMERIC) AS predicted_demand,
      CAST(NULL AS NUMERIC) AS confidence_low,
      CAST(NULL AS NUMERIC) AS confidence_high,
      @model_name AS model_version,
      CAST(NULL AS INT64) AS inference_ms
    FROM ML.PREDICT(
      MODEL `{model_id}`,
      (
        SELECT
          isbn13,
          store_id,
          feature_date,
          day_of_week,
          month,
          COALESCE(on_hand, 0) AS on_hand,
          CAST(COALESCE(is_holiday, FALSE) AS INT64) AS holiday_flag,
          COALESCE(event_nearby_days, 0) AS event_nearby_days,
          COALESCE(sns_mentions_1d, 0) AS sns_mentions_1d,
          COALESCE(sns_mentions_7d, 0) AS sns_mentions_7d
        FROM `{source_table_id}`
      )
    )
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("model_name", "STRING", model_name),
        ]
    )
    client.query(query, job_config=job_config).result()
    return forecast_table_id


def create_pipeline():
    @dsl.pipeline(
        name="bookflow-existing-books-forecast",
        description="Builds the existing-books training dataset, trains a demand model, evaluates it, and writes batch forecasts.",
    )
    def bookflow_existing_books_forecast(
        project_id: str,
        dataset_id: str,
        staging_bucket: str,
        models_bucket: str,
        source_object: str,
        bq_location: str,
        features_table: str,
        training_table: str,
        model_name: str,
        forecast_table: str,
    ):
        runtime_config = validate_runtime_config(
            project_id=project_id,
            dataset_id=dataset_id,
            staging_bucket=staging_bucket,
            models_bucket=models_bucket,
            source_object=source_object,
        )

        training_dataset = build_training_dataset(
            project_id=project_id,
            dataset_id=dataset_id,
            features_table=features_table,
            location=bq_location,
            training_table=training_table,
        ).after(runtime_config)

        model = train_demand_model(
            project_id=project_id,
            dataset_id=dataset_id,
            location=bq_location,
            training_table=training_table,
            model_name=model_name,
        ).after(training_dataset)

        evaluation = evaluate_demand_model(
            project_id=project_id,
            dataset_id=dataset_id,
            location=bq_location,
            model_name=model_name,
        ).after(model)

        write_batch_forecast(
            project_id=project_id,
            dataset_id=dataset_id,
            location=bq_location,
            training_table=training_table,
            model_name=model_name,
            forecast_table=forecast_table,
        ).after(evaluation)

    return bookflow_existing_books_forecast


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile the BOOKFLOW existing-books KFP v2 pipeline JSON."
    )
    parser.add_argument(
        "--output-json",
        default=os.getenv(
            ENV_OUTPUT_JSON,
            str(Path(__file__).resolve().with_name("bookflow-existing-books-pipeline.json")),
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    output_json = Path(args.output_json).resolve()
    output_json.parent.mkdir(parents=True, exist_ok=True)

    pipeline_func = create_pipeline()
    compiler.Compiler().compile(
        pipeline_func=pipeline_func,
        package_path=str(output_json),
    )

    print(f"Compiled KFP v2 pipeline spec: {output_json}")


if __name__ == "__main__":
    main()
