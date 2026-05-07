import logging
import os

import functions_framework
from google.cloud import bigquery


DEFAULT_LIMIT = int(os.getenv("BOOKFLOW_FEATURE_LIMIT", "100"))


def _json_error(message, status_code):
    return ({"error": message}, status_code)


def _configured_feature_columns():
    raw_columns = os.getenv("BOOKFLOW_FEATURE_COLUMNS", "")
    return [column.strip() for column in raw_columns.split(",") if column.strip()]


@functions_framework.http
def handler(request):
    try:
        payload = request.get_json(silent=True) or {}
        project_id = payload.get("project_id") or os.getenv("BOOKFLOW_PROJECT_ID")
        dataset_id = payload.get("dataset_id") or os.getenv("BOOKFLOW_DATASET_ID")
        location = payload.get("bq_location") or os.getenv("BOOKFLOW_BQ_LOCATION")
        table_id = payload.get("table_id") or payload.get("table") or os.getenv("BOOKFLOW_FEATURE_TABLE")
        limit = int(payload.get("limit", DEFAULT_LIMIT))

        missing = [
            name
            for name, value in {
                "project_id": project_id,
                "dataset_id": dataset_id,
                "table_id": table_id,
            }.items()
            if not value
        ]
        if missing:
            return _json_error(f"Missing required fields: {', '.join(missing)}", 400)

        feature_columns = payload.get("feature_columns")
        if not feature_columns:
            feature_columns = _configured_feature_columns()

        selected_columns = [f"`{column}`" for column in feature_columns] if feature_columns else ["*"]
        query = f"""
        SELECT {", ".join(selected_columns)}
        FROM `{project_id}.{dataset_id}.{table_id}`
        LIMIT @limit
        """

        client = bigquery.Client(project=project_id, location=location)
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        )
        rows = client.query(query, job_config=job_config, location=location).result()

        instances = []
        for row in rows:
            instance = {}
            row_columns = feature_columns or list(row.keys())
            for column in row_columns:
                value = row.get(column)
                instance[column] = value.isoformat() if hasattr(value, "isoformat") else value
            instances.append(instance)

        return {
            "instances": instances,
            "metadata": {
                "project_id": project_id,
                "dataset_id": dataset_id,
                "table_id": table_id,
                "feature_columns": feature_columns,
                "row_count": len(instances),
            },
        }
    except Exception as exc:
        logging.exception("Feature assembly failed")
        return _json_error(str(exc), 500)
