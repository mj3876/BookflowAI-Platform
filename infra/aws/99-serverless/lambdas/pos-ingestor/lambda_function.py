"""pos-ingestor Lambda · Kinesis ESM consumer.

Reads `bookflow-pos-events` stream (online-sim/offline-sim 가 put_record).
Per record:
  1. INSERT sales_realtime (V3: txn_id UUID PK · event_ts · store_id · wh_id · channel · isbn13 · qty · unit_price · revenue)
  2. UPDATE inventory SET on_hand -= qty WHERE (isbn13, location_id=store_id)
  3. INSERT audit_log
  4. Redis publish stock.changed

Failures reported via batchItemFailures so Kinesis ESM retries only the failed records.
"""
import base64
import json
import logging
import os

import boto3
import psycopg
import redis

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

RDS_HOST = os.environ["RDS_HOST"]
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
RDS_DB = os.environ.get("RDS_DB", "bookflow")
RDS_USER_ENV = os.environ.get("RDS_USER", "pos_ingestor")  # fallback if secret JSON has no username
RDS_SECRET_ARN = os.environ["RDS_SECRET_ARN"]
REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

# location_id (store) → wh_id mapping. Phase 2 fixed; Phase 3+ fetch from RDS or Parameter Store.
# location 1-5 = wh 1 (수도권), 6-10 = wh 2 (영남), 11=online-app→wh1, 12=online-web→wh1
LOC_TO_WH = {1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 2, 7: 2, 8: 2, 9: 2, 10: 2, 11: 1, 12: 1}

_secrets = boto3.client("secretsmanager")
_rds_credentials: tuple[str, str] | None = None
_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=3)


def _get_credentials() -> tuple[str, str]:
    """Read (username, password) from secret JSON; fallback to env RDS_USER if secret has none.

    Supports per-pod secret (`bookflow/rds/pos_ingestor`) AND master fallback
    (`bookflow/rds/master-password` returns `{username: bookflow_admin, ...}`).
    """
    global _rds_credentials
    if _rds_credentials is None:
        sec = _secrets.get_secret_value(SecretId=RDS_SECRET_ARN)
        body = json.loads(sec["SecretString"])
        user = body.get("username") or body.get("Username") or RDS_USER_ENV
        pwd = body.get("password") or body.get("Password")
        _rds_credentials = (user, pwd)
    return _rds_credentials


def _conn():
    user, pwd = _get_credentials()
    return psycopg.connect(
        host=RDS_HOST, port=RDS_PORT, dbname=RDS_DB,
        user=user, password=pwd,
        connect_timeout=5,
    )


def _process_one(conn, payload: dict) -> dict:
    """One sales record → RDS rows + Redis pub. Returns published payload."""
    txn_id = payload["tx_id"]
    isbn13 = payload["isbn13"]
    qty = int(payload["qty"])
    unit_price = int(payload["unit_price"])
    channel = payload["channel"][:10]
    store_id = int(payload["location_id"])
    wh_id = LOC_TO_WH.get(store_id, 1)
    event_ts = payload["ts"]
    revenue = int(payload.get("total_price", qty * unit_price))

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sales_realtime
                (txn_id, event_ts, store_id, wh_id, channel, isbn13, qty, unit_price, discount, revenue)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, %s)
            ON CONFLICT (txn_id) DO NOTHING
            """,
            (txn_id, event_ts, store_id, wh_id, channel, isbn13, qty, unit_price, revenue),
        )
        # inventory delta (decrement on_hand). NULL row tolerated (book not stocked at this location).
        cur.execute(
            """
            UPDATE inventory
               SET on_hand = on_hand - %s, updated_at = NOW(), updated_by = 'pos-ingestor'
             WHERE isbn13 = %s AND location_id = %s
            RETURNING on_hand, reserved_qty
            """,
            (qty, isbn13, store_id),
        )
        row = cur.fetchone()
        on_hand_after = row[0] if row else None
        available = (row[0] - row[1]) if row else None

        cur.execute(
            """
            INSERT INTO audit_log (actor_type, actor_id, action, entity_type, entity_id, after_state)
            VALUES ('lambda', 'pos-ingestor', 'sales.ingest', 'sales_realtime', %s, %s)
            """,
            (txn_id, json.dumps({"isbn13": isbn13, "store_id": store_id, "qty": qty, "on_hand_after": on_hand_after})),
        )

    return {
        "isbn13": isbn13,
        "location_id": store_id,
        "available": available,
        "ts": event_ts,
    }


def lambda_handler(event, context):
    records = event.get("Records", [])
    failures = []

    if not records:
        return {"batchItemFailures": []}

    try:
        with _conn() as conn:
            published = []
            for rec in records:
                seq = rec["kinesis"]["sequenceNumber"]
                try:
                    payload = json.loads(base64.b64decode(rec["kinesis"]["data"]))
                    pub = _process_one(conn, payload)
                    published.append(pub)
                except Exception as e:
                    log.exception("record %s failed: %s", seq, e)
                    failures.append({"itemIdentifier": seq})
            conn.commit()

        # publish AFTER commit (so subscribers see consistent state)
        for p in published:
            try:
                _redis.publish("stock.changed", json.dumps(p))
            except Exception as e:
                log.warning("redis publish failed: %s", e)

    except Exception as e:
        log.exception("batch failed: %s", e)
        # Whole batch failed at connect time - retry all
        return {"batchItemFailures": [{"itemIdentifier": r["kinesis"]["sequenceNumber"]} for r in records]}

    log.info("processed %d / %d records", len(records) - len(failures), len(records))
    return {"batchItemFailures": failures}
