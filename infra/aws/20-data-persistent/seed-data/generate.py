"""BookFlow RDS seed CSV generator.

Reads existing parquet seeds (uhwk's GCP work) for books/authors/publishers/locations
and synthesizes the rest from those real ISBNs. Deterministic via seed=42.

Usage (from repo root):
    py infra/aws/20-data-persistent/seed-data/generate.py

Output: 17 CSVs in this directory (books, authors, publishers, warehouses, locations,
        users, inventory, reservations, pending_orders, order_approvals, returns,
        forecast_cache, new_book_requests, spike_events, notifications_log,
        sales_realtime, audit_log).
inventory_snapshot_daily and kpi_daily are derived at-deploy via SQL aggregation,
not seeded as CSV (too large to commit).
"""
import csv
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq

random.seed(42)

ROOT = Path(__file__).resolve().parent
PARQUET_DIR = ROOT.parent.parent.parent.parent / "scripts" / "output" / "historical"

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).replace(microsecond=0)
TODAY = NOW.date()


def write_csv(name: str, rows: list[dict]) -> None:
    if not rows:
        print(f"  skip {name} (empty)")
        return
    path = ROOT / f"{name}.csv"
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"  + {name}.csv ({len(rows)} rows)")


def parquet_to_dicts(path: Path) -> list[dict]:
    t = pq.read_table(path)
    return t.to_pylist()


# =========================================================================
# 1. publishers (extracted from books_seed) + 2. authors (split + meta)
# =========================================================================
def gen_books_authors_publishers() -> tuple[list[dict], list[dict], list[dict]]:
    src = parquet_to_dicts(PARQUET_DIR / "books_seed.parquet")

    # publishers: dedupe by name
    pub_map: dict[str, int] = {}
    publishers: list[dict] = []
    for row in src:
        name = row.get("publisher") or "(unknown)"
        if name not in pub_map:
            pub_map[name] = len(pub_map) + 1
            publishers.append({
                "publisher_id": pub_map[name],
                "name": name,
                "contact_email": f"contact@{('publisher' + str(pub_map[name]))}.kr",
            })

    # authors: split first author, dedupe
    author_map: dict[str, int] = {}
    authors: list[dict] = []
    for row in src:
        full = row.get("author") or "(unknown)"
        first_author = full.split(",")[0].strip().split(";")[0].strip() or "(unknown)"
        if first_author not in author_map:
            author_map[first_author] = len(author_map) + 1
            authors.append({
                "author_id": author_map[first_author],
                "name": first_author,
                "debut_year": row.get("author_debut_year") if first_author != "(unknown)" else None,
                "past_books_count": row.get("author_past_books_count") or 0,
            })

    # books: project to 23 columns matching books table
    books: list[dict] = []
    for row in src:
        full_author = row.get("author") or ""
        first_author = full_author.split(",")[0].strip().split(";")[0].strip() or None
        books.append({
            "isbn13": row["isbn13"],
            "isbn10": "",
            "aladin_item_id": "",
            "title": (row.get("title") or "")[:500],
            "author": first_author,
            "publisher": row.get("publisher"),
            "pub_date": row.get("pub_date") or "",
            "category_id": row.get("category_id"),
            "category_name": (row.get("category_name") or "")[:200],
            "price_standard": row.get("price_standard"),
            "price_sales": row.get("price_sales"),
            "cover_url": row.get("cover_url") or "",
            "description": (row.get("description") or "")[:1000],
            "active": "true" if row.get("active") else "false",
            "discontinue_mode": "NONE",
            "discontinue_reason": "",
            "discontinue_at": "",
            "discontinue_by": "",
            "reactivated_at": "",
            "expected_soldout_at": "",
            "source": row.get("source") or "ALADIN",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        })
    return books, authors, publishers


# =========================================================================
# 3. warehouses + 4. locations
# =========================================================================
def gen_warehouses_locations() -> tuple[list[dict], list[dict]]:
    warehouses = [
        {"wh_id": 1, "name": "Sudogwon WH", "region": "Sudogwon",  "capacity": 50000},
        {"wh_id": 2, "name": "Yeongnam WH",  "region": "Yeongnam",   "capacity": 40000},
    ]
    locs_src = parquet_to_dicts(PARQUET_DIR / "locations_seed.parquet")
    locations = [
        {
            "location_id":  l["location_id"],
            "location_type": l["location_type"],
            "wh_id":        l["wh_id"],
            "name":         l.get("name") or "",
            "size":         l.get("size") or "",
            "region":       l.get("region") or "",
            "is_virtual":   "true" if l.get("is_virtual") else "false",
            "active":       "true" if l.get("active") else "false",
        }
        for l in locs_src
    ]
    return warehouses, locations


