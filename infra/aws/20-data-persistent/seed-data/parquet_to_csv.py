"""parquet → CSV 변환 (V3 schema 매핑 컬럼만)

소스: BOOKFLOW/01_데이터스키마/*.parquet (민지 ETL 산출 · 2년 시계열)
출력: seed-data/parquet_csv/*.csv (RDS COPY 용 · UTF-8)

매핑:
  books_seed.parquet (1000) → books.csv (V3 books 컬럼)
  locations_seed.parquet (14) → locations.csv (V3 locations)
  inventory_daily_2y.parquet (290만, 최근 90일 cut) → inventory_snapshot_daily.csv
  sales_fact_2y.parquet (76만, 365일 GROUP BY 집계) → kpi_daily.csv
  sales_fact_2y.parquet (76만, 최근 14일 raw 확장) → sales_realtime.csv

실행: py infra/aws/20-data-persistent/seed-data/parquet_to_csv.py
"""
import csv
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

SCHEMA_DIR = Path(r"C:\Users\User\Desktop\kyobo project\BOOKFLOW\01_데이터스키마")
OUT_DIR = Path(__file__).parent / "parquet_csv"
OUT_DIR.mkdir(exist_ok=True)

TODAY = date(2026, 5, 6)
SNAPSHOT_CUT = TODAY - timedelta(days=90)
SALES_CUT = TODAY - timedelta(days=14)


def _shift_date(d, target_today=TODAY):
    """parquet 의 sale_date (2024-2026) → 우리 target_today 기준 최근으로 shift."""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    return d


def convert_books():
    src = SCHEMA_DIR / "books_seed.parquet"
    t = pq.read_table(src).to_pylist()
    out = OUT_DIR / "books.csv"
    cols = ["isbn13", "title", "author", "publisher", "pub_date", "category_id",
            "category_name", "price_standard", "price_sales", "cover_url",
            "description", "active", "source"]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in t:
            w.writerow([
                r.get("isbn13"),
                (r.get("title") or "")[:500],
                (r.get("author") or "")[:200],
                (r.get("publisher") or "")[:100],
                r.get("pub_date"),
                r.get("category_id"),
                (r.get("category_name") or "")[:200],
                r.get("price_standard"),
                r.get("price_sales"),
                (r.get("cover_url") or "")[:500],
                (r.get("description") or "")[:1000],
                "t" if r.get("active") else "f",
                (r.get("source") or "ALADIN")[:20],
            ])
    print(f"  books.csv: {len(t)} rows")
    return [r["isbn13"] for r in t]


def convert_locations():
    src = SCHEMA_DIR / "locations_seed.parquet"
    t = pq.read_table(src).to_pylist()
    out = OUT_DIR / "locations.csv"
    cols = ["location_id", "location_type", "wh_id", "name", "size", "region", "is_virtual", "active"]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in t:
            w.writerow([
                r["location_id"],
                r.get("location_type", "STORE"),
                r.get("wh_id"),
                (r.get("name") or "")[:100],
                r.get("size", "M"),
                (r.get("region") or "")[:100],
                "t" if r.get("is_virtual") else "f",
                "t" if r.get("active", True) else "f",
            ])
    print(f"  locations.csv: {len(t)} rows")


def convert_inventory_snapshot():
    src = SCHEMA_DIR / "inventory_daily_2y.parquet"
    out = OUT_DIR / "inventory_snapshot_daily.csv"
    cols = ["snapshot_date", "isbn13", "location_id", "on_hand", "reserved_qty", "available", "safety_stock"]
    cnt = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        # streaming read (290만 row 메모리 부담)
        pf = pq.ParquetFile(src)
        for batch in pf.iter_batches(batch_size=50_000):
            for r in batch.to_pylist():
                d = r["snapshot_date"]
                if isinstance(d, str):
                    d = datetime.strptime(d, "%Y-%m-%d").date()
                # 90일 cut
                if d < SNAPSHOT_CUT:
                    continue
                on_hand = r["on_hand"] or 0
                rsv = r["reserved_qty"] or 0
                w.writerow([d.isoformat(), r["isbn13"], r["location_id"], on_hand, rsv,
                           max(0, on_hand - rsv), r.get("safety_stock")])
                cnt += 1
    print(f"  inventory_snapshot_daily.csv: {cnt} rows (90d cut)")


