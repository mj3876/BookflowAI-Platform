import logging
import os

import functions_framework
from google.cloud import bigquery


_DEFAULT_LEAD_DAYS = int(os.getenv("BOOKFLOW_FORECAST_LEAD_DAYS", "30"))


def _json_error(message, status_code):
    return ({"error": message}, status_code)


@functions_framework.http
def handler(request):
    try:
        payload = request.get_json(silent=True) or {}
        isbn13 = payload.get("isbn13")
        if not isbn13:
            return _json_error("isbn13 is required", 400)

        project_id   = os.environ["BOOKFLOW_PROJECT_ID"]
        dataset_id   = os.environ.get("BOOKFLOW_DATASET_ID", "bookflow_dw")
        location     = os.environ.get("BOOKFLOW_BQ_LOCATION", "asia-northeast1")
        model_name   = os.environ.get("BOOKFLOW_NEW_BOOK_MODEL_NAME", "bookflow_new_books_forecast")
        forecast_tbl = os.environ.get("BOOKFLOW_NEW_BOOK_FORECAST_TABLE", "new_book_forecast")
        lead_days    = int(payload.get("lead_days", _DEFAULT_LEAD_DAYS))

        client = bigquery.Client(project=project_id, location=location)

        def p(t):
            return f"`{project_id}.{dataset_id}.{t}`"

        # 1. Clear existing predictions for this isbn13
        client.query(
            f"DELETE FROM {p(forecast_tbl)} WHERE isbn13 = @isbn13",
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("isbn13", "STRING", isbn13),
            ]),
        ).result()

        # 2. ML.PREDICT × all stores → INSERT into new_book_forecast
        client.query(
            f"""
            INSERT INTO {p(forecast_tbl)}
              (isbn13, store_id, wh_id, predicted_daily_demand, predicted_30d_qty,
               prediction_date, model_version)
            SELECT
              @isbn13                                                       AS isbn13,
              slm.store_id,
              ls.wh_id,
              GREATEST(pred.predicted_label, 0.0)                          AS predicted_daily_demand,
              CAST(ROUND(GREATEST(pred.predicted_label, 0.0) * @lead_days)
                   AS INT64)                                               AS predicted_30d_qty,
              CURRENT_DATE()                                                AS prediction_date,
              @model_name                                                   AS model_version
            FROM ML.PREDICT(
              MODEL {p(model_name)},
              (
                SELECT
                  b.category_id,
                  b.price_tier,
                  COALESCE(b.sales_point, 0)                              AS sales_point,
                  CAST(COALESCE(b.is_bestseller_flag, FALSE) AS INT64)   AS is_bestseller_flag,
                  COALESCE(b.author_experience_years, 0)                  AS author_experience_years,
                  COALESCE(b.author_past_books_count, 0)                  AS author_past_books_count,
                  COALESCE(b.item_page, 0)                                AS item_page,
                  slm.store_id,
                  COALESCE(ls.wh_id, 1)                                   AS region_code,
                  COALESCE(CASE ls.size WHEN 'L' THEN 3 WHEN 'M' THEN 2 WHEN 'S' THEN 1 ELSE 2 END, 2) AS size_numeric
                FROM {p('books_static')} b
                CROSS JOIN {p('store_location_map')} slm
                LEFT JOIN {p('locations_static')} ls ON ls.location_id = slm.location_id
                WHERE b.isbn13 = @isbn13
              )
            ) AS pred
            JOIN {p('store_location_map')} slm ON slm.store_id = pred.store_id
            JOIN {p('locations_static')} ls    ON ls.location_id = slm.inventory_location_id
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("isbn13",     "STRING", isbn13),
                bigquery.ScalarQueryParameter("lead_days",  "INT64",  lead_days),
                bigquery.ScalarQueryParameter("model_name", "STRING", model_name),
            ]),
        ).result()

        # 3. Aggregate by wh_id to return recommended quantities
        rows = list(client.query(
            f"""
            SELECT wh_id, SUM(predicted_30d_qty) AS wh_qty
            FROM {p(forecast_tbl)}
            WHERE isbn13 = @isbn13 AND prediction_date = CURRENT_DATE()
            GROUP BY wh_id
            ORDER BY wh_id
            """,
            job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("isbn13", "STRING", isbn13),
            ]),
        ).result())

        wh_qtys = {int(r.wh_id): int(r.wh_qty) for r in rows if r.wh_id is not None}
        return {
            "isbn13":        isbn13,
            "wh1_qty":       wh_qtys.get(1, 0),
            "wh2_qty":       wh_qtys.get(2, 0),
            "lead_days":     lead_days,
            "source":        "new_book_model",
            "model_version": model_name,
        }

    except Exception as exc:
        logging.exception("New-book inference failed")
        return _json_error(str(exc), 500)