# =========================================================================
# 5. users (17: 1 hq-admin + 2 wh-manager + 14 branch-clerk)
# =========================================================================
def gen_users(locations: list[dict]) -> list[dict]:
    users = []
    # 1 hq-admin
    users.append({
        "user_id": str(uuid.UUID("00000000-0000-0000-0000-000000000001")),
        "email": "hq-admin@bookflow.local",
        "display_name": "HQ Admin",
        "role": "hq-admin",
        "scope_wh_id": "",
        "scope_store_id": "",
        "last_login_at": (NOW - timedelta(hours=2)).isoformat(),
    })
    # 2 wh-manager (1 per WH)
    for wh in (1, 2):
        users.append({
            "user_id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"wh-manager-{wh}")),
            "email": f"wh{wh}-manager@bookflow.local",
            "display_name": f"WH{wh} Manager",
            "role": "wh-manager",
            "scope_wh_id": wh,
            "scope_store_id": "",
            "last_login_at": (NOW - timedelta(hours=4)).isoformat(),
        })
    # 14 branch-clerk (1 per non-WH location)
    for l in locations:
        if l["location_type"] == "WH":
            continue
        users.append({
            "user_id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"branch-{l['location_id']}")),
            "email": f"branch{l['location_id']}@bookflow.local",
            "display_name": f"Branch {l['location_id']} Clerk",
            "role": "branch-clerk",
            "scope_wh_id": l["wh_id"],
            "scope_store_id": l["location_id"],
            "last_login_at": (NOW - timedelta(hours=random.randint(1, 24))).isoformat(),
        })
    return users


