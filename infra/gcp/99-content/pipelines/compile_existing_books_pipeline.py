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
    sales_table: str,
    inventory_table: str,
    features_table: str,
    books_table: str,
    locations_table: str,
    store_location_map_table: str,
    location: str,
    training_table: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    table_id = f"{project_id}.{dataset_id}.{training_table}"

    query = f"""
    CREATE OR REPLACE TABLE `{table_id}` AS
    WITH sales_bounds AS (
      SELECT
        MIN(SAFE_CAST(sale_date AS DATE)) AS min_date,
        MAX(SAFE_CAST(sale_date AS DATE)) AS max_date
      FROM `{project_id}.{dataset_id}.{sales_table}`
    ),
    date_spine AS (
      SELECT day AS sale_date
      FROM sales_bounds, UNNEST(GENERATE_DATE_ARRAY(min_date, max_date)) AS day
      WHERE min_date IS NOT NULL AND max_date IS NOT NULL
    ),
    active_books AS (
      SELECT
        isbn13,
        category_id,
        category_name,
        publisher,
        author,
        price_standard,
        price_sales,
        price_tier,
        sales_point,
        item_page,
        is_bestseller_flag,
        author_past_books_count,
        author_debut_year,
        author_experience_years
      FROM `{project_id}.{dataset_id}.{books_table}`
    ),
    stores AS (
      SELECT
        m.store_id,
        m.location_id,
        m.inventory_location_id,
        ls.location_type,
        ls.wh_id,
        ls.size,
        ls.is_virtual
      FROM `{project_id}.{dataset_id}.{store_location_map_table}` AS m
      JOIN `{project_id}.{dataset_id}.{locations_table}` AS ls
        ON m.location_id = ls.location_id
    ),
    sales_daily AS (
      SELECT
        SAFE_CAST(sale_date AS DATE) AS sale_date,
        isbn13,
        store_id,
        SUM(CAST(qty_sold AS FLOAT64)) AS qty_sold,
        SUM(CAST(revenue AS FLOAT64)) AS revenue,
        SAFE_DIVIDE(SUM(CAST(revenue AS FLOAT64)), NULLIF(SUM(CAST(qty_sold AS FLOAT64)), 0)) AS avg_price,
        SUM(CAST(tx_count AS FLOAT64)) AS tx_count
      FROM `{project_id}.{dataset_id}.{sales_table}`
      WHERE SAFE_CAST(sale_date AS DATE) IS NOT NULL
      GROUP BY sale_date, isbn13, store_id
    ),
    training_grid AS (
      SELECT
        d.sale_date,
        b.isbn13,
        s.store_id,
        s.location_id,
        s.inventory_location_id,
        b.category_id,
        b.category_name,
        b.publisher,
        b.author,
        b.price_standard,
        b.price_sales,
        b.price_tier,
        b.sales_point,
        b.item_page,
        b.is_bestseller_flag AS book_is_bestseller_flag,
        b.author_past_books_count,
        b.author_debut_year,
        b.author_experience_years,
        s.location_type,
        s.wh_id,
        s.size,
        s.is_virtual
      FROM date_spine AS d
      CROSS JOIN active_books AS b
      CROSS JOIN stores AS s
    )
    SELECT
      g.sale_date,
      g.isbn13,
      g.store_id,
      COALESCE(s.qty_sold, 0) AS qty_sold,
      COALESCE(s.revenue, 0) AS revenue,
      s.avg_price,
      COALESCE(s.tx_count, 0) AS tx_count,
      g.category_id,
      g.category_name,
      g.publisher,
      g.author,
      g.price_standard,
      g.price_sales,
      g.price_tier,
      g.sales_point,
      g.item_page,
      g.book_is_bestseller_flag,
      g.author_past_books_count,
      g.author_debut_year,
      g.author_experience_years,
      g.location_id,
      g.inventory_location_id,
      g.location_type,
      g.wh_id,
      g.size,
      g.is_virtual,
      feat.is_holiday,
      feat.holiday_name,
      feat.season,
      feat.day_of_week,
      feat.is_weekend,
      feat.month,
      feat.event_nearby_days,
      COALESCE(feat.sns_mentions_1d, 0) AS sns_mentions_1d,
      COALESCE(feat.sns_mentions_7d, 0) AS sns_mentions_7d,
      COALESCE(feat.on_hand_total, 0) AS on_hand_total,
      feat.days_since_last_stockout,
      feat.book_age_days,
      COALESCE(feat.is_bestseller_flag, g.book_is_bestseller_flag, FALSE) AS is_bestseller_flag,
      COALESCE(inv.on_hand, 0) AS on_hand,
      COALESCE(inv.reserved_qty, 0) AS reserved_qty,
      COALESCE(inv.safety_stock, 0) AS safety_stock
    FROM training_grid AS g
    LEFT JOIN sales_daily AS s
      ON g.sale_date = s.sale_date
     AND g.isbn13 = s.isbn13
     AND g.store_id = s.store_id
    LEFT JOIN `{project_id}.{dataset_id}.{features_table}` AS feat
      ON g.isbn13 = feat.isbn13
     AND g.sale_date = SAFE_CAST(feat.feature_date AS DATE)
    LEFT JOIN `{project_id}.{dataset_id}.{inventory_table}` AS inv
      ON g.isbn13 = inv.isbn13
     AND g.sale_date = SAFE_CAST(inv.snapshot_date AS DATE)
     AND g.inventory_location_id = inv.location_id
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
      EXTRACT(DAYOFWEEK FROM sale_date) AS day_of_week,
      EXTRACT(MONTH FROM sale_date) AS month,
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
    CREATE OR REPLACE TABLE `{forecast_table_id}` AS
    SELECT
      CURRENT_DATE() AS prediction_date,
      DATE_ADD(sale_date, INTERVAL 1 DAY) AS target_date,
      isbn13,
      store_id,
      predicted_qty_sold AS predicted_demand,
      CAST(NULL AS NUMERIC) AS confidence_low,
      CAST(NULL AS NUMERIC) AS confidence_high,
      '{model_name}' AS model_version,
      CAST(NULL AS INT64) AS inference_ms
    FROM ML.PREDICT(
      MODEL `{model_id}`,
      (
        SELECT
          isbn13,
          store_id,
          sale_date,
          EXTRACT(DAYOFWEEK FROM sale_date) AS day_of_week,
          EXTRACT(MONTH FROM sale_date) AS month,
          COALESCE(on_hand, 0) AS on_hand,
          CAST(COALESCE(is_holiday, FALSE) AS INT64) AS holiday_flag,
          COALESCE(event_nearby_days, 0) AS event_nearby_days,
          COALESCE(sns_mentions_1d, 0) AS sns_mentions_1d,
          COALESCE(sns_mentions_7d, 0) AS sns_mentions_7d
        FROM `{source_table_id}`
      )
    )
    """

    client.query(query).result()
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
        sales_table: str,
        inventory_table: str,
        features_table: str,
        books_table: str,
        locations_table: str,
        store_location_map_table: str,
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
            sales_table=sales_table,
            inventory_table=inventory_table,
            features_table=features_table,
            books_table=books_table,
            locations_table=locations_table,
            store_location_map_table=store_location_map_table,
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