def convert_sales_aggregated_to_kpi():
    """sales_fact_2y → kpi_daily (GROUP BY sale_date+store+channel · 365일)"""
    src = SCHEMA_DIR / "sales_fact_2y.parquet"
    pf = pq.ParquetFile(src)
    cut = TODAY - timedelta(days=365)
    agg = {}  # (date, store, channel) → {qty, revenue, tx, isbns}
    for batch in pf.iter_batches(batch_size=50_000):
        for r in batch.to_pylist():
            d = r["sale_date"]
            if isinstance(d, str):
                d = datetime.strptime(d, "%Y-%m-%d").date()
            if d < cut:
                continue
            key = (d, r["store_id"], r.get("channel", "offline"))
            a = agg.setdefault(key, {"qty": 0, "rev": 0, "tx": 0, "isbns": set(), "top": None, "top_qty": 0})
            a["qty"] += r.get("qty_sold", 0) or 0
            a["rev"] += r.get("revenue", 0) or 0
            a["tx"] += r.get("tx_count", 1) or 1
            a["isbns"].add(r["isbn13"])
            if (r.get("qty_sold", 0) or 0) > a["top_qty"]:
                a["top"] = r["isbn13"]
                a["top_qty"] = r.get("qty_sold", 0) or 0
    out = OUT_DIR / "kpi_daily.csv"
    cols = ["kpi_date", "store_id", "category_id", "channel", "qty_sold", "revenue",
            "tx_count", "avg_price", "unique_isbn_count", "top_isbn"]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for (d, store, ch), a in agg.items():
            avg = int(a["rev"] // a["qty"]) if a["qty"] else None
            w.writerow([d.isoformat(), int(store), 0, ch, int(a["qty"]), int(a["rev"]), int(a["tx"]),
                       avg, len(a["isbns"]), a["top"]])
    print(f"  kpi_daily.csv: {len(agg)} rows (365d agg)")


def convert_sales_realtime():
    """sales_fact_2y 최근 14일 → sales_realtime (V3 schema 정확 컬럼)"""
    src = SCHEMA_DIR / "sales_fact_2y.parquet"
    pf = pq.ParquetFile(src)
    out = OUT_DIR / "sales_realtime.csv"
    # V3 sales_realtime 컬럼: txn_id / event_ts / store_id / wh_id / channel / isbn13 / qty / unit_price / discount / revenue / payment_method
    cols = ["txn_id", "event_ts", "store_id", "wh_id", "channel", "isbn13",
            "qty", "unit_price", "discount", "revenue", "payment_method"]
    cnt = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for batch in pf.iter_batches(batch_size=50_000):
            for r in batch.to_pylist():
                d = r["sale_date"]
                if isinstance(d, str):
                    d = datetime.strptime(d, "%Y-%m-%d").date()
                if d < SALES_CUT:
                    continue
                qty = r.get("qty_sold", 0) or 0
                if qty <= 0:
                    continue
                rev = r.get("revenue", 0) or 0
                avg = r.get("avg_price") or (rev // qty if qty else 0)
                w.writerow([
                    str(uuid.uuid4()),
                    f"{d.isoformat()} 12:00:00+09",
                    int(r["store_id"]),
                    int(r.get("wh_id") or 1),
                    r.get("channel", "offline"),
                    r["isbn13"],
                    int(qty),
                    int(avg),
                    0,  # discount
                    int(rev),
                    "",  # payment_method (V3 nullable)
                ])
                cnt += 1
    print(f"  sales_realtime.csv: {cnt} rows (14d raw)")


if __name__ == "__main__":
    print(f"OUT_DIR: {OUT_DIR}")
    print(f"TODAY: {TODAY}, SNAPSHOT_CUT (90d): {SNAPSHOT_CUT}, SALES_CUT (14d): {SALES_CUT}")
    print()
    convert_books()
    convert_locations()
    convert_inventory_snapshot()
    convert_sales_aggregated_to_kpi()
    convert_sales_realtime()
    print("\nDone. 다음:")
    print("  1. aws s3 sync parquet_csv/ s3://bookflow-cp-artifacts-354493396671/seed/")
    print("  2. SSM RunCommand 로 ansible-node → RDS COPY")
