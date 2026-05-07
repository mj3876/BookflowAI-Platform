import logging
import os
from pathlib import PurePosixPath

import functions_framework
from google.cloud import bigquery


def _configured_load_tables():
    raw_tables = os.getenv("BOOKFLOW_LOAD_TABLES", "")
    return [table.strip() for table in raw_tables.split(",") if table.strip()]


def _table_aliases():
    raw_aliases = os.getenv("BOOKFLOW_LOAD_TABLE_ALIASES", "")
    aliases = {}
    for alias_pair in raw_aliases.split(","):
        if ":" not in alias_pair:
            continue
        source_name, table_name = alias_pair.split(":", 1)
        source_name = source_name.strip().lower()
        table_name = table_name.strip()
        if source_name and table_name:
            aliases[source_name] = table_name
    return aliases


def _resolve_table_id(payload, object_name):
    explicit_table = payload.get("table_id") or payload.get("table")
    if explicit_table:
        return explicit_table

    configured_tables = _configured_load_tables()
    object_stem = PurePosixPath(object_name).stem.lower()
    aliases = _table_aliases()
    if object_stem in aliases and aliases[object_stem] in configured_tables:
        return aliases[object_stem]

    for table in configured_tables:
        if table.lower() in object_stem:
            return table

    if len(configured_tables) == 1:
        return configured_tables[0]

    return None


def _json_error(message, status_code):
    return ({"error": message}, status_code)


@functions_framework.http
def handler(request):
    try:
        payload = request.get_json(silent=True) or {}
        bucket = payload.get("bucket")
        object_name = payload.get("object") or payload.get("name")
        project_id = payload.get("project_id") or os.getenv("BOOKFLOW_PROJECT_ID")
        dataset_id = payload.get("dataset_id") or os.getenv("BOOKFLOW_DATASET_ID")
        table_id = _resolve_table_id(payload, object_name or "")
        location = payload.get("bq_location") or os.getenv("BOOKFLOW_BQ_LOCATION")

        missing = [
            name
            for name, value in {
                "bucket": bucket,
                "object": object_name,
                "project_id": project_id,
                "dataset_id": dataset_id,
                "table_id": table_id,
            }.items()
            if not value
        ]
        if missing:
            return _json_error(f"Missing required fields: {', '.join(missing)}", 400)

        source_uri = f"gs://{bucket}/{object_name}"
        destination = f"{project_id}.{dataset_id}.{table_id}"
        client = bigquery.Client(project=project_id, location=location)

        job_config = bigquery.LoadJobConfig()
        if object_name.lower().endswith(".parquet"):
            job_config.source_format = bigquery.SourceFormat.PARQUET
        else:
            job_config.source_format = bigquery.SourceFormat.CSV
            job_config.autodetect = True
            job_config.skip_leading_rows = int(payload.get("skip_leading_rows", 1))
        job_config.write_disposition = (
            payload.get("write_disposition")
            or os.getenv("BOOKFLOW_WRITE_DISPOSITION")
            or bigquery.WriteDisposition.WRITE_APPEND
        )

        job = client.load_table_from_uri(
            source_uri,
            destination,
            job_config=job_config,
            location=location,
        )
        job.result()

        table = client.get_table(destination)
        return {
            "job_id": job.job_id,
            "source_uri": source_uri,
            "destination": destination,
            "output_rows": table.num_rows,
        }
    except Exception as exc:
        logging.exception("BigQuery load failed")
        return _json_error(str(exc), 500)