# =========================================================================
# 6. inventory (book x non-virtual location); virtual STORE_ONLINE row 미생성
#    → v_online_store_available view 가 WH 재고 참조
# =========================================================================
def gen_inventory(books: list[dict], locations: list[dict]) -> list[dict]:
    rows = []
    target_locs = [l for l in locations if not (l.get("is_virtual") in ("true", True))]
    for b in books:
        for l in target_locs:
            if l["location_type"] == "WH":
                on_hand = random.randint(50, 500)
                safety = 30
            else:
                on_hand = random.randint(0, 30)
                safety = 5
            reserved = random.randint(0, max(0, on_hand // 10))
            rows.append({
                "isbn13": b["isbn13"],
                "location_id": l["location_id"],
                "on_hand": on_hand,
                "reserved_qty": reserved,
                "safety_stock": safety,
                "updated_at": (NOW - timedelta(minutes=random.randint(1, 600))).isoformat(),
                "updated_by": "seed-script",
            })
    return rows


# =========================================================================
# 7. reservations (10 rows · ACTIVE/COMMITTED/EXPIRED 다양)
# =========================================================================
def gen_reservations(books, locations) -> list[dict]:
    rows = []
    pickable_locs = [l for l in locations if l["location_type"] != "WH" and not (l.get("is_virtual") in ("true", True))]
    reasons = ["NORMAL", "SALE_ORDER", "SPIKE_URGENT", "SOLDOUT_BATCH"]
    statuses = ["ACTIVE"] * 6 + ["COMMITTED"] * 3 + ["EXPIRED"]
    for i in range(10):
        b = random.choice(books)
        l = random.choice(pickable_locs)
        rows.append({
            "reservation_id": str(uuid.uuid4()),
            "isbn13": b["isbn13"],
            "location_id": l["location_id"],
            "qty": random.randint(1, 5),
            "reason": random.choice(reasons),
            "status": statuses[i],
            "ttl": (NOW + timedelta(minutes=random.randint(5, 60))).isoformat(),
            "created_by": f"order-{i+1}",
            "created_at": (NOW - timedelta(minutes=random.randint(1, 120))).isoformat(),
        })
    return rows


# =========================================================================
# 8. forecast_cache (D+1 only · book × store, store_id 1~12)
# =========================================================================
def gen_forecast_cache(books) -> list[dict]:
    rows = []
    target_date = TODAY + timedelta(days=1)
    snap_date = TODAY
    for b in books[:1000]:
        for store_id in range(1, 13):
            base = random.randint(1, 30)
            rows.append({
                "snapshot_date": snap_date.isoformat(),
                "isbn13": b["isbn13"],
                "store_id": store_id,
                "predicted_demand": round(base * random.uniform(0.8, 1.2), 2),
                "confidence_low":   round(base * 0.7, 2),
                "confidence_high":  round(base * 1.3, 2),
                "model_version":    "automl-v1.0.0",
                "synced_at": NOW.isoformat(),
            })
    return rows[:1000]   # plan: 1000 row


# =========================================================================
# 9. pending_orders (30 · 다양 상태 · order_type 다양)
# =========================================================================
def gen_pending_orders(books, locations) -> list[dict]:
    rows = []
    types = ["REBALANCE", "WH_TRANSFER", "PUBLISHER_ORDER", "MANUAL"]
    statuses = ["PENDING"] * 10 + ["APPROVED"] * 12 + ["REJECTED"] * 5 + ["AUTO_EXECUTED"] * 3
    urgencies = ["NORMAL"] * 22 + ["URGENT_SOLDOUT"] * 5 + ["URGENT_SPIKE"] * 3
    target_locs = [l for l in locations if not (l.get("is_virtual") in ("true", True))]
    for i in range(30):
        b = random.choice(books)
        src = random.choice(target_locs)
        tgt = random.choice([l for l in target_locs if l["location_id"] != src["location_id"]])
        status = statuses[i]
        urgency = urgencies[i]
        created = NOW - timedelta(hours=random.randint(1, 72))
        approved_at = ""
        executed_at = ""
        if status in ("APPROVED", "AUTO_EXECUTED"):
            approved_at = (created + timedelta(hours=1)).isoformat()
        if status == "AUTO_EXECUTED":
            executed_at = (created + timedelta(hours=2)).isoformat()
        rows.append({
            "order_id": str(uuid.uuid4()),
            "order_type": random.choice(types),
            "isbn13": b["isbn13"],
            "source_location_id": src["location_id"] if random.random() > 0.2 else "",
            "target_location_id": tgt["location_id"],
            "qty": random.randint(5, 100),
            "est_lead_time_hours": random.choice([6, 12, 24, 48, 72]),
            "est_cost": random.randint(50000, 300000),
            "forecast_rationale": json.dumps({"reason": "demand_growth", "ratio": round(random.uniform(0.1, 0.5), 2)}),
            "urgency_level": urgency,
            "auto_execute_eligible": "true" if urgency != "NORMAL" else "false",
            "stock_days_remaining": round(random.uniform(0.5, 5.0), 2),
            "demand_confidence_ratio": round(random.uniform(0.1, 0.5), 2),
            "demand_cv": round(random.uniform(0.1, 0.5), 2),
            "status": status,
            "execution_reason": "AUTO_CRON_URGENT" if status == "AUTO_EXECUTED" else "",
            "reject_reason": "WRONG_QUANTITY" if status == "REJECTED" else "",
            "reject_count": random.randint(0, 2) if status == "REJECTED" else 0,
            "created_at": created.isoformat(),
            "approved_at": approved_at,
            "executed_at": executed_at,
        })
    return rows


# =========================================================================
# 10. order_approvals (per APPROVED/REJECTED order · 1-2 rows)
# =========================================================================
def gen_order_approvals(pending_orders) -> list[dict]:
    rows = []
    for o in pending_orders:
        if o["status"] not in ("APPROVED", "REJECTED", "AUTO_EXECUTED"):
            continue
        # WH_TRANSFER => 2 sides, else 1 SINGLE
        sides = ["SOURCE", "TARGET"] if o["order_type"] == "WH_TRANSFER" else ["SINGLE"]
        for side in sides:
            decision = "APPROVED" if o["status"] in ("APPROVED", "AUTO_EXECUTED") else "REJECTED"
            rows.append({
                "approval_id": str(uuid.uuid4()),
                "order_id": o["order_id"],
                "approver_id": str(uuid.uuid5(uuid.NAMESPACE_OID, f"approver-{side}")),
                "approver_role": "wh-manager" if side != "SINGLE" else "hq-admin",
                "approver_wh_id": random.choice([1, 2]),
                "approval_side": side,
                "decision": decision,
                "reject_reason": o["reject_reason"] if decision == "REJECTED" else "",
                "decided_at": o["approved_at"] or NOW.isoformat(),
            })
    return rows


# =========================================================================
# 11. returns
# =========================================================================
def gen_returns(books, locations) -> list[dict]:
    rows = []
    statuses = ["PENDING"] * 8 + ["APPROVED"] * 7 + ["EXECUTED"] * 4 + ["REJECTED"]
    reasons = ["CUSTOMER", "DAMAGED", "SOFT_DISCONTINUE_END", "LONG_TAIL"]
    target_locs = [l for l in locations if not (l.get("is_virtual") in ("true", True))]
    for i in range(20):
        b = random.choice(books)
        l = random.choice(target_locs)
        s = statuses[i]
        requested_at = NOW - timedelta(days=random.randint(1, 14))
        rows.append({
            "return_id": str(uuid.uuid4()),
            "isbn13": b["isbn13"],
            "location_id": l["location_id"],
            "qty": random.randint(1, 10),
            "reason": random.choice(reasons),
            "status": s,
            "requested_at": requested_at.isoformat(),
            "hq_approved_at": (requested_at + timedelta(days=1)).isoformat() if s in ("APPROVED", "EXECUTED") else "",
            "executed_at":    (requested_at + timedelta(days=3)).isoformat() if s == "EXECUTED" else "",
        })
    return rows


# =========================================================================
# 12. new_book_requests
# =========================================================================
def gen_new_book_requests(publishers) -> list[dict]:
    rows = []
    statuses = ["NEW"] * 5 + ["FETCHED"] * 5 + ["FORECASTED"] * 3 + ["APPROVED"] * 5 + ["REJECTED"] * 2
    for i in range(20):
        p = random.choice(publishers)
        created = NOW - timedelta(days=random.randint(1, 30))
        s = statuses[i]
        rows.append({
            "id": i + 1,
            "publisher_id": str(p["publisher_id"]),
            "isbn13": "979" + "".join(random.choices("0123456789", k=10)),
            "title": f"Forthcoming title {i+1}",
            "author": "TBD",
            "genre": random.choice(["fiction", "nonfiction", "self-help", "economics", "tech"]),
            "expected_pub_date": (TODAY + timedelta(days=random.randint(7, 60))).isoformat(),
            "estimated_initial_sales": random.randint(500, 5000),
            "marketing_plan": "Standard launch + SNS push.",
            "similar_books": json.dumps([]),
            "target_segments": json.dumps(["20s-female", "metro"]),
            "status": s,
            "created_at": created.isoformat(),
            "fetched_at":  (created + timedelta(hours=1)).isoformat() if s != "NEW" else "",
            "approved_at": (created + timedelta(days=1)).isoformat() if s in ("APPROVED", "REJECTED") else "",
        })
    return rows


# =========================================================================
# 13. spike_events
# =========================================================================
def gen_spike_events(books) -> list[dict]:
    rows = []
    for i in range(10):
        b = random.choice(books)
        detected = NOW - timedelta(hours=random.randint(1, 48))
        resolved = (detected + timedelta(hours=random.randint(2, 24))).isoformat() if i % 3 == 0 else ""
        rows.append({
            "event_id": str(uuid.uuid4()),
            "detected_at": detected.isoformat(),
            "isbn13": b["isbn13"],
            "z_score": round(random.uniform(3.0, 8.0), 2),
            "mentions_count": random.randint(50, 500),
            "triggered_order_id": "",
            "resolved_at": resolved,
        })
    return rows


# =========================================================================
# 14. notifications_log
# =========================================================================
def gen_notifications_log() -> list[dict]:
    rows = []
    event_types = ["OrderPending", "OrderApproved", "OrderRejected", "AutoExecutedUrgent",
                   "AutoRejectedBatch", "SpikeUrgent", "StockDepartPending", "StockArrivalPending",
                   "NewBookRequest", "ReturnPending", "LambdaAlarm", "DeploymentRollback"]
    severities = {
        "SpikeUrgent": "CRITICAL", "AutoExecutedUrgent": "WARN", "OrderPending": "WARN",
        "LambdaAlarm": "ERROR", "DeploymentRollback": "WARN",
    }
    for i in range(50):
        et = random.choice(event_types)
        sent = NOW - timedelta(hours=random.randint(1, 168))
        rows.append({
            "notification_id": str(uuid.uuid4()),
            "event_type": et,
            "correlation_id": str(uuid.uuid4()),
            "severity": severities.get(et, "INFO"),
            "recipients": json.dumps({"roles": ["hq-admin"]}),
            "channels": "teams,email",
            "payload_summary": json.dumps({"summary": f"event-{i+1}"}),
            "sent_at": sent.isoformat(),
            "status": "SENT" if i % 10 != 0 else "RETRYING",
        })
    return rows


# =========================================================================
# 15. sales_realtime (14 days · ~3000 events/day = ~42k rows)
#     books_seed.parquet 의 sales_point 을 weight 로 사용
# =========================================================================
def gen_sales_realtime(books, locations) -> list[dict]:
    rows = []
    online_locs = [l for l in locations if l["location_type"] == "STORE_ONLINE"]
    offline_locs = [l for l in locations if l["location_type"] == "STORE_OFFLINE"]
    # 14 days, ~3000/day
    for day_offset in range(14):
        day = NOW - timedelta(days=day_offset)
        is_weekend = day.weekday() >= 5
        n = 4500 if is_weekend else 3000
        for _ in range(n):
            b = random.choice(books)
            is_online = random.random() < 0.4
            l = random.choice(online_locs if is_online else offline_locs)
            qty = random.randint(1, 3)
            unit_price = b.get("price_sales") or 15000
            try:
                unit_price = int(unit_price)
            except (TypeError, ValueError):
                unit_price = 15000
            discount = int(unit_price * random.uniform(0, 0.1)) * qty
            revenue = unit_price * qty - discount
            event_ts = day.replace(
                hour=random.randint(8, 22),
                minute=random.randint(0, 59),
                second=random.randint(0, 59),
            )
            rows.append({
                "txn_id": str(uuid.uuid4()),
                "event_ts": event_ts.isoformat(),
                "store_id": l["location_id"],
                "wh_id":    l["wh_id"],
                "channel":  "online" if is_online else "offline",
                "isbn13":   b["isbn13"],
                "qty":      qty,
                "unit_price": unit_price,
                "discount": discount,
                "revenue":  revenue,
                "payment_method": random.choice(["CARD", "MOBILE", "CASH"]),
                "created_at": event_ts.isoformat(),
            })
    return rows


# =========================================================================
# 16. audit_log (200)
# =========================================================================
def gen_audit_log() -> list[dict]:
    actor_types = ["USER", "SYSTEM", "CRONJOB", "LAMBDA"]
    actions = ["APPROVE", "REJECT", "AUTO_ORDER", "ADJUST", "RESERVE", "CANCEL", "INSERT", "UPDATE"]
    entities = ["pending_orders", "inventory", "returns", "books", "users"]
    rows = []
    for i in range(200):
        ts = NOW - timedelta(minutes=random.randint(1, 60 * 24 * 14))
        rows.append({
            "log_id": i + 1,
            "ts": ts.isoformat(),
            "actor_type": random.choice(actor_types),
            "actor_id":   f"actor-{random.randint(1, 20)}",
            "action":     random.choice(actions),
            "entity_type": random.choice(entities),
            "entity_id":  str(uuid.uuid4()),
            "before_state": json.dumps({"v": 1}),
            "after_state":  json.dumps({"v": 2}),
            "source_ip": f"10.0.{random.randint(1,255)}.{random.randint(1,255)}",
            "request_id": str(uuid.uuid4()),
        })
    return rows


def main() -> None:
    print(f"BookFlow seed-data generator (deterministic, seed=42)")
    print(f"  reading parquet from: {PARQUET_DIR}")
    print(f"  output dir:           {ROOT}")
    print()

    books, authors, publishers = gen_books_authors_publishers()
    warehouses, locations = gen_warehouses_locations()
    users = gen_users(locations)
    inventory = gen_inventory(books, locations)
    reservations = gen_reservations(books, locations)
    forecast_cache = gen_forecast_cache(books)
    pending_orders = gen_pending_orders(books, locations)
    order_approvals = gen_order_approvals(pending_orders)
    returns_ = gen_returns(books, locations)
    new_book_requests = gen_new_book_requests(publishers)
    spike_events = gen_spike_events(books)
    notifications_log = gen_notifications_log()
    sales_realtime = gen_sales_realtime(books, locations)
    audit_log = gen_audit_log()

    write_csv("publishers",         publishers)
    write_csv("authors",            authors)
    write_csv("books",              books)
    write_csv("warehouses",         warehouses)
    write_csv("locations",          locations)
    write_csv("users",              users)
    write_csv("inventory",          inventory)
    write_csv("reservations",       reservations)
    write_csv("forecast_cache",     forecast_cache)
    write_csv("pending_orders",     pending_orders)
    write_csv("order_approvals",    order_approvals)
    write_csv("returns",            returns_)
    write_csv("new_book_requests",  new_book_requests)
    write_csv("spike_events",       spike_events)
    write_csv("notifications_log",  notifications_log)
    write_csv("sales_realtime",     sales_realtime)
    write_csv("audit_log",          audit_log)

    print()
    print("done")


if __name__ == "__main__":
    main()
