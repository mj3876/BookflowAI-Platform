"""
event-sync Lambda
 03:00 KST(UTC 18:00) → S3 Raw/events/{event_type}/ gzip JSON

event_etl.py(BookFlowAI-Apps)  :
event_id, event_type, title, start_date, end_date, location, isbn13_list, synced_at

S3 : events/{event_type}/year=YYYY/month=MM/day=DD/
event_type: book_fair, holiday, publisher_promo, author_signing
"""
import gzip
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import requests

REGION      = os.environ.get("AWS_REGION", "ap-northeast-1")
HOLIDAY_URL = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"

#   
BOOK_FAIRS = [
    {"month": 2,  "title": " ",        "duration": 5,  "location": " "},
    {"month": 6,  "title": "",            "duration": 5,  "location": " "},
    {"month": 9,  "title": "",            "duration": 4,  "location": " "},
    {"month": 10, "title": " ",   "duration": 6,  "location": " "},
    {"month": 11, "title": "",           "duration": 3,  "location": " "},
]

#     ()
PUBLISHER_PROMOS = [
    {"month": 3,  "title": "   ",    "location": " "},
    {"month": 6,  "title": "  ",        "location": " "},
    {"month": 9,  "title": "  ",          "location": " "},
    {"month": 12, "title": "   ",   "location": " "},
]


def _get_secret(sm, name: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _date_add(base: str, days: int) -> str:
    d = datetime.strptime(base, "%Y%m%d") + timedelta(days=days)
    return d.strftime("%Y-%m-%d")


def _fmt(yyyymmdd: str) -> str:
    try:
        return datetime.strptime(str(yyyymmdd), "%Y%m%d").strftime("%Y-%m-%d")
    except Exception:
        return str(yyyymmdd)


def collect_holidays(service_key: str, years: list[int]) -> list[dict]:
    events = []
    for year in years:
        for month in range(1, 13):
            try:
                r = requests.get(
                    HOLIDAY_URL,
                    params={
                        "serviceKey": service_key,
                        "solYear":    year,
                        "solMonth":   f"{month:02d}",
                        "_type":      "json",
                        "numOfRows":  50,
                    },
                    timeout=10,
                )
                body  = r.json().get("response", {}).get("body", {})
                items = body.get("items", {}).get("item", [])
                if isinstance(items, dict):
                    items = [items]
                for it in items:
                    date_str = _fmt(it.get("locdate", ""))
                    events.append({
                        "event_id":    str(uuid.uuid4()),
                        "event_type":  "holiday",
                        "title":       it.get("dateName", ""),
                        "start_date":  date_str,
                        "end_date":    date_str,
                        "location":    "",
                        "isbn13_list": [],
                    })
            except Exception as e:
                print(f"[event-sync] holiday API error {year}/{month}: {e}")
    return events


def collect_book_fairs(years: list[int]) -> list[dict]:
    events = []
    for year in years:
        for bf in BOOK_FAIRS:
            start = f"{year}{bf['month']:02d}01"
            events.append({
                "event_id":    str(uuid.uuid4()),
                "event_type":  "book_fair",
                "title":       bf["title"],
                "start_date":  _fmt(start),
                "end_date":    _date_add(start, bf["duration"] - 1),
                "location":    bf["location"],
                "isbn13_list": [],
            })
    return events


def collect_publisher_promos(years: list[int]) -> list[dict]:
    events = []
    for year in years:
        for pp in PUBLISHER_PROMOS:
            start = f"{year}{pp['month']:02d}01"
            events.append({
                "event_id":    str(uuid.uuid4()),
                "event_type":  "publisher_promo",
                "title":       pp["title"],
                "start_date":  _fmt(start),
                "end_date":    _date_add(start, 29),
                "location":    pp["location"],
                "isbn13_list": [],
            })
    return events


def collect_author_signings(years: list[int]) -> list[dict]:
    #    ( API    )
    events = []
    months = [4, 7, 10]
    for year in years:
        for month in months:
            start = f"{year}{month:02d}15"
            events.append({
                "event_id":    str(uuid.uuid4()),
                "event_type":  "author_signing",
                "title":       f"{year} {month}  ",
                "start_date":  _fmt(start),
                "end_date":    _fmt(start),
                "location":    "  ",
                "isbn13_list": [],
            })
    return events


def upload_by_type(s3, raw_bucket: str, events: list[dict],
                   partition: str, now: datetime) -> None:
    by_type: dict[str, list] = {}
    for e in events:
        et = e["event_type"]
        by_type.setdefault(et, []).append(e)

    synced_at = now.isoformat()
    for etype, items in by_type.items():
        for item in items:
            item["synced_at"] = synced_at
        ndjson = "\n".join(json.dumps(e, ensure_ascii=False) for e in items)
        body   = gzip.compress(ndjson.encode("utf-8"))
        key    = f"events/{etype}/{partition}/events_{now.strftime('%H%M%S')}.json.gz"
        s3.put_object(Bucket=raw_bucket, Key=key, Body=body, ContentEncoding="gzip")
        print(f"[event-sync] {etype}: {len(items)} → s3://{raw_bucket}/{key}")


def lambda_handler(event, context):
    sm         = boto3.client("secretsmanager", region_name=REGION)
    s3         = boto3.client("s3",             region_name=REGION)
    raw_bucket = os.environ["RAW_BUCKET"]
    secret     = _get_secret(sm, "bookflow/external/publicdata-apikey")
    service_key = secret["apikey"]

    now       = datetime.now(timezone.utc)
    years     = [now.year, now.year + 1]
    partition = f"year={now.year}/month={now.month:02d}/day={now.day:02d}"

    all_events: list[dict] = []
    all_events += collect_holidays(service_key, years)
    all_events += collect_book_fairs(years)
    all_events += collect_publisher_promos(years)
    all_events += collect_author_signings(years)

    upload_by_type(s3, raw_bucket, all_events, partition, now)
    return {"statusCode": 200, "total": len(all_events)}
