"""
[5/6~5/7] Task7 ETL2 · spike-detect Lambda
10 cron ·  1 SNS   → Poisson Z-score ≥ 3.0 → RDS spike_events INSERT
VPC   (BookFlowAI VPC · RDS  )
"""
import gzip
import json
import math
import os
from datetime import datetime, timedelta, timezone

import boto3
import psycopg2

REGION      = os.environ.get("AWS_REGION", "ap-northeast-1")
Z_THRESHOLD = float(os.environ.get("Z_THRESHOLD", "3.0"))
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "1"))


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


def _read_sns_window(s3, bucket: str, now: datetime) -> dict[str, int]:
    """ WINDOW_HOURS  S3 SNS → isbn13 mention_count """
    counts: dict[str, int] = {}
    for delta in range(WINDOW_HOURS + 1):
        h = now - timedelta(hours=delta)
        prefix = (
            f"sns/year={h.year}/month={h.month:02d}"
            f"/day={h.day:02d}/hour={h.hour:02d}/"
        )
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                try:
                    raw  = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    text = gzip.decompress(raw).decode("utf-8")
                    for line in text.splitlines():
                        if not line.strip():
                            continue
                        rec    = json.loads(line)
                        isbn13 = rec.get("isbn13", "")
                        if isbn13:
                            counts[isbn13] = counts.get(isbn13, 0) + 1
                except Exception as e:
                    print(f"[spike-detect] S3   {obj['Key']}: {e}")
    return counts


def _z_score(count: int, lam: float) -> float:
    return (count - lam) / math.sqrt(lam) if lam > 0 else 0.0


def lambda_handler(event, context):
    sm         = boto3.client("secretsmanager", region_name=REGION)
    s3         = boto3.client("s3",             region_name=REGION)
    raw_bucket = os.environ["RAW_BUCKET"]

    cfg     = _get_secret(sm, "bookflow/sns-gen-config")
    tracked = {b["isbn13"]: b for b in cfg.get("tracked_isbns", [])}
    rds_sec = _get_secret(sm, "bookflow/rds-master")

    now    = datetime.now(timezone.utc)
    counts = _read_sns_window(s3, raw_bucket, now)

    spikes = []
    for isbn13, book in tracked.items():
        lam   = float(book.get("baseline_lam", 5.0))
        count = counts.get(isbn13, 0)
        z     = _z_score(count, lam)
        if z >= Z_THRESHOLD:
            spikes.append({
                "isbn13":          isbn13,
                "detected_at":     now.isoformat(),
                "z_score":         round(z, 4),
                "mention_count":   count,
                "baseline_count":  round(lam, 2),
                "window_hours":    WINDOW_HOURS,
                "is_resolved":     False,
            })

    print(
        f"[spike-detect] {len(counts)} ISBNs  "
        f"· {len(spikes)} spikes (Z≥{Z_THRESHOLD})"
    )

    if not spikes:
        return {"statusCode": 200, "spikes": 0}

    conn = _db_connect(rds_sec)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO spike_events
                        (isbn13, detected_at, z_score, mention_count,
                         baseline_count, is_resolved)
                    VALUES (%(isbn13)s, %(detected_at)s, %(z_score)s,
                            %(mention_count)s, %(baseline_count)s, %(is_resolved)s)
                    ON CONFLICT (isbn13, detected_at) DO NOTHING
                    """,
                    spikes,
                )
    finally:
        conn.close()

    return {"statusCode": 200, "spikes": len(spikes)}
