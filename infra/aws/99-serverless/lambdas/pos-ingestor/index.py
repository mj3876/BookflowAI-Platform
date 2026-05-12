"""
pos-ingestor Lambda
Kinesis ESM (bookflow-pos-events) → sales_realtime INSERT + inventory UPDATE + Redis 
VPC   · ReservedConcurrentExecutions=5 · batchItemFailures 

ECS sim  : tx_id, isbn13, qty, unit_price, total_price, channel, location_id, ts
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
        host=secret["host"],
        port=int(secret.get("port", 5432)),
        dbname=secret.get("dbname", "bookflow"),
        user=secret["username"],
        password=secret["password"],
        connect_timeout=10,
    )


def _redis_client(secret: dict):
    return redis.Redis(
        host=secret["host"],
        port=int(secret.get("port", 6379)),
        decode_responses=True,
        socket_timeout=3,
    )


def _process(cur, rc, rec: dict) -> None:
    """ECS sim 의 record dict → sales_realtime INSERT (schema v3 정합).

    sim record keys (online/offline-sim app.py):
      tx_id, isbn13, qty, unit_price, total_price (=revenue), channel,
      location_id (=store_id), ts, wh_id, payment_method, ...
    schema sales_realtime columns:
      txn_id PK, event_ts, store_id, wh_id, channel, isbn13, qty,
      unit_price, discount, revenue, payment_method, created_at
    """
    isbn13         = rec["isbn13"]
    store_id       = int(rec["location_id"])     # sim location_id == schema store_id
    wh_id          = int(rec.get("wh_id", 1))
    qty            = int(rec["qty"])
    unit_price     = int(rec.get("unit_price", 0))
    revenue        = int(rec.get("total_price", rec.get("revenue", qty * unit_price)))
    discount       = int(rec.get("discount", 0))
    channel        = rec.get("channel", "OFFLINE")
    payment_method = rec.get("payment_method")
    txn_id         = rec["tx_id"]                # sim tx_id == schema txn_id
    event_ts       = rec.get("ts", rec.get("event_ts", datetime.now(timezone.utc).isoformat()))

    cur.execute(
        """
        INSERT INTO sales_realtime
            (txn_id, event_ts, store_id, wh_id, channel, isbn13, qty,
             unit_price, discount, revenue, payment_method)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (txn_id) DO NOTHING
        """,
        (txn_id, event_ts, store_id, wh_id, channel, isbn13, qty,
         unit_price, discount, revenue, payment_method),
    )
    location_id = store_id  # 아래 inventory UPDATE 에서 sim 의 location_id 그대로 사용

    # Notion 명세: 온라인 매장 (location_type='STORE_ONLINE') 의 재고 출처 = WH 본체
    # → channel=ONLINE 또는 location.is_virtual=true 시 inventory 차감 대상 location 을 WH 본체로 substitute
    # schema: inventory(isbn13, location_id) PK · on_hand INT · reserved_qty INT · safety_stock INT · updated_at · updated_by
    cur.execute(
        """
        WITH target AS (
            SELECT CASE WHEN l.location_type = 'STORE_ONLINE'
                        THEN (SELECT location_id FROM locations
                              WHERE location_type='WH' AND wh_id=l.wh_id LIMIT 1)
                        ELSE l.location_id END AS lid
              FROM locations l WHERE l.location_id = %s
        )
        UPDATE inventory
        SET    on_hand    = GREATEST(on_hand - %s, 0),
               updated_at = NOW(),
               updated_by = 'pos-ingestor'
        WHERE  isbn13      = %s
          AND  location_id = (SELECT lid FROM target)
        RETURNING location_id
        """,
        (location_id, qty, isbn13),
    )
    updated_loc = cur.fetchone()
    target_loc = updated_loc[0] if updated_loc else location_id

    try:
        rc.delete(f"stock:{isbn13}:{target_loc}")
    except Exception as e:
        print(f"[pos-ingestor] Redis   {isbn13}:{target_loc}: {e}")


def lambda_handler(event, context):
    sm = boto3.client("secretsmanager", region_name=REGION)
    rds_sec   = _get_secret(sm, "bookflow/rds/master-password")
    redis_sec = _get_secret(sm, "bookflow/redis")

    conn     = _db_connect(rds_sec)
    rc       = _redis_client(redis_sec)
    records  = event.get("Records", [])
    failures = []

    try:
        for r in records:
            seq = r["kinesis"]["sequenceNumber"]
            try:
                payload = base64.b64decode(r["kinesis"]["data"]).decode("utf-8")
                rec     = json.loads(payload)
                with conn:
                    with conn.cursor() as cur:
                        _process(cur, rc, rec)
            except Exception:
                print(f"[pos-ingestor] seq={seq} \n{traceback.format_exc()}")
                failures.append({"itemIdentifier": seq})
    finally:
        conn.close()

    print(f"[pos-ingestor] {len(records)}  · {len(failures)} ")
    return {"batchItemFailures": failures}
