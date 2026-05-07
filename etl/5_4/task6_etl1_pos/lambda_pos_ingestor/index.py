"""
[5/4] Task6 ETL1 · pos-ingestor Lambda
Kinesis ESM → RDS sales_realtime + inventory UPDATE + Redis 

ECS sim   (BookFlowAI-Apps):
tx_id, isbn13, qty, unit_price, total_price, channel, location_id, ts
"""
import base64
import json
import os
import traceback
from datetime import datetime, timezone

import boto3
import psycopg2
import redis

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")


def _get_secret(sm, name: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _db_connect(secret: dict):
    return psycopg2.connect(
        host=secret["host"], port=int(secret.get("port", 5432)),
        dbname=secret.get("dbname", "bookflow"),
        user=secret["username"], password=secret["password"], connect_timeout=10,
    )


def _redis_client(secret: dict):
    return redis.Redis(
        host=secret["host"], port=int(secret.get("port", 6379)),
        decode_responses=True, socket_timeout=3,
    )


def _process(cur, rc, rec: dict) -> None:
    isbn13      = rec["isbn13"]
    location_id = int(rec["location_id"])
    qty         = int(rec["qty"])
    sale_price  = float(rec.get("total_price", rec.get("sale_price", 0)))
    channel     = rec.get("channel", "OFFLINE")
    tx_id       = rec["tx_id"]
    created_at  = rec.get("ts", rec.get("created_at", datetime.now(timezone.utc).isoformat()))

    cur.execute(
        """
        INSERT INTO sales_realtime
            (tx_id, isbn13, location_id, qty, sale_price, channel, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (tx_id) DO NOTHING
        """,
        (tx_id, isbn13, location_id, qty, sale_price, channel, created_at),
    )
    cur.execute(
        """
        UPDATE inventory
        SET available = GREATEST(available - %s, 0), updated_at = NOW()
        WHERE isbn13 = %s AND location_id = %s
        """,
        (qty, isbn13, location_id),
    )
    try:
        rc.delete(f"stock:{isbn13}:{location_id}")
    except Exception as e:
        print(f"[pos-ingestor] Redis  {isbn13}:{location_id}: {e}")


def lambda_handler(event, context):
    sm = boto3.client("secretsmanager", region_name=REGION)
    conn     = _db_connect(_get_secret(sm, "bookflow/rds-master"))
    rc       = _redis_client(_get_secret(sm, "bookflow/redis"))
    records  = event.get("Records", [])
    failures = []
    try:
        for r in records:
            seq = r["kinesis"]["sequenceNumber"]
            try:
                rec = json.loads(base64.b64decode(r["kinesis"]["data"]).decode("utf-8"))
                with conn:
                    with conn.cursor() as cur:
                        _process(cur, rc, rec)
            except Exception:
                print(f"[pos-ingestor] seq={seq}\n{traceback.format_exc()}")
                failures.append({"itemIdentifier": seq})
    finally:
        conn.close()
    print(f"[pos-ingestor] {len(records)} · {len(failures)} ")
    return {"batchItemFailures": failures}
