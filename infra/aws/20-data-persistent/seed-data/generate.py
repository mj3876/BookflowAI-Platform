"""BookFlow RDS seed CSV generator.

Reads existing parquet seeds (uhwk's GCP work) for books/authors/publishers/locations
and synthesizes the rest from those real ISBNs. Deterministic via seed=42.

Usage (from repo root):
    py infra/aws/20-data-persistent/seed-data/generate.py

Output: 17 CSVs in this directory (books, authors, publishers, warehouses, locations,
        users, inventory, reservations, pending_orders, order_approvals, returns,
        forecast_cache, new_book_requests, spike_events, notifications_log,
        sales_realtime, audit_log).
kpi_daily is seeded with 30 days × 13 stores (전사+10 오프라인+2 온라인) for demo charts;
운영에선 매일 03:30 KST kpi-sync CronJob 이 BQ kpi_daily_view 에서 MERGE.
inventory_snapshot_daily 는 너무 커서 (14일 × 12000 = 168k) 시드 제외 · CronJob 이 채움.
"""
import csv
import json
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parent
ALADIN_JSON = ROOT / "books_aladin.json"

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST).replace(microsecond=0)
# 시연 시점 = 오늘 18:00 KST 가정 (NOW 가 새벽이라도 .date() 단위로 fixture/daily 가 일관)
NOW = NOW.replace(hour=18, minute=0, second=0)
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


def read_aladin_json(path: Path) -> list[dict]:
    """books_aladin.json 읽기 (fetch_aladin.py 결과). 1000개 dict 반환."""
    return json.loads(path.read_text(encoding="utf-8"))


