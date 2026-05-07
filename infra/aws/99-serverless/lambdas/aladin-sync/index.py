"""
aladin-sync Lambda
 04:00 KST(UTC 19:00) Aladin API → S3 Raw/aladin/ gzip JSON

aladin_etl.py(BookFlowAI-Apps)  :
isbn13, title, author, publisher, pub_date, category_id, category_name,
price, cover_url, sales_point, stock_status, synced_at
"""
import gzip
import json
import os
from datetime import datetime, timezone

import boto3
import requests

REGION = os.environ.get("AWS_REGION", "ap-northeast-1")
ALADIN_BASE  = "http://www.aladin.co.kr/ttb/api/ItemList.aspx"
CATEGORY_IDS = [0, 1, 2, 4, 8]
QUERY_TYPES  = ["Bestseller", "ItemNewSpecial"]


def _get_secret(sm, name: str) -> dict:
    return json.loads(sm.get_secret_value(SecretId=name)["SecretString"])


def _fetch_aladin(ttbkey: str, query_type: str, category_id: int) -> list[dict]:
    params = {
        "ttbkey":       ttbkey,
        "QueryType":    query_type,
        "MaxResults":   50,
        "start":        1,
        "SearchTarget": "Book",
        "CategoryId":   category_id,
        "output":       "js",
        "Version":      "20131101",
        "Cover":        "Mid",
    }
    try:
        r = requests.get(ALADIN_BASE, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("item", [])
    except Exception as e:
        print(f"[aladin-sync] fetch error type={query_type} cat={category_id}: {e}")
        return []


def lambda_handler(event, context):
    sm         = boto3.client("secretsmanager", region_name=REGION)
    s3         = boto3.client("s3",             region_name=REGION)
    raw_bucket = os.environ["RAW_BUCKET"]
    secret     = _get_secret(sm, "bookflow/external/aladin-ttbkey")
    ttbkey     = secret["ttbkey"]

    now       = datetime.now(timezone.utc)
    partition = f"year={now.year}/month={now.month:02d}/day={now.day:02d}"

    seen:  set[str]   = set()
    books: list[dict] = []

    for qt in QUERY_TYPES:
        for cat in CATEGORY_IDS:
            for item in _fetch_aladin(ttbkey, qt, cat):
                isbn13 = item.get("isbn13", "").strip()
                if not isbn13 or isbn13 in seen:
                    continue
                seen.add(isbn13)
                # aladin_etl.py   
                books.append({
                    "isbn13":        isbn13,
                    "title":         item.get("title", ""),
                    "author":        item.get("author", ""),
                    "publisher":     item.get("publisher", ""),
                    "pub_date":      item.get("pubDate", ""),
                    "category_id":   int(item.get("categoryId", cat)),
                    "category_name": item.get("categoryName", ""),  # aladin_etl.py 
                    "price":         int(item.get("priceSales", 0)),
                    "cover_url":     item.get("cover", ""),
                    "sales_point":   int(item.get("salesPoint", 0)),
                    "stock_status":  item.get("stockStatus", ""),
                    "query_type":    qt,
                    "rating":        float(item.get("customerReviewRank", 0)),
                    "synced_at":     now.isoformat(),
                })

    ndjson = "\n".join(json.dumps(b, ensure_ascii=False) for b in books)
    body   = gzip.compress(ndjson.encode("utf-8"))
    key    = f"aladin/{partition}/aladin_{now.strftime('%H%M%S')}.json.gz"

    s3.put_object(Bucket=raw_bucket, Key=key, Body=body, ContentEncoding="gzip")
    print(f"[aladin-sync] {len(books)} books → s3://{raw_bucket}/{key}")
    return {"statusCode": 200, "synced": len(books), "s3_key": key}
