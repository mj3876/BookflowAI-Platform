"""spike-detect Lambda - z-score based detection.

Cron 10min. POS sales 기반 인기도 spike 검출 (V6.2 의 SNS mentions 채널 대신 POS 트래픽 사용 - 데모/Phase 3.5).
실 운영 시 sns-gen S3 raw 데이터로 교체 가능.

Algorithm:
  For each isbn13 with sales in last 1h:
    count_1h    = COUNT(sales_realtime last 1h)
    baseline_24h_avg/std = AVG/STDDEV over 24h hourly counts
    z = (count_1h - avg) / std (if std > 0)
    If z > Z_THRESHOLD: INSERT spike_events + Redis pub
"""
import json
import logging
import os
import uuid

import boto3
import psycopg
import redis

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

RDS_HOST = os.environ["RDS_HOST"]
RDS_PORT = int(os.environ.get("RDS_PORT", "5432"))
RDS_DB = os.environ.get("RDS_DB", "bookflow")
RDS_USER_ENV = os.environ.get("RDS_USER", "spike_detect")  # fallback if secret JSON has no username
RDS_SECRET_ARN = os.environ["RDS_SECRET_ARN"]
REDIS_HOST = os.environ["REDIS_HOST"]
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

Z_THRESHOLD = float(os.environ.get("Z_THRESHOLD", "2.0"))
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "60"))

_secrets = boto3.client("secretsmanager")
_rds_credentials: tuple[str, str] | None = None
_redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True, socket_connect_timeout=3)


def _get_credentials() -> tuple[str, str]:
    """Read (username, password) from secret JSON; fallback to env RDS_USER if secret has none.

    Supports per-pod secret (`bookflow/rds/spike_detect`) AND master fallback
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


# Demo algorithm (Phase 3.5):
#   - last Nh sales count per ISBN (`window` minutes)
#   - cross-ISBN mean (m) + stddev (s) of those counts
#   - For each ISBN: z = (count - m) / s; if z >= threshold -> spike
#
# Phase 4 swap: per-ISBN historic baseline (24h+ rolling), proper z-score.
# Cross-ISBN normalization는 "이 책이 평균 책보다 더 팔린다" semantic 로 sufficient for demo.
SQL_DETECT = """
WITH per_isbn AS (
    SELECT isbn13, COUNT(*)::numeric AS count_w
      FROM sales_realtime
     WHERE event_ts > NOW() - (INTERVAL '1 minute' * %(window)s)
     GROUP BY isbn13
),
stats AS (
    SELECT AVG(count_w) AS mean_w, COALESCE(STDDEV_POP(count_w), 0) AS std_w
      FROM per_isbn
)
SELECT p.isbn13, p.count_w::int AS count_1h,
       s.mean_w AS avg_h,
       s.std_w  AS std_h,
       (p.count_w - s.mean_w) / NULLIF(s.std_w, 0) AS z_score
  FROM per_isbn p CROSS JOIN stats s
 WHERE s.std_w > 0
   AND (p.count_w - s.mean_w) / s.std_w >= %(threshold)s
 ORDER BY z_score DESC
"""


def lambda_handler(event, context):
    detected = 0
    skipped = 0

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_DETECT, {"window": WINDOW_MINUTES, "threshold": Z_THRESHOLD})
            rows = cur.fetchall()

            for isbn13, count_1h, avg_h, std_h, z in rows:
                event_id = str(uuid.uuid4())
                z_rounded = float(round(z, 2))
                # Skip if duplicate spike for this ISBN in last 30min (cooldown)
                cur.execute(
                    "SELECT 1 FROM spike_events WHERE isbn13 = %s AND detected_at > NOW() - INTERVAL '30 minutes' LIMIT 1",
                    (isbn13,),
                )
                if cur.fetchone():
                    skipped += 1
                    continue

                cur.execute(
                    """
                    INSERT INTO spike_events (event_id, isbn13, z_score, mentions_count)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (event_id, isbn13, z_rounded, int(count_1h)),
                )
                cur.execute(
                    """
                    INSERT INTO audit_log (actor_type, actor_id, action, entity_type, entity_id, after_state)
                    VALUES ('lambda', 'spike-detect', 'spike.detected', 'spike_events', %s, %s)
                    """,
                    (event_id, json.dumps({
                        "isbn13": isbn13, "z_score": z_rounded, "count_1h": int(count_1h),
                        "baseline_avg": float(avg_h), "baseline_std": float(std_h),
                    })),
                )

                # Redis pub spike.detected (notification-svc + dashboard-svc subscribe)
                try:
                    _redis.publish("spike.detected", json.dumps({
                        "event_id": event_id,
                        "isbn13": isbn13,
                        "z_score": z_rounded,
                        "mentions_count": int(count_1h),
                        "severity": "CRITICAL" if z_rounded >= 3.0 else "WARNING",
                    }))
                except Exception as e:
                    log.warning("redis publish failed for %s: %s", isbn13, e)

                detected += 1
        conn.commit()

    log.info("spike-detect: detected=%d skipped(cooldown)=%d", detected, skipped)
    return {"statusCode": 200, "detected": detected, "skipped": skipped}
