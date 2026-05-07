"""
[4/30]   API   
Task 7 ETL2 · aladin-sync   

:
    pip install requests python-dotenv boto3
    export ALADIN_TTB_KEY=ttbxxxxxxxx
    python aladin_fetch.py                     #  
    python aladin_fetch.py --upload-s3 --bucket my-raw-bucket   # S3 

:
    ./output/aladin_YYYYMMDD_HHMMSS.ndjson.gz  (S3 Raw  )
    ./output/aladin_YYYYMMDD_HHMMSS.csv        ()
"""
import argparse
import csv
import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ALADIN_BASE = "http://www.aladin.co.kr/ttb/api/ItemList.aspx"

#  : (QueryType, CategoryId, )
FETCH_TARGETS = [
    ("Bestseller",      0,  " "),
    ("Bestseller",      1,  " "),
    ("Bestseller",      2,  "/ "),
    ("Bestseller",      4,  " "),
    ("Bestseller",      8,  " "),
    ("ItemNewSpecial",  0,  "  "),
    ("ItemNewSpecial",  1,  " "),
    ("ItemNewSpecial",  2,  "/ "),
    ("ItemNewAll",      0,  " "),
]

MAX_RESULTS_PER_PAGE = 50
MAX_PAGES = 4  #  200/


def fetch_aladin_page(ttbkey: str, query_type: str, category_id: int,
                      page: int = 1) -> list[dict]:
    params = {
        "ttbkey":       ttbkey,
        "QueryType":    query_type,
        "MaxResults":   MAX_RESULTS_PER_PAGE,
        "start":        page,
        "SearchTarget": "Book",
        "CategoryId":   category_id,
        "output":       "js",
        "Version":      "20131101",
        "Cover":        "Mid",
    }
    try:
        r = requests.get(ALADIN_BASE, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("item", [])
    except requests.RequestException as e:
        print(f"  [!] API  type={query_type} cat={category_id} page={page}: {e}")
        return []
    except (ValueError, KeyError) as e:
        print(f"  [!]   : {e}")
        return []


def parse_item(item: dict, query_type: str, category_id: int,
               now: datetime) -> dict:
    return {
        "isbn13":      item.get("isbn13", "").strip(),
        "isbn":        item.get("isbn", "").strip(),
        "title":       item.get("title", "").strip(),
        "author":      item.get("author", "").strip(),
        "publisher":   item.get("publisher", "").strip(),
        "pub_date":    item.get("pubDate", "").strip(),
        "price":       int(item.get("priceSales", 0)),
        "price_standard": int(item.get("priceStandard", 0)),
        "category":    item.get("categoryName", "").strip(),
        "cover_url":   item.get("cover", "").strip(),
        "link":        item.get("link", "").strip(),
        "query_type":  query_type,
        "category_id": category_id,
        "rating":      float(item.get("customerReviewRank", 0)),
        "review_count": int(item.get("reviewCount", 0)),
        "sales_point": int(item.get("salesPoint", 0)),
        "description": item.get("description", "").strip()[:500],
        "synced_at":   now.isoformat(),
    }


def collect_all(ttbkey: str) -> list[dict]:
    seen: set[str] = set()
    books: list[dict] = []
    now = datetime.now(timezone.utc)

    total_api_calls = 0
    for query_type, category_id, desc in FETCH_TARGETS:
        print(f"   : {desc} (type={query_type}, cat={category_id})")
        page_books = 0
        for page in range(1, MAX_PAGES + 1):
            items = fetch_aladin_page(ttbkey, query_type, category_id, page)
            total_api_calls += 1

            if not items:
                break

            for item in items:
                isbn13 = item.get("isbn13", "").strip()
                if not isbn13 or len(isbn13) != 13 or isbn13 in seen:
                    continue
                seen.add(isbn13)
                books.append(parse_item(item, query_type, category_id, now))
                page_books += 1

            if len(items) < MAX_RESULTS_PER_PAGE:
                break

            time.sleep(0.3)  # API rate limit 

        print(f"    →  {page_books} ( {len(books)}, API {total_api_calls})")

    return books


def save_local(books: list[dict], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # NDJSON gzip (S3 Raw  )
    ndjson_path = output_dir / f"aladin_{ts}.ndjson.gz"
    ndjson_body = "\n".join(json.dumps(b, ensure_ascii=False) for b in books)
    with gzip.open(ndjson_path, "wb") as f:
        f.write(ndjson_body.encode("utf-8"))

    # CSV ()
    csv_path = output_dir / f"aladin_{ts}.csv"
    if books:
        fieldnames = list(books[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(books)

    return ndjson_path, csv_path


def upload_s3(books: list[dict], bucket: str, now: datetime) -> str:
    import boto3
    s3 = boto3.client("s3")
    partition = (
        f"aladin/year={now.year}/month={now.month:02d}/day={now.day:02d}"
    )
    key = f"{partition}/aladin_{now.strftime('%H%M%S')}.json.gz"
    ndjson = "\n".join(json.dumps(b, ensure_ascii=False) for b in books)
    body   = gzip.compress(ndjson.encode("utf-8"))
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentEncoding="gzip")
    return f"s3://{bucket}/{key}"


def print_stats(books: list[dict]) -> None:
    if not books:
        print("  ")
        return
    query_types = {}
    for b in books:
        qt = b["query_type"]
        query_types[qt] = query_types.get(qt, 0) + 1
    publishers = {}
    for b in books:
        p = b["publisher"]
        publishers[p] = publishers.get(p, 0) + 1
    top5 = sorted(publishers.items(), key=lambda x: -x[1])[:5]

    print(f"\n{'='*50}")
    print(f"  : {len(books)}")
    print(f"QueryType:")
    for qt, cnt in query_types.items():
        print(f"  {qt}: {cnt}")
    print(f" Top5:")
    for pub, cnt in top5:
        print(f"  {pub}: {cnt}")
    avg_price = sum(b["price"] for b in books) / len(books)
    print(f" : {avg_price:,.0f}")
    print(f"{'='*50}")


def main():
    parser = argparse.ArgumentParser(description=" API   ")
    parser.add_argument("--ttbkey",     default=os.environ.get("ALADIN_TTB_KEY", ""),
                        help=" TTBKey (env: ALADIN_TTB_KEY)")
    parser.add_argument("--upload-s3",  action="store_true",
                        help="S3  (--bucket )")
    parser.add_argument("--bucket",     default=os.environ.get("RAW_BUCKET", ""),
                        help="S3  (env: RAW_BUCKET)")
    parser.add_argument("--output-dir", default="./output",
                        help="   (: ./output)")
    args = parser.parse_args()

    if not args.ttbkey:
        print("[] ALADIN_TTB_KEY   --ttbkey ")
        print("          TTBKey : https://www.aladin.co.kr/ttb/wblog_list.aspx")
        return 1

    print(f"[ API  ] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" : {len(FETCH_TARGETS)}  ×  {MAX_PAGES}")

    now = datetime.now(timezone.utc)
    books = collect_all(args.ttbkey)

    if not books:
        print("[]   . TTBKey .")
        return 1

    ndjson_path, csv_path = save_local(books, Path(args.output_dir))
    print(f"\n[  ]")
    print(f"  NDJSON: {ndjson_path}")
    print(f"  CSV:    {csv_path}")

    if args.upload_s3:
        if not args.bucket:
            print("[] S3   --bucket ")
            return 1
        s3_uri = upload_s3(books, args.bucket, now)
        print(f"  S3:     {s3_uri}")

    print_stats(books)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
