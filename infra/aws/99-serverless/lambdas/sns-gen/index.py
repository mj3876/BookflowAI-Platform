"""
sns-gen Lambda
10 cron · 70 ISBN  SNS   → S3 Raw/sns/ gzip JSON

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

PLATFORMS = ["twitter", "instagram", "blog", "community", "bookstore_review"]

# sentiment → sentiment_score  (sns_agg.py double )
SENTIMENT_SCORES = {
    "positive": round(random.uniform(0.6, 1.0), 2),
    "neutral":  round(random.uniform(0.3, 0.6), 2),
    "negative": round(random.uniform(0.0, 0.3), 2),
}
SENTIMENTS   = ["positive", "neutral", "negative"]
SENT_WEIGHTS = [0.65, 0.25, 0.10]

TEMPLATES = [
    "{title}     ",
    "{title}  ! {author}  ",
    "{title}     ",
    "{title}      ",
    "{title}      ",
    "{author}  {title}   ?",
    "{title}    ",
    "{title}  ...  ",
    " {title}  ?",
    "{title}    ",
    "{title} e     ",
    "{author}  {title}   ",
]

SPIKE_PROB     = 0.05
SPIKE_MULT_MIN = 10
SPIKE_MULT_MAX = 30


def _get_config(sm) -> dict:
    return json.loads(
        sm.get_secret_value(SecretId="bookflow/etl/sns-gen-config")["SecretString"]
    )


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
    partition = (
        f"sns/year={now.year}/month={now.month:02d}"
        f"/day={now.day:02d}/hour={now.hour:02d}"
    )

    records: list[dict] = []
    spike_count = 0

    for book in tracked:
        isbn13   = book["isbn13"]
        title    = book.get("title", "")
        author   = book.get("author", "")
        lam      = float(book.get("baseline_lam", 5.0))
        count    = _poisson(lam)
        is_spike = random.random() < SPIKE_PROB

        if is_spike:
            count = int(count * random.uniform(SPIKE_MULT_MIN, SPIKE_MULT_MAX)) + SPIKE_MULT_MIN
            spike_count += 1

        sentiment = random.choices(SENTIMENTS, SENT_WEIGHTS)[0]
        tmpl      = random.choice(TEMPLATES)

        # sns_agg.py : mention_id, isbn13, platform, mention_count, sentiment_score, collected_at
        records.append({
            "mention_id":      str(uuid.uuid4()),
            "isbn13":          isbn13,
            "platform":        random.choice(PLATFORMS),
            "content":         tmpl.format(title=title, author=author),
            "mention_count":   max(0, count),
            "sentiment":       sentiment,
            "sentiment_score": _sentiment_score(sentiment),
            "is_spike_seed":   is_spike,
            "collected_at":    now.isoformat(),   # sns_agg.py 
            "is_synthetic":    True,
        })

    random.shuffle(records)
    ndjson = "\n".join(json.dumps(r, ensure_ascii=False) for r in records)
    body   = gzip.compress(ndjson.encode("utf-8"))
    key    = f"{partition}/sns_{now.strftime('%M%S')}.json.gz"

    s3.put_object(Bucket=raw_bucket, Key=key, Body=body, ContentEncoding="gzip")
    print(f"[sns-gen] {len(records)} records (spikes={spike_count}) → s3://{raw_bucket}/{key}")
    return {"statusCode": 200, "records": len(records), "spikes": spike_count}
