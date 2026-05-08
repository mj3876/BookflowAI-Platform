"""Aladin ItemList Bestseller 1000권 (10 카테고리 × 100권) 수집 + 커버 S3 업로드.

매일 redeploy 시점에는 호출 안 함 (idempotent 보장 위해 books_aladin.json + S3 cover snapshot 사용).
이 스크립트는 시드 데이터 갱신 시 1회성 수동 실행.

Output:
  - books_aladin.json (1000개 dict · cover_url 은 S3 URL)
  - s3://bookflow-book-covers-{ACCOUNT}/covers/{isbn13}.jpg (1000장)

Usage:
    py fetch_aladin.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).parent / "books_aladin.json"
PER_CALL = 50

# 알라딘 CategoryId · 한국 도서 시장 광범위 (Bestseller + ItemNewAll 두 채널 조합)
CATEGORIES = [
    (1,     "소설/시/희곡"),
    (170,   "경제경영"),
    (656,   "자기계발"),
    (798,   "인문학"),
    (50917, "사회과학"),
    (798,   "역사"),
    (1108,  "어린이"),
    (336,   "가정/요리/뷰티"),
    (74,    "에세이"),
    (517,   "건강/취미"),
    (351,   "예술/대중문화"),
    (55890, "청소년"),
    (50921, "종교/역학"),
    (987,   "과학"),
    (517,   "취미/실용/스포츠"),
]
QUERY_TYPES = ["Bestseller", "ItemNewAll", "ItemEditorChoice"]
TARGET = 1000


def _ttbkey() -> str:
    if env := os.environ.get("TTBKEY"):
        return env
    import boto3
    sm = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "bookflow-deploy"),
                       region_name=os.environ.get("AWS_REGION", "ap-northeast-1")).client("secretsmanager")
    val = sm.get_secret_value(SecretId="bookflow/external/aladin-ttbkey")["SecretString"]
    try:
        d = json.loads(val)
        return d.get("TTBKey") or d.get("ttbkey") or d.get("key") or val
    except Exception:
        return val


def fetch_page(ttb: str, query_type: str, category_id: int, start: int) -> list[dict]:
    qs = urllib.parse.urlencode({
        "ttbkey": ttb,
        "QueryType": query_type,
        "SearchTarget": "Book",
        "CategoryId": category_id,
        "MaxResults": PER_CALL,
        "start": start,
        "output": "js",
        "Version": "20131101",
        "Cover": "Big",
    })
    req = urllib.request.Request(
        f"http://www.aladin.co.kr/ttb/api/ItemList.aspx?{qs}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8")).get("item", [])


def ensure_bucket(s3, bucket: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
        print(f"  bucket exists: {bucket}")
        return
    except Exception:
        pass
    print(f"  creating bucket: {bucket}")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket)
    else:
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    # public read (cover 이미지 SPA 가 직접 GET)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": False, "IgnorePublicAcls": False,
            "BlockPublicPolicy": False, "RestrictPublicBuckets": False,
        },
    )
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{"Sid": "PublicReadCover", "Effect": "Allow", "Principal": "*",
                       "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/covers/*"}],
    }))
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={
        "CORSRules": [{"AllowedHeaders": ["*"], "AllowedMethods": ["GET", "HEAD"],
                       "AllowedOrigins": ["*"], "MaxAgeSeconds": 3000}],
    })


def upload_cover(s3, bucket: str, isbn13: str, src_url: str) -> str:
    """알라딘 cover URL → S3. 이미 있으면 skip. 반환: S3 public URL."""
    key = f"covers/{isbn13}.jpg"
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except Exception:
        try:
            req = urllib.request.Request(src_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="image/jpeg",
                          CacheControl="public, max-age=86400")
        except Exception as e:
            return src_url  # fallback to aladin direct
    return f"https://{bucket}.s3.ap-northeast-1.amazonaws.com/{key}"


def main() -> None:
    import boto3
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    profile = os.environ.get("AWS_PROFILE", "bookflow-deploy")
    sess = boto3.Session(profile_name=profile, region_name=region)
    sts = sess.client("sts"); s3 = sess.client("s3")
    account = sts.get_caller_identity()["Account"]
    bucket = f"bookflow-book-covers-{account}"
    ensure_bucket(s3, bucket, region)

    ttb = _ttbkey()
    print(f"\nTTBKey: {ttb[:8]}...")
    items: list[dict] = []
    seen: set[str] = set()
    # QueryType × Category 모든 조합 시도, 1000권 도달 시 stop
    for qtype in QUERY_TYPES:
        if len(items) >= TARGET: break
        for cat_id, cat_label in CATEGORIES:
            if len(items) >= TARGET: break
            before = len(items)
            for start in range(1, 201, PER_CALL):  # start 1, 51, 101, 151
                if len(items) >= TARGET: break
                try:
                    batch = fetch_page(ttb, qtype, cat_id, start)
                except Exception as e:
                    print(f"  {qtype} cat={cat_id} start={start} ERR: {e}"); continue
                if not batch: break  # 빈 결과면 다음 카테고리
                for it in batch:
                    isbn = it.get("isbn13") or ""
                    if not isbn or isbn in seen:
                        continue
                    seen.add(isbn)
                    cover_src = it.get("cover") or ""
                    cover_url = upload_cover(s3, bucket, isbn, cover_src) if cover_src else ""
                    items.append({
                        "isbn13": isbn,
                        "isbn10": it.get("isbn") or "",
                        "aladin_item_id": str(it.get("itemId") or ""),
                        "title": it.get("title") or "",
                        "author": it.get("author") or "",
                        "publisher": it.get("publisher") or "",
                        "pub_date": it.get("pubDate") or "",
                        "category_id": it.get("categoryId") or cat_id,
                        "category_name": it.get("categoryName") or cat_label,
                        "price_standard": it.get("priceStandard") or 0,
                        "price_sales": it.get("priceSales") or 0,
                        "cover_url": cover_url,
                        "description": it.get("description") or "",
                        "active": True,
                        "source": "ALADIN",
                        "author_debut_year": None,
                        "author_past_books_count": 0,
                    })
                    if len(items) >= TARGET: break
                time.sleep(0.15)
            print(f"  {qtype} cat={cat_id} ({cat_label}): +{len(items)-before} (total {len(items)})")

    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {len(items)} books to {OUT}")
    print(f"Covers uploaded to s3://{bucket}/covers/")


if __name__ == "__main__":
    main()