# =========================================================================
# 1. publishers (extracted from books_seed) + 2. authors (split + meta)
# =========================================================================
def gen_books_authors_publishers() -> tuple[list[dict], list[dict], list[dict]]:
    src = read_aladin_json(ALADIN_JSON)

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
        {"wh_id": 1, "name": "수도권 거점창고", "region": "수도권", "capacity": 50000},
        {"wh_id": 2, "name": "영남 거점창고",   "region": "영남",   "capacity": 40000},
    ]
    # 14 locations · 오프라인 12 (수도권 6 + 영남 6) + 온라인 가상 2 (한국어 매장명)
    SUDO_STORES = [("강남점", "L"), ("광화문점", "L"), ("잠실점", "M"),
                   ("홍대점", "M"), ("신촌점", "S"), ("용산점", "S")]
    YEONG_STORES = [("부산 서면점", "L"), ("대구 동성점", "L"),
                    ("울산 삼산점", "M"), ("대구 교대점", "M"),
                    ("부산 센텀점", "S"), ("포항 양덕점", "S")]
    locations: list[dict] = []
    lid = 1
    for name, size in SUDO_STORES:
        locations.append({"location_id": lid, "location_type": "STORE_OFFLINE", "wh_id": 1,
                          "name": name, "size": size, "region": "수도권",
                          "is_virtual": "false", "active": "true"})
        lid += 1
    for name, size in YEONG_STORES:
        locations.append({"location_id": lid, "location_type": "STORE_OFFLINE", "wh_id": 2,
                          "name": name, "size": size, "region": "영남",
                          "is_virtual": "false", "active": "true"})
        lid += 1
    # 온라인 가상 (각 WH 1 개씩)
    locations.append({"location_id": lid, "location_type": "STORE_ONLINE", "wh_id": 1,
                      "name": "수도권 온라인", "size": "L", "region": "수도권",
                      "is_virtual": "true", "active": "true"})
    lid += 1
    locations.append({"location_id": lid, "location_type": "STORE_ONLINE", "wh_id": 2,
                      "name": "영남 온라인", "size": "L", "region": "영남",
                      "is_virtual": "true", "active": "true"})
    lid += 1
    # WH 본체 (거점 창고 자체) 2 row — 권역 거점 inventory 저장 (출판사 입고 → WH → 매장 분배)
    locations.append({"location_id": lid, "location_type": "WH", "wh_id": 1,
                      "name": "수도권 거점창고", "size": "XL", "region": "수도권",
                      "is_virtual": "false", "active": "true"})
    lid += 1
    locations.append({"location_id": lid, "location_type": "WH", "wh_id": 2,
                      "name": "영남 거점창고", "size": "XL", "region": "영남",
                      "is_virtual": "false", "active": "true"})
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
def gen_inventory(books: list[dict], locations: list[dict], scenario_b_isbns: list[str]) -> list[dict]:
    """inventory 시드 + 시나리오 B 8 도서 의도적 부족 (cascade 시연용).

    SHORT_PAIRS = pending_orders 의 시나리오 B fixture 와 정합하는 매장 부족 매핑.
    """
    SHORT_PAIRS: dict[str, list[int]] = {
        scenario_b_isbns[0]: [1, 2],   # 강남·광화문 부족 → REBALANCE 3건의 source/target
        scenario_b_isbns[1]: [2, 3],
        scenario_b_isbns[2]: [3, 4],
        scenario_b_isbns[3]: [7],      # 부산 서면 부족 → WH_TRANSFER 수신
        scenario_b_isbns[4]: [9],      # 울산 삼산 부족
        scenario_b_isbns[5]: [1],      # 강남 부족 → PUBLISHER URGENT
        scenario_b_isbns[6]: [7],      # 부산 서면 부족 → PUBLISHER CRITICAL
        scenario_b_isbns[7]: [2],      # 광화문 부족 → PUBLISHER URGENT
    }
    rows = []
    target_locs = [l for l in locations if not (l.get("is_virtual") in ("true", True))]
    for b in books:
        isbn = b["isbn13"]
        short_locs = SHORT_PAIRS.get(isbn, [])
        for l in target_locs:
            if l["location_type"] == "WH":
                on_hand = random.randint(50, 500)
                safety = 30
            elif l["location_id"] in short_locs:
                # 시나리오 B 의도 부족 — safety_stock 미만 (cascade 시연용)
                on_hand = random.randint(0, 2)
                safety = 5
            else:
                on_hand = random.randint(0, 30)
                safety = 5
            reserved = random.randint(0, max(0, on_hand // 10))
            rows.append({
                "isbn13": isbn,
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
def gen_forecast_cache(books, scenario_b_isbns: list[str], days: int = 7) -> list[dict]:
    """forecast_cache · 7d rolling (D+0 ~ D+6) × 약 1000 row/day = 7000 row.

    각 day 마다:
      - 시나리오 B fixture 8 도서 (SHORT_PAIRS) 의도적 high predicted_demand
      - 일반 random fill (books[:~85] × 12 store ≈ 1000 row)

    PK (snapshot_date, isbn13, store_id) seen check 로 중복 방지.
    """
    SHORT_PAIRS: dict[str, list[int]] = {
        scenario_b_isbns[0]: [1, 2],
        scenario_b_isbns[1]: [2, 3],
        scenario_b_isbns[2]: [3, 4],
        scenario_b_isbns[3]: [7],
        scenario_b_isbns[4]: [9],
        scenario_b_isbns[5]: [1],
        scenario_b_isbns[6]: [7],
        scenario_b_isbns[7]: [2],
    }
    rows: list[dict] = []
    seen: set[tuple[str, str, int]] = set()

    for d in range(days):
        snap_date = TODAY + timedelta(days=d)
        day_rows = 0
        target_per_day = 1000

        # 시나리오 B fixture 먼저 (모든 day 동일 패턴)
        for isbn, locs in SHORT_PAIRS.items():
            for store_id in locs:
                key = (snap_date.isoformat(), isbn, store_id)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "snapshot_date": snap_date.isoformat(),
                    "isbn13": isbn,
                    "store_id": store_id,
                    "predicted_demand": round(random.uniform(50, 80), 2),
                    "confidence_low":   40.0,
                    "confidence_high":  100.0,
                    "model_version":    "automl-v1.0.0",
                    "synced_at": NOW.isoformat(),
                })
                day_rows += 1

        # 일반 random fill — day 별 target_per_day 까지
        for b in books[:200]:
            if day_rows >= target_per_day:
                break
            for store_id in range(1, 13):
                if day_rows >= target_per_day:
                    break
                key = (snap_date.isoformat(), b["isbn13"], store_id)
                if key in seen:
                    continue
                seen.add(key)
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
                day_rows += 1
    return rows


# =========================================================================
# 9. pending_orders (30 · 다양 상태 · order_type 다양)
# =========================================================================
def _po_row(order_type, isbn13, src, tgt, qty, urgency, status,
            auto_exec=False, hours_ago=12, reason="demand_growth"):
    """pending_orders row helper — 시나리오 fixture 의 row 생성 단순화."""
    created = NOW - timedelta(hours=hours_ago)
    approved_at = (created + timedelta(hours=1)).isoformat() if status in ("APPROVED", "AUTO_EXECUTED", "EXECUTED") else ""
    executed_at = (created + timedelta(hours=2)).isoformat() if status in ("AUTO_EXECUTED", "EXECUTED") else ""
    return {
        "order_id": str(uuid.uuid4()),
        "order_type": order_type,
        "isbn13": isbn13,
        "source_location_id": src if src is not None else "",
        "target_location_id": tgt,
        "qty": qty,
        "est_lead_time_hours": 24 if order_type == "PUBLISHER_ORDER" else 6,
        "est_cost": qty * 15000 if order_type == "PUBLISHER_ORDER" else qty * 500,
        "forecast_rationale": json.dumps({"reason": reason, "ratio": 0.35}),
        "urgency_level": urgency,
        "auto_execute_eligible": "true" if auto_exec else "false",
        "stock_days_remaining": 1.5 if urgency in ("URGENT", "CRITICAL") else 4.0,
        "demand_confidence_ratio": 0.42,
        "demand_cv": 0.28,
        "status": status,
        "execution_reason": "AUTO_CRON_URGENT" if status == "AUTO_EXECUTED" else "",
        "reject_reason": "재고 부족" if status == "REJECTED" else "",
        "reject_count": 1 if status == "REJECTED" else 0,
        "created_at": created.isoformat(),
        "approved_at": approved_at,
        "executed_at": executed_at,
    }


def gen_pending_orders(books, locations, scenario_b_isbns) -> list[dict]:
    """시나리오 정합 fixture (random 폐기).

    A. 신간 추론 (PUBLISHER_ORDER · NEWBOOK · 4건 — 권역별 분배)
    B. 재고 부족 cascade (REBALANCE 3 + WH_TRANSFER 2 양측 + PUBLISHER 3) — scenario_b_isbns 8 도서 활용
    C. 권역 이동 양측 승인 (WH_TRANSFER 4건 — order_approvals 정합)
    """
    rows: list[dict] = []
    isbns = [b["isbn13"] for b in books]

    # ── A. 신간 추론 — PUBLISHER_ORDER + urgency=NEWBOOK (출판사 신간 신청 결정) ──
    # 4건: 2 권역 × 2 status (APPROVED / PENDING)
    for i, (isbn, status, hours) in enumerate([
        (isbns[20], "APPROVED", 30), (isbns[21], "APPROVED", 28),  # WH-1 & WH-2 분배
        (isbns[22], "PENDING",  10), (isbns[23], "PENDING",  6),
    ]):
        target_wh_loc = 1 if i % 2 == 0 else 7  # WH 인근 매장 (강남점 / 부산서면점)
        rows.append(_po_row("PUBLISHER_ORDER", isbn, None, target_wh_loc,
                            qty=80, urgency="NEWBOOK", status=status,
                            auto_exec=False, hours_ago=hours, reason="new_book_distribution"))

    # ── B. 재고 부족 cascade — scenario_b_isbns 8 도서 시드 의도 부족 ──
    # B1. REBALANCE 3건 (수도권 매장 간 · source 5,6→target 1,2,3 — 같은 권역)
    for i, (isbn, src, tgt) in enumerate([
        (scenario_b_isbns[0], 5, 1), (scenario_b_isbns[1], 6, 2), (scenario_b_isbns[2], 5, 3),
    ]):
        rows.append(_po_row("REBALANCE", isbn, src, tgt, qty=20,
                            urgency="NORMAL", status="PENDING" if i < 2 else "APPROVED",
                            hours_ago=8 + i * 4, reason="rebalance_low_stock"))

    # B2. WH_TRANSFER 2건 (수도권 → 영남 양측 — 양 wh 매장)
    for i, (isbn, src, tgt) in enumerate([
        (scenario_b_isbns[3], 1, 7),  # 강남점 → 부산 서면점
        (scenario_b_isbns[4], 4, 9),  # 홍대점 → 울산 삼산점
    ]):
        rows.append(_po_row("WH_TRANSFER", isbn, src, tgt, qty=50,
                            urgency="NORMAL", status="PENDING",
                            hours_ago=4 + i * 2, reason="cross_region_balance"))

    # B3. PUBLISHER_ORDER 3건 (URGENT/CRITICAL · auto_execute_eligible)
    for i, (isbn, urg, tgt) in enumerate([
        (scenario_b_isbns[5], "URGENT",   1),  # WH 인근 강남점
        (scenario_b_isbns[6], "CRITICAL", 7),  # 부산 서면점
        (scenario_b_isbns[7], "URGENT",   2),  # 광화문점
    ]):
        rows.append(_po_row("PUBLISHER_ORDER", isbn, None, tgt, qty=100,
                            urgency=urg, status="PENDING",
                            auto_exec=True, hours_ago=2 + i, reason="forecast_shortage"))

    # ── C. 권역 이동 4건 — 시연 정합으로 모두 PENDING 시작 ──
    for i, (isbn, src, tgt) in enumerate([
        (isbns[40], 2, 8),   # 광화문 → 대구 동성
        (isbns[41], 3, 11),  # 잠실 → 부산 센텀
        (isbns[42], 7, 1),   # 부산 서면 → 강남
        (isbns[43], 10, 4),  # 대구 교대 → 홍대
    ]):
        rows.append(_po_row("WH_TRANSFER", isbn, src, tgt, qty=30,
                            urgency="NORMAL", status="PENDING",
                            hours_ago=12 + i * 6, reason="capacity_balance"))

    return rows


# =========================================================================
# 9b. pending_orders daily-generated (시연 fixture + 운영 mimic)
# =========================================================================
def gen_pending_orders_daily(books, locations, days: int = 7, per_day: int = 100) -> list[dict]:
    """시연 정합 (2026-05-12 v2):
      - D-1 ~ D-6 (과거): 모두 batch 처리완료 (APPROVED / REJECTED / AUTO_EXECUTED) → 일자별 기록
      - D-0 (오늘): 0 row — 사용자가 'BQ cascade 발의' 버튼 누르면 decision-svc 가 PENDING 생성
    """
    """매일 ~100건 daily-generated · batch 시각성 반영.

    하루 batch 흐름:
      - 03:30 KST decision-cascade 가 모든 PENDING 발의
      - 07:00 KST intervention-auto-execute 가 URGENT/CRITICAL + auto_execute_eligible 자동 승인
      - 18:00 KST intervention-auto-reject 가 NORMAL 미처리 D-1 일괄 거절

    시점 별 status 분포:
      - D-6 ~ D-1 (과거): 모두 batch 처리 완료
          · URGENT/CRITICAL → AUTO_EXECUTED (07:00 batch)
          · NORMAL → REJECTED (18:00 batch) 또는 일부 APPROVED (사람이 처리 ~20%)
      - D-0 (오늘): batch 일부 진행 (시연 시점 09:00+ 가정)
          · URGENT/CRITICAL → APPROVED (07:00 batch 완료)
          · NORMAL → PENDING (대부분 · 사용자가 처리할 ~14건) + 일부 APPROVED (사람이 이미 ~5건)

    분포:
      - order_type: REBALANCE 50% · WH_TRANSFER 30% · PUBLISHER_ORDER 20%
      - urgency: NORMAL 70% · URGENT 25% · CRITICAL 5%
    """
    rows: list[dict] = []
    isbns = [b["isbn13"] for b in books]
    store_ids = [l["location_id"] for l in locations if l["location_id"] <= 12]
    wh_groups = {1: [1, 2, 3, 4, 5, 6], 2: [7, 8, 9, 10, 11, 12]}

    for d in range(days):
        day_offset = days - 1 - d   # day_offset=0 이 오늘 (D-0), days-1 이 가장 과거 (D-6)
        is_today = day_offset == 0
        # D-0 (오늘) skip — cascade 발의 버튼이 동적 생성 (decision-svc)
        if is_today:
            continue
        for i in range(per_day):
            order_type = random.choices(
                ["REBALANCE", "WH_TRANSFER", "PUBLISHER_ORDER"],
                weights=[50, 30, 20],
            )[0]
            urgency = random.choices(["NORMAL", "URGENT", "CRITICAL"], weights=[70, 25, 5])[0]
            auto_exec = urgency in ("URGENT", "CRITICAL")
            # 과거 batch 처리 완료 분포
            if auto_exec:
                status = "AUTO_EXECUTED"   # 07:00 batch
            else:
                status = random.choices(["APPROVED", "REJECTED"], weights=[55, 45])[0]

            isbn = random.choice(isbns[50:500])
            qty = random.randint(10, 80)
            # created_at 을 .date() 기준 정확히 D-day_offset 로 떨어뜨림
            # (영업시간 09-17 KST · NOW 가 새벽이어도 .date() 일관)
            target_date = NOW.date() - timedelta(days=day_offset)
            target_dt = NOW.replace(year=target_date.year, month=target_date.month, day=target_date.day,
                                    hour=random.randint(9, 17), minute=random.randint(0, 59), second=0)
            hours_ago = max(1, int((NOW - target_dt).total_seconds() / 3600))

            if order_type == "REBALANCE":
                wh = random.choice([1, 2])
                src, tgt = random.sample(wh_groups[wh], 2)
            elif order_type == "WH_TRANSFER":
                if random.random() < 0.5:
                    src = random.choice(wh_groups[1]); tgt = random.choice(wh_groups[2])
                else:
                    src = random.choice(wh_groups[2]); tgt = random.choice(wh_groups[1])
            else:  # PUBLISHER_ORDER
                src = None
                tgt = random.choice(store_ids)

            rows.append(_po_row(
                order_type, isbn, src, tgt, qty,
                urgency=urgency, status=status,
                auto_exec=auto_exec, hours_ago=hours_ago,
                reason="daily_generated",
            ))
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
            # Guard: 미래 시각 cap (today row 의 random hour 8~22 가 현재 시각보다 미래일 때
            # ORDER BY event_ts DESC LIMIT 12 가 seed 미래 row 만 표시 → sim 새 INSERT 가
            # 화면에 안 보이는 버그 방지)
            if event_ts > NOW:
                event_ts = NOW - timedelta(seconds=random.randint(60, 3600))
            rows.append({
                "txn_id": str(uuid.uuid4()),
                "event_ts": event_ts.isoformat(),
                "store_id": l["location_id"],
                "wh_id":    l["wh_id"],
                # ECS sim 정합 — sim 은 'ONLINE_APP'/'ONLINE_WEB'/'OFFLINE' (대문자) emit.
                # backend SQL (master.py) 가 `channel LIKE 'ONLINE%'` / `= 'OFFLINE'` 으로 정확 match.
                "channel":  ("ONLINE_APP" if random.random() < 0.7 else "ONLINE_WEB") if is_online else "OFFLINE",
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


def gen_kpi_daily(books: list[dict], locations: list[dict]) -> list[dict]:
    """30일치 일별 KPI · store_id=0 (전사) + 실 매장 12개 = 13 stores.
    channel='ALL' · category_id=0 (전체) 단일 분면. 데모 차트용 합리적 수치 분포.
    """
    rows = []
    isbns = [b["isbn13"] for b in books]
    store_ids = [0] + [loc["location_id"] for loc in locations]   # 0=전사
    for d in range(30, 0, -1):
        kpi_date = (TODAY - timedelta(days=d)).isoformat()
        per_store: list[dict] = []
        for sid in store_ids:
            if sid == 0:
                continue   # 전사 row 는 매장 합으로 마지막에 만듦
            qty = random.randint(80, 380)
            avg_price = random.randint(13000, 22000)
            revenue = qty * avg_price + random.randint(-50000, 50000)
            tx_count = random.randint(40, 180)
            unique = random.randint(20, 120)
            row = {
                "kpi_date":          kpi_date,
                "store_id":          sid,
                "category_id":       0,
                "channel":           "ALL",
                "qty_sold":          qty,
                "revenue":           revenue,
                "tx_count":          tx_count,
                "avg_price":         avg_price,
                "unique_isbn_count": unique,
                "top_isbn":          random.choice(isbns),
                "synced_from_bq_at": NOW.isoformat(),
            }
            per_store.append(row)
            rows.append(row)
        # 전사 합산 row (store_id=0)
        rows.append({
            "kpi_date":          kpi_date,
            "store_id":          0,
            "category_id":       0,
            "channel":           "ALL",
            "qty_sold":          sum(r["qty_sold"] for r in per_store),
            "revenue":           sum(r["revenue"] for r in per_store),
            "tx_count":          sum(r["tx_count"] for r in per_store),
            "avg_price":         sum(r["revenue"] for r in per_store) // max(sum(r["qty_sold"] for r in per_store), 1),
            "unique_isbn_count": min(1000, sum(r["unique_isbn_count"] for r in per_store)),
            "top_isbn":          random.choice(isbns),
            "synced_from_bq_at": NOW.isoformat(),
        })
    return rows


def main() -> None:
    print(f"BookFlow seed-data generator (deterministic, seed=42)")
    print(f"  aladin source: {ALADIN_JSON.name}")
    print(f"  output dir:    {ROOT}")
    print()

    books, authors, publishers = gen_books_authors_publishers()
    warehouses, locations = gen_warehouses_locations()
    users = gen_users(locations)

    # 시나리오 B (재고 부족 cascade) 의 8 도서 — books 앞쪽 인덱스에서 stable 추출.
    # gen_inventory · gen_forecast_cache · gen_pending_orders 모두 같은 list 사용 → 정합 보장.
    scenario_b_isbns = [b["isbn13"] for b in books[:8]]

    inventory = gen_inventory(books, locations, scenario_b_isbns)
    reservations = gen_reservations(books, locations)
    forecast_cache = gen_forecast_cache(books, scenario_b_isbns, days=7)
    # 시연 정합: D-1~D-6 처리완료 (600 row) · D-0 0 row (cascade 발의 버튼이 동적 생성)
    # 기존 scenario fixture (gen_pending_orders) 는 D-0 PENDING 포함이라 제외.
    pending_orders = gen_pending_orders_daily(books, locations, days=7, per_day=100)
    order_approvals = gen_order_approvals(pending_orders)
    returns_ = gen_returns(books, locations)
    new_book_requests = gen_new_book_requests(publishers)
    spike_events = gen_spike_events(books)
    notifications_log = gen_notifications_log()
    sales_realtime = gen_sales_realtime(books, locations)
    audit_log = gen_audit_log()
    kpi_daily = gen_kpi_daily(books, locations)

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
    write_csv("kpi_daily",          kpi_daily)

    print()
    print("done")


if __name__ == "__main__":
    main()
