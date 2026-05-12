"""신간 파이프라인 JSON에 region_code, size_numeric 피처를 추가하는 패치 스크립트"""
import json
from pathlib import Path

PIPELINE_PATH = Path(r"D:\gcp\BookFlowAI-Platform\infra\gcp\99-content-runtime\pipelines\bookflow-new-books-pipeline.json")

NEW_BUILD_CODE = '''
import kfp
from kfp import dsl
from kfp.dsl import *
from typing import *

def build_new_book_training_dataset(
    project_id: str,
    dataset_id: str,
    sales_table: str,
    books_table: str,
    location: str,
    training_table: str,
) -> str:
    from google.cloud import bigquery

    client = bigquery.Client(project=project_id, location=location)
    table_id = f"{project_id}.{dataset_id}.{training_table}"

    query = f"""
    CREATE OR REPLACE TABLE `{table_id}` AS
    WITH first_sale AS (
      SELECT isbn13, MIN(SAFE_CAST(sale_date AS DATE)) AS first_sale_date
      FROM `{project_id}.{dataset_id}.{sales_table}`
      GROUP BY isbn13
    ),
    early_demand AS (
      SELECT
        s.isbn13,
        s.store_id,
        AVG(COALESCE(CAST(s.qty_sold AS FLOAT64), 0)) AS avg_daily_demand
      FROM `{project_id}.{dataset_id}.{sales_table}` s
      JOIN first_sale fs ON s.isbn13 = fs.isbn13
      WHERE SAFE_CAST(s.sale_date AS DATE)
            BETWEEN fs.first_sale_date
            AND DATE_ADD(fs.first_sale_date, INTERVAL 30 DAY)
      GROUP BY s.isbn13, s.store_id
    )
    SELECT
      b.category_id,
      b.price_tier,
      COALESCE(b.sales_point, 0)                           AS sales_point,
      CAST(COALESCE(b.is_bestseller_flag, FALSE) AS INT64) AS is_bestseller_flag,
      COALESCE(b.author_experience_years, 0)               AS author_experience_years,
      COALESCE(b.author_past_books_count, 0)               AS author_past_books_count,
      COALESCE(b.item_page, 0)                             AS item_page,
      ed.store_id,
      COALESCE(ls.wh_id, 1)                                AS region_code,
      COALESCE(CASE ls.size WHEN \'L\' THEN 3 WHEN \'M\' THEN 2 WHEN \'S\' THEN 1 ELSE 2 END, 2) AS size_numeric,
      ed.avg_daily_demand                                  AS label
    FROM early_demand ed
    JOIN `{project_id}.{dataset_id}.{books_table}` b ON b.isbn13 = ed.isbn13
    LEFT JOIN `{project_id}.{dataset_id}.store_location_map` slm ON slm.store_id = ed.store_id
    LEFT JOIN `{project_id}.{dataset_id}.locations_static` ls ON ls.location_id = slm.location_id
    WHERE ed.avg_daily_demand IS NOT NULL
    """

    client.query(query).result()
    return table_id

'''

NEW_TRAIN_CODE = '''
import kfp
from kfp import dsl
from kfp.dsl import *
from typing import *

def train_new_book_model(
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
      MODEL_TYPE = \'BOOSTED_TREE_REGRESSOR\',
      INPUT_LABEL_COLS = [\'label\'],
      MAX_ITERATIONS = 25
    ) AS
    SELECT
      label,
      category_id,
      price_tier,
      sales_point,
      is_bestseller_flag,
      author_experience_years,
      author_past_books_count,
      item_page,
      store_id,
      region_code,
      size_numeric
    FROM `{source_table_id}`
    WHERE label IS NOT NULL
    """

    client.query(query).result()
    return model_id

'''

pipeline = json.loads(PIPELINE_PATH.read_text(encoding="utf-8"))

executors = pipeline["deploymentSpec"]["executors"]

# build 컴포넌트 패치
build_cmd = executors["exec-build-new-book-training-dataset"]["container"]["command"]
build_cmd[-1] = NEW_BUILD_CODE

# train 컴포넌트 패치
train_cmd = executors["exec-train-new-book-model"]["container"]["command"]
train_cmd[-1] = NEW_TRAIN_CODE

PIPELINE_PATH.write_text(json.dumps(pipeline, ensure_ascii=False, indent=2), encoding="utf-8")
print("pipeline JSON patch done")
print(f"   {PIPELINE_PATH}")
