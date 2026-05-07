"""
[4/30 ~ 5/4] Task7 ETL2 · sns-gen Lambda
10 cron

sns_agg.py(BookFlowAI-Apps)  :
mention_id, isbn13, platform, mention_count, sentiment_score, collected_at
"""
import gzip
import json
import math
import os
import random
import uuid
from datetime import datetime, timezone

import boto3

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")

PLATFORMS    = ["twitter", "instagram", "blog", "community", "bookstore_review"]
SENTIMENTS   = ["positive", "neutral", "negative"]
SENT_WEIGHTS = [0.65, 0.25, 0.10]

TEMPLATES = [
    "{title}     ",
    "{title}  ! {author}  ",
    "{title}     ",
    "{title}      ",
    "{author}  {title}   ?",
    "{title}    ",
    " {title}  ?",
    "{title}    ",
]


def _get_config(sm) -> dict:
    return json.loads(sm.get_secret_value(SecretId="bookflow/sns-gen-config")["SecretString"])


def _poisson(lam: float) -> int:
    L, k, p = math.exp(-lam), 0, 1.0
    while p > L:
        k += 1
        p *= random.random()
    return k - 1


def _sentiment_score(sentiment: str) -> float:
    base = {"positive": 0.75, "neutral": 0.45, "negative": 0.15}
    return round(base.get(sentiment, 0.5) + random.uniform(-0.1, 0.1), 4)


def lambda_handler(event, context):
    sm         = boto3.client("secretsmanager", region_name=REGION)
    s3         = boto3.client("s3",             region_name=REGION)
    raw_bucket = os.environ["RAW_BUCKET"]
    config     = _get_config(sm)
    tracked    = config.get("tracked_isbns", [])

    now       = datetime.now(timezone.utc)
    partition = f"sns/year={now.year}/month={now.month:02d}/day={now.day:02d}/hour={now.hour:02d}"

    records: list[dict] = []
    for book in tracked:
        isbn13   = book["isbn13"]
        lam      = float(book.get("baseline_lam", 5.0))
        count    = _poisson(lam)
        is_spike = random.random() < 0.05
        if is_spike:
            count = int(count * random.uniform(10, 30)) + 10

        sentiment = random.choices(SENTIMENTS, SENT_WEIGHTS)[0]
        tmpl      = random.choice(TEMPLATES)
        records.append({
            "mention_id":      str(uuid.uuid4()),
            "isbn13":          isbn13,
            "platform":        random.choice(PLATFORMS),
            "content":         tmpl.format(title=book.get("title", ""), author=book.get("author", "")),
            "mention_count":   max(0, count),
            "sentiment":       sentiment,
            "sentiment_score": _sentiment_score(sentiment),
            "is_spike_seed":   is_spike,
            "collected_at":    now.isoformat(),
            "is_synthetic":    True,
        })

    random.shuffle(records)
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    body   = gzip.compress(ndjson.encode("utf-8"))
    key    = f"{partition}/sns_{now.strftime('%M%S')}.json.gz"
    s3.put_object(Bucket=raw_bucket, Key=key, Body=body, ContentEncoding="gzip")
    print(f"[sns-gen] {len(records)} records → s3://{raw_bucket}/{key}")
    return {"statusCode": 200, "records": len(records)}
