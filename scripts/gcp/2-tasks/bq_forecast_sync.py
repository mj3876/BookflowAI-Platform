"""
bq_forecast_sync.py — BigQuery forecast_results → RDS forecast_cache 동기화

[역할]
  Vertex AI 파이프라인이 BigQuery forecast_results 에 쓴 예측값을
  RDS forecast_cache 로 복사해 dashboard-svc / forecast-svc 가 읽을 수 있게 함.

[컬럼 매핑]
  BQ forecast_results.target_date     → RDS forecast_cache.snapshot_date
  BQ forecast_results.isbn13          → RDS forecast_cache.isbn13
  BQ forecast_results.store_id        → RDS forecast_cache.store_id
  BQ forecast_results.predicted_demand→ RDS forecast_cache.predicted_demand
  BQ forecast_results.confidence_low  → RDS forecast_cache.confidence_low  (NULL 허용)
  BQ forecast_results.confidence_high → RDS forecast_cache.confidence_high (NULL 허용)
  BQ forecast_results.model_version   → RDS forecast_cache.model_version

[사용법]
  python bq_forecast_sync.py
  python bq_forecast_sync.py --days 14
  python bq_forecast_sync.py --dry-run

[필수 환경변수]
  BQ_PROJECT_ID    GCP 프로젝트 ID
  RDS_HOST         PostgreSQL 호스트
  RDS_USER         PostgreSQL 사용자
  RDS_PASSWORD     PostgreSQL 비밀번호

[선택 환경변수]
  BQ_DATASET_ID       BigQuery 데이터셋 (기본: bookflow_dw)
  BQ_LOCATION         BigQuery 위치    (기본: asia-northeast1)
  BQ_FORECAST_TABLE   forecast 테이블  (기본: forecast_results)
  RDS_PORT            PostgreSQL 포트  (기본: 5432)
  RDS_DB              데이터베이스명   (기본: bookflow)
"""
import argparse
import logging
import os
from datetime import datetime, timezone

import psycopg
from google.cloud import bigquery

log = logging.getLogger(__name__)


def _bq_client() -> bigquery.Client:
    return bigquery.Client(
        project=os.environ["BQ_PROJECT_ID"],
        location=os.environ.get("BQ_LOCATION", "asia-northeast1"),
    )


def _pg_connstr() -> str:
    parts = [
        f"host={os.environ['RDS_HOST']}",
        f"port={os.environ.get('RDS_PORT', '5432')}",
        f"dbname={os.environ.get('RDS_DB', 'bookflow')}",
        f"user={os.environ['RDS_USER']}",
        f"password={os.environ['RDS_PASSWORD']}",
    ]
    sslmode = os.environ.get("RDS_SSLMODE")
    sslrootcert = os.environ.get("RDS_SSLROOTCERT")
    if sslmode:
        parts.append(f"sslmode={sslmode}")
    if sslrootcert:
        parts.append(f"sslrootcert={sslrootcert}")
    return " ".join(parts)


def fetch_rows(client: bigquery.Client, days: int) -> list[dict]:
    project = os.environ["BQ_PROJECT_ID"]
    dataset = os.environ.get("BQ_DATASET_ID", "bookflow_dw")
    table   = os.environ.get("BQ_FORECAST_TABLE", "forecast_results")

    # 가장 최근 prediction_date 배치에서 target_date 최근 N일치만 동기화
    # (35M 행 전체 대신 대시보드에 필요한 최신 예측 슬라이스만 가져옴)
    query = f"""
    WITH latest AS (
      SELECT MAX(prediction_date) AS pred_date,
             MAX(target_date)     AS max_tgt
      FROM `{project}.{dataset}.{table}`
    )
    SELECT
      f.target_date       AS snapshot_date,
      f.isbn13,
      f.store_id,
      f.predicted_demand,
      f.confidence_low,
      f.confidence_high,
      f.model_version
    FROM `{project}.{dataset}.{table}` f
    JOIN latest ON f.prediction_date = latest.pred_date
    WHERE f.target_date >= DATE_SUB(latest.max_tgt, INTERVAL {days} DAY)
    ORDER BY f.target_date, f.store_id, f.isbn13
    """
    rows = list(client.query(query).result())
    log.info("BigQuery: %d rows fetched (latest prediction_date, target_date last %d days)", len(rows), days)
    return [dict(r) for r in rows]


_UPSERT = """
INSERT INTO forecast_cache
  (snapshot_date, isbn13, store_id, predicted_demand,
   confidence_low, confidence_high, model_version, synced_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (snapshot_date, isbn13, store_id) DO UPDATE
SET predicted_demand = EXCLUDED.predicted_demand,
    confidence_low   = EXCLUDED.confidence_low,
    confidence_high  = EXCLUDED.confidence_high,
    model_version    = EXCLUDED.model_version,
    synced_at        = EXCLUDED.synced_at
"""


def upsert(rows: list[dict], dry_run: bool) -> int:
    if not rows:
        log.warning("rows empty — nothing to sync")
        return 0

    now = datetime.now(timezone.utc)
    params = [
        (
            row["snapshot_date"],
            row["isbn13"],
            int(row["store_id"]),
            float(row["predicted_demand"]),
            float(row["confidence_low"])  if row["confidence_low"]  is not None else None,
            float(row["confidence_high"]) if row["confidence_high"] is not None else None,
            row["model_version"],
            now,
        )
        for row in rows
    ]

    if dry_run:
        log.info("dry-run: would upsert %d rows (skipping DB write)", len(params))
        return len(params)

    with psycopg.connect(_pg_connstr()) as conn:
        with conn.cursor() as cur:
            cur.executemany(_UPSERT, params)
        conn.commit()

    log.info("RDS forecast_cache: %d rows upserted", len(params))
    return len(params)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="BigQuery forecast_results → RDS forecast_cache sync")
    parser.add_argument(
        "--days", type=int, default=7,
        help="오늘 기준 N일 전부터의 target_date 행을 동기화 (기본: 7)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="BigQuery 조회만 하고 RDS 쓰기는 생략"
    )
    args = parser.parse_args()

    client = _bq_client()
    rows   = fetch_rows(client, args.days)
    count  = upsert(rows, dry_run=args.dry_run)
    log.info("sync complete — %d rows", count)


if __name__ == "__main__":
    main()
