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
BQ_FORECAST_CSV = ROOT / "bq_forecast_base.csv"

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


def read_bq_forecast_base(path: Path) -> dict[tuple[str, int], float]:
    """bq_forecast_base.csv 로드 — GCP BQML champion 모델 실예측을 (isbn,store)별 5일 평균낸 것.
    BQ `forecast_results` 를 bq query 로 추출한 스냅샷. gen_forecast_cache 가 이 분포를
    seed 실 알라딘 책에 1:1 relabel 한다 (BQ 책 우주는 합성 데이터 · ISBN 정합은
    추후 GCP 가 실 ISBN 으로 재학습 시 해결). 반환: {(isbn13, store_id): base_demand}."""
    out: dict[tuple[str, int], float] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[(r["isbn13"].strip(), int(r["store_id"]))] = float(r["base_demand"])
    return out


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
def gen_inventory(
    books: list[dict],
    locations: list[dict],
    scenario_b_isbns: list[str],
    base_demand: dict[tuple[str, int], float],
    publisher_force_isbns: list[str] | None = None,
    wh_transfer_force_isbns: list[str] | None = None,
    wh_to_store_force_isbns: list[str] | None = None,
) -> list[dict]:
    """inventory 시드 — 2026-05-17 재설계 (안정 baseline + 격리된 데모 시나리오).

    safety_stock = base_demand × 5 (매장) · 권역 매장 base_demand 합 × 5 (WH).
    base_demand 는 forecast 와 동일 source → 안전재고 == 화면 5일치 항상 정합.

    baseline (비시나리오 책): on_hand = safety × 1.5~3 (매장) · ×2~4 (WH) → 부족 0 · 결품 0.
    데모 시나리오만 의도적 부족:
      - scenario_b 8책 SHORT_PAIRS 매장: 안전재고 미만 (부족 · 결품 아님)
      - publisher_force 책: 전 위치 on_hand 0 (cascade stage 0/1/2 fail → stage 3 PUBLISHER)
      - wh_transfer_force 책: 영남 권역(wh_id=2) 매장+WH 부족 · 수도권 WH 는 충분
        → REBALANCE/WH_TO_STORE fail → 수도권→영남 WH_TRANSFER 트리거
      - wh_to_store_force 책: 수도권(wh_id=1) 오프라인 매장 전부(loc 1~6) 부족 · 수도권 WH 본체는 충분
        → 같은 wh 매장 surplus 없어 REBALANCE fail → 자기 wh 본체→매장 WH_TO_STORE 트리거
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
    publisher_force_set = set(publisher_force_isbns or [])
    wh_transfer_force_set = set(wh_transfer_force_isbns or [])
    wh_to_store_force_set = set(wh_to_store_force_isbns or [])
    target_locs = [l for l in locations if not (l.get("is_virtual") in ("true", True))]
    # store_id → wh_id (WH safety = 권역 매장 base_demand 합 × 5)
    store_wh = {
        l["location_id"]: l.get("wh_id")
        for l in target_locs if l["location_type"] != "WH"
    }

    rows = []
    for b in books:
        isbn = b["isbn13"]
        short_locs = set(SHORT_PAIRS.get(isbn, []))
        is_publisher_force = isbn in publisher_force_set
        is_wh_transfer_force = isbn in wh_transfer_force_set
        is_wh_to_store_force = isbn in wh_to_store_force_set
        for l in target_locs:
            lid = l["location_id"]
            if l["location_type"] == "WH":
                wh_id = l.get("wh_id")
                wh_5d = sum(
                    base_demand.get((isbn, sid), 0.0)
                    for sid, w in store_wh.items() if w == wh_id
                ) * 5
                safety = max(int(wh_5d), 5)
                if is_publisher_force:
                    on_hand = 0                                                  # 양 WH 결품 → stage 3
                elif is_wh_transfer_force and wh_id == 2:
                    on_hand = 0                                                  # 영남 WH 결품 → WH_TO_STORE fail
                else:
                    on_hand = random.randint(safety * 2, safety * 4 + 1)         # 충분 (수도권 WH = WH_TO_STORE source)
            else:
                base = base_demand.get((isbn, lid), 1.0)
                safety = max(int(base * 5), 5)
                if is_publisher_force:
                    on_hand = 0                                                  # 전 매장 결품
                elif is_wh_transfer_force and l.get("wh_id") == 2:
                    on_hand = random.randint(1, max(2, int(safety * 0.25)))       # 영남 매장 부족 → REBALANCE fail
                elif is_wh_to_store_force and l.get("wh_id") == 1:
                    on_hand = random.randint(1, max(2, int(safety * 0.25)))       # 수도권 매장 전부 부족 → REBALANCE fail → WH_TO_STORE
                elif lid in short_locs:
                    on_hand = random.randint(1, max(2, int(safety * 0.25)))       # 의도 부족 (결품 X)
                else:
                    on_hand = random.randint(int(safety * 1.5), int(safety * 3) + 1)  # baseline 충분
            reserved = random.randint(0, max(0, on_hand // 10))
            rows.append({
                "isbn13": isbn,
                "location_id": lid,
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
def gen_forecast_cache(books, days: int = 7) -> tuple[list[dict], dict]:
    """forecast_cache · 7d rolling (D+0 ~ D+6) × 전 책 × 전 매장.

    2026-05-17 재설계 — base_demand 를 (책,매장) 별 1회 산출 → 매일 ±10% 노이즈만.
    날짜가 지나도 D+1 예측이 일관 → safety_stock(=base×5) 과 화면 5일치가 항상 ≈ 일치.
    (기존: 매일 독립 난수 → safety_stock 과 화면 forecast 가 무관한 값으로 어긋남.)

    2026-05-19 cascade 발주 폭증 버그 정정 — fixture 도서(publisher_force·wh_transfer·
    scenario_b SHORT_PAIRS)의 base_demand 인위 인플레이션(18~45권/day) 제거.
    인플레이션된 base_demand 가 safety_stock(=base×5) 으로 전파되어 cascade desired/gap 이
    BQ 실예측과 무관하게 부풀려졌다(WH safety ≈ 1000+ → /plan-daily 발주 ~2000).
    이제 전 도서·매장이 BQ 실예측 분포만 사용 → safety_stock 이 항상 예측과 정합.
    데모 시나리오의 cascade 트리거는 gen_inventory 의 on_hand 조작(publisher_force on_hand=0 ·
    wh_transfer/short_stores on_hand 부족)만으로 유지 — base_demand 와 독립.

    base_demand 분포:
      - 전 도서·매장 = GCP BQML champion 모델 실예측 분포 (bq_forecast_base.csv).
        seed 실 알라딘 책 ↔ BQ 책을 isbn 정렬 후 1:1 relabel — intermittent long-tail
        (실측: 80% ≲0.76권/day · 90분위 4.8 · 온라인/거점 고수요).

    반환: (forecast_rows, base_demand) — base_demand 는 gen_inventory 가 safety_stock 산출에 사용.
    """
    # BQ 실예측 분포를 seed 책에 1:1 relabel — seed 책 ↔ BQ 책 결정적 bijection
    # (양쪽 isbn13 정렬 후 zip · 1000↔1000). 추후 GCP 가 실 isbn 으로 재학습하면 자연 정합.
    bq_base = read_bq_forecast_base(BQ_FORECAST_CSV)
    bq_isbns = sorted({k[0] for k in bq_base})
    seed_isbns = sorted(b["isbn13"] for b in books)
    seed_to_bq = dict(zip(seed_isbns, bq_isbns))

    # base_demand[(isbn, store_id)] — (책,매장) 별 1회 산출 (날짜 무관 안정값)
    base_demand: dict[tuple[str, int], float] = {}
    for b in books:
        isbn = b["isbn13"]
        bq_isbn = seed_to_bq.get(isbn)
        for store_id in range(1, 15):
            # GCP BQML champion 모델 실예측 (매핑된 BQ 책의 해당 매장 5일 평균 수요)
            base_demand[(isbn, store_id)] = bq_base.get((bq_isbn, store_id), 0.0)

    # 7일치 (D+0 ~ D+6) — 매일 base × ±10% 노이즈만 (일관성 유지)
    rows: list[dict] = []
    for d in range(days):
        snap_date = (TODAY + timedelta(days=d)).isoformat()
        for b in books:
            isbn = b["isbn13"]
            for store_id in range(1, 15):
                demand = round(base_demand[(isbn, store_id)] * random.uniform(0.9, 1.1), 2)
                rows.append({
                    "snapshot_date":    snap_date,
                    "isbn13":           isbn,
                    "store_id":         store_id,
                    "predicted_demand": demand,
                    "confidence_low":   round(demand * 0.7, 2),
                    "confidence_high":  round(demand * 1.3, 2),
                    "model_version":    "automl-v1.0.0",
                    "synced_at":        NOW.isoformat(),
                })
    return rows, base_demand


def append_wh_forecast(forecast_rows: list[dict], locations: list[dict]) -> None:
    """WH row 추가 (in-place) — 자기 권역 매장 (오프라인+온라인) predicted_demand 합산.

    물류센터별 수요예측 = 권역 매장 수요의 sum (사용자 결정 2026-05-13).
    store_id = WH 의 location_id (15, 16) · model_version='demo-v1-wh-aggregate'.
    """
    wh_locs = [l for l in locations if l["location_type"] == "WH"]
    store_wh_map = {
        l["location_id"]: l.get("wh_id")
        for l in locations if l["location_type"] != "WH"
    }

    by_date: dict[str, list[dict]] = {}
    for r in forecast_rows:
        by_date.setdefault(r["snapshot_date"], []).append(r)

    new_rows: list[dict] = []
    for snap_date, rows in by_date.items():
        per_book_wh: dict[tuple[str, int], float] = {}
        for r in rows:
            wh = store_wh_map.get(r["store_id"])
            if wh is None:
                continue
            key = (r["isbn13"], wh)
            per_book_wh[key] = per_book_wh.get(key, 0.0) + float(r["predicted_demand"])

        for (isbn, wh), total in per_book_wh.items():
            wh_loc = next((w for w in wh_locs if w.get("wh_id") == wh), None)
            if wh_loc is None:
                continue
            new_rows.append({
                "snapshot_date":    snap_date,
                "isbn13":           isbn,
                "store_id":         wh_loc["location_id"],
                "predicted_demand": round(total, 2),
                "confidence_low":   round(total * 0.85, 2),
                "confidence_high":  round(total * 1.15, 2),
                "model_version":    "demo-v1-wh-aggregate",
                "synced_at":        NOW.isoformat(),
            })
    forecast_rows.extend(new_rows)


# =========================================================================
# 9. pending_orders (30 · 다양 상태 · order_type 다양)
# =========================================================================
# Stage 별 LEAD_DAYS — decision-svc/src/routes/decision.py 와 동일 (v5 2026-05-16).
# 사용자 도메인: D+0 새벽 예측 → 9시 승인 → 당일 실행.
#   REBALANCE/WH_TO_STORE/WH_TRANSFER 모두 D+0 (WH_TRANSFER 는 물류센터끼리 당일 분배).
#   PUBLISHER 만 D+3 (외부 발주 리드). chained WH_TO_STORE 는 상위 도착 +1일.
PO_LEAD_DAYS = {
    "REBALANCE":       0,
    "WH_TO_STORE":     0,
    "WH_TRANSFER":     0,
    "PUBLISHER_ORDER": 3,
}


def _po_row(order_type, isbn13, src, tgt, qty, urgency, status,
            auto_exec=False, hours_ago=12, reason="demand_growth",
            rejection_stage=None):
    """pending_orders row helper — 시나리오 fixture 의 row 생성 단순화.

    forecast_rationale.expected_arrival_date: created_at + LEAD_DAYS[order_type] (date).
    decision-svc /decide 와 /plan-daily 가 채우는 필드 정합.

    4-step state machine v2 (migration 006):
      PENDING → APPROVED → IN_TRANSIT → EXECUTED (또는 REJECTED + rejection_stage)
      신규 컬럼 5개 정합 채움 — CHECK 제약 충족 보장.

      | status         | approved_at | dispatched_at | executed_at | rejection_stage |
      | PENDING        | NULL        | NULL          | NULL        | NULL            |
      | APPROVED       | ✓           | NULL          | NULL        | NULL            |
      | IN_TRANSIT     | ✓           | ✓             | NULL        | NULL            |
      | EXECUTED       | ✓           | ✓             | ✓           | NULL            |
      | AUTO_EXECUTED  | ✓           | ✓             | ✓           | NULL            |
      | REJECTED       | stage 따라  | stage 따라    | NULL        | ✓ (PENDING|APPROVED|IN_TRANSIT) |
    """
    created = NOW - timedelta(hours=hours_ago)
    # expected_arrival_at: 완료 status (EXECUTED/AUTO_EXECUTED/REJECTED) 는 과거 일자에 cap.
    #   - 시드 시점 캘린더에 D-0 이상 ✅완료가 보이면 안 됨 (사용자 SoT 원칙).
    # PENDING/APPROVED/IN_TRANSIT 만 LEAD_DAYS 기반 미래 일자.
    if status in ("EXECUTED", "AUTO_EXECUTED"):
        expected_arrival_date = (created + timedelta(hours=3)).date()  # executed_at::date 와 동일
    elif status == "REJECTED":
        expected_arrival_date = created.date()
    else:
        expected_arrival_date = created.date() + timedelta(days=PO_LEAD_DAYS.get(order_type, 0))
    expected_arrival = expected_arrival_date.isoformat()

    # ── approved_at: APPROVED 이후 모든 status + REJECTED stage='APPROVED'|'IN_TRANSIT'
    has_approved = status in ("APPROVED", "IN_TRANSIT", "EXECUTED", "AUTO_EXECUTED") \
                   or (status == "REJECTED" and rejection_stage in ("APPROVED", "IN_TRANSIT"))
    approved_at = (created + timedelta(hours=1)).isoformat() if has_approved else ""

    # ── dispatched_at: IN_TRANSIT 진입 후 모든 status + REJECTED stage='IN_TRANSIT'
    has_dispatched = status in ("IN_TRANSIT", "EXECUTED", "AUTO_EXECUTED") \
                     or (status == "REJECTED" and rejection_stage == "IN_TRANSIT")
    dispatched_at = (created + timedelta(hours=2)).isoformat() if has_dispatched else ""
    dispatched_by = "seed:cron" if status == "AUTO_EXECUTED" else ("seed:user" if has_dispatched else "")

    # ── executed_at: EXECUTED 또는 AUTO_EXECUTED 만
    has_executed = status in ("EXECUTED", "AUTO_EXECUTED")
    executed_at = (created + timedelta(hours=3)).isoformat() if has_executed else ""
    executed_by = "seed:cron" if status == "AUTO_EXECUTED" else ("seed:user" if has_executed else "")

    return {
        "order_id": str(uuid.uuid4()),
        "order_type": order_type,
        "isbn13": isbn13,
        "source_location_id": src if src is not None else "",
        "target_location_id": tgt,
        "qty": qty,
        "est_lead_time_hours": 24 if order_type == "PUBLISHER_ORDER" else 6,
        "est_cost": qty * 15000 if order_type == "PUBLISHER_ORDER" else qty * 500,
        "forecast_rationale": json.dumps({
            "reason": reason,
            "ratio": 0.35,
            "expected_arrival_date": expected_arrival,
        }),
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
        # 4-step state machine v2 신규 컬럼 (migration 006)
        "dispatched_at": dispatched_at,
        "dispatched_by": dispatched_by,
        "executed_by": executed_by,
        "rejection_stage": rejection_stage if status == "REJECTED" else "",
        "expected_arrival_at": expected_arrival,
    }


def gen_pending_orders(books, locations, scenario_b_isbns) -> list[dict]:
    """시나리오 정합 fixture (random 폐기).

    A. 신간 추론 (PUBLISHER_ORDER · NEWBOOK · 4건 — 권역별 분배)
    B. 재고 부족 cascade (REBALANCE 3 + WH_TRANSFER 2 양측 + PUBLISHER 3) — scenario_b_isbns 8 도서 활용
    C. 권역 이동 양측 승인 (WH_TRANSFER 4건 — order_approvals 정합)
    """
    rows: list[dict] = []
    isbns = [b["isbn13"] for b in books]

    # 2026-05-15 v3 사용자 결정: 시드 시점 PENDING/APPROVED/IN_TRANSIT 모두 0.
    # 아래 시나리오 fixture row 들은 모두 D-1 이전 완료 (EXECUTED) 상태로 시드.
    # 시연 시 Plan 페이지의 [🎬 시연 발의] 버튼이 새 PENDING cascade 생성 → 양측 ✓ → APPROVED → dispatch → IN_TRANSIT → receive → EXECUTED 흐름.

    # ── A. 신간 추론 history (PUBLISHER_ORDER · 4건 · D-1~D-2 완료) ──
    wh_locs = [l["location_id"] for l in locations if l["location_type"] == "WH"]
    for i, (isbn, hours) in enumerate([
        (isbns[20], 30), (isbns[21], 28),
        (isbns[22], 32), (isbns[23], 26),
    ]):
        target_wh_loc = wh_locs[i % 2] if len(wh_locs) >= 2 else wh_locs[0]
        rows.append(_po_row("PUBLISHER_ORDER", isbn, None, target_wh_loc,
                            qty=80, urgency="NEWBOOK", status="EXECUTED",
                            auto_exec=False, hours_ago=hours, reason="new_book_distribution"))

    # ── B. 재고 부족 cascade history — 8 도서 모두 D-1 이전 완료 ──
    # B1. REBALANCE 3건 (수도권 매장 간 · 과거 처리됨)
    for i, (isbn, src, tgt) in enumerate([
        (scenario_b_isbns[0], 5, 1), (scenario_b_isbns[1], 6, 2), (scenario_b_isbns[2], 5, 3),
    ]):
        rows.append(_po_row("REBALANCE", isbn, src, tgt, qty=20,
                            urgency="NORMAL", status="EXECUTED",
                            hours_ago=30 + i * 4, reason="rebalance_low_stock"))

    # B2. WH_TRANSFER 2건 (수도권 ↔ 영남 WH · 과거 처리됨)
    wh1_loc = next((l["location_id"] for l in locations if l["location_type"] == "WH" and l["wh_id"] == 1), None)
    wh2_loc = next((l["location_id"] for l in locations if l["location_type"] == "WH" and l["wh_id"] == 2), None)
    if wh1_loc and wh2_loc:
        for i, (isbn, src, tgt) in enumerate([
            (scenario_b_isbns[3], wh1_loc, wh2_loc),
            (scenario_b_isbns[4], wh1_loc, wh2_loc),
        ]):
            rows.append(_po_row("WH_TRANSFER", isbn, src, tgt, qty=50,
                                urgency="NORMAL", status="EXECUTED",
                                hours_ago=36 + i * 2, reason="cross_region_balance"))

    # B3. PUBLISHER_ORDER 3건 (URGENT/CRITICAL 자동 실행 · 과거 AUTO_EXECUTED)
    if wh1_loc and wh2_loc:
        for i, (isbn, urg, tgt) in enumerate([
            (scenario_b_isbns[5], "URGENT",   wh1_loc),
            (scenario_b_isbns[6], "CRITICAL", wh2_loc),
            (scenario_b_isbns[7], "URGENT",   wh1_loc),
        ]):
            rows.append(_po_row("PUBLISHER_ORDER", isbn, None, tgt, qty=100,
                                urgency=urg, status="AUTO_EXECUTED",
                                auto_exec=True, hours_ago=40 + i, reason="forecast_shortage"))

    # ── C. 권역 이동 4건 (WH 본체 간 · 과거 EXECUTED) ──
    if wh1_loc and wh2_loc:
        for i, (isbn, src, tgt) in enumerate([
            (isbns[40], wh1_loc, wh2_loc),
            (isbns[41], wh1_loc, wh2_loc),
            (isbns[42], wh2_loc, wh1_loc),
            (isbns[43], wh2_loc, wh1_loc),
        ]):
            rows.append(_po_row("WH_TRANSFER", isbn, src, tgt, qty=30,
                                urgency="NORMAL", status="EXECUTED",
                                hours_ago=48 + i * 6, reason="capacity_balance"))

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

    분포 (2026-05-14 WH_TO_STORE 추가):
      - order_type: WH_TO_STORE 25% · REBALANCE 35% · WH_TRANSFER 20% · PUBLISHER_ORDER 20%
      - urgency: NORMAL 70% · URGENT 25% · CRITICAL 5%
    """
    rows: list[dict] = []
    isbns = [b["isbn13"] for b in books]
    store_ids = [l["location_id"] for l in locations if l["location_id"] <= 12]
    wh_groups = {1: [1, 2, 3, 4, 5, 6], 2: [7, 8, 9, 10, 11, 12]}
    # WH 본체 (location_type='WH') wh_id → location_id 매핑
    wh_body_by_id = {
        l["wh_id"]: l["location_id"]
        for l in locations
        if l["location_type"] == "WH" and l.get("wh_id") is not None
    }

    for d in range(days):
        day_offset = days - 1 - d   # day_offset=0 이 오늘 (D-0), days-1 이 가장 과거 (D-6)
        is_today = day_offset == 0
        # D-0 (오늘) skip — cascade 발의 버튼이 동적 생성 (decision-svc)
        if is_today:
            continue
        for i in range(per_day):
            order_type = random.choices(
                ["WH_TO_STORE", "REBALANCE", "WH_TRANSFER", "PUBLISHER_ORDER"],
                weights=[25, 35, 20, 20],
            )[0]
            urgency = random.choices(["NORMAL", "URGENT", "CRITICAL"], weights=[70, 25, 5])[0]
            auto_exec = urgency in ("URGENT", "CRITICAL")
            # 2026-05-15 v3 사용자 결정: 과거 (D-6~D-1) 모두 완료 상태만.
            #   D-0 / 미래 row 는 시드에서 생성 X · 시연 발의 시점에 채워짐.
            if auto_exec:
                # URGENT/CRITICAL — 07:00 cron 후 자연 흐름. AUTO_EXECUTED 또는 EXECUTED 만.
                status = random.choices(["AUTO_EXECUTED", "EXECUTED"], weights=[40, 60])[0]
            else:
                # NORMAL — 사람이 처리 끝. EXECUTED 또는 REJECTED 만.
                status = random.choices(["EXECUTED", "REJECTED"], weights=[55, 45])[0]
            # REJECTED 시 rejection_stage 분포: PENDING 거부 50% · APPROVED 거부 30% · IN_TRANSIT 거부 20%
            rejection_stage = (
                random.choices(["PENDING", "APPROVED", "IN_TRANSIT"], weights=[50, 30, 20])[0]
                if status == "REJECTED" else None
            )

            isbn = random.choice(isbns[50:500])
            qty = random.randint(10, 80)
            # created_at 을 .date() 기준 정확히 D-day_offset 로 떨어뜨림
            # (영업시간 09-17 KST · NOW 가 새벽이어도 .date() 일관)
            target_date = NOW.date() - timedelta(days=day_offset)
            target_dt = NOW.replace(year=target_date.year, month=target_date.month, day=target_date.day,
                                    hour=random.randint(9, 17), minute=random.randint(0, 59), second=0)
            hours_ago = max(1, int((NOW - target_dt).total_seconds() / 3600))

            if order_type == "WH_TO_STORE":
                # 자기 권역 wh 본체 → 자기 권역 매장 (2026-05-14 Stage 0)
                wh = random.choice([1, 2])
                src = wh_body_by_id.get(wh)
                if src is None:
                    continue
                tgt = random.choice(wh_groups[wh])
            elif order_type == "REBALANCE":
                wh = random.choice([1, 2])
                src, tgt = random.sample(wh_groups[wh], 2)
            elif order_type == "WH_TRANSFER":
                # V6.2 Notion: WH_TRANSFER 는 WH 본체 (location_type='WH') 간 이동만
                # wh_locs = [{wh1 location_id}, {wh2 location_id}] 형태로 함수 위에 정의 가정
                wh_locs = [l["location_id"] for l in locations if l["location_type"] == "WH"]
                if len(wh_locs) < 2:
                    continue
                if random.random() < 0.5:
                    src, tgt = wh_locs[0], wh_locs[1]
                else:
                    src, tgt = wh_locs[1], wh_locs[0]
            else:  # PUBLISHER_ORDER
                src = None
                # V6.2: 출판사 → WH 분배 (지점 직접 X)
                wh_locs = [l["location_id"] for l in locations if l["location_type"] == "WH"]
                tgt = random.choice(wh_locs) if wh_locs else random.choice(store_ids)

            rows.append(_po_row(
                order_type, isbn, src, tgt, qty,
                urgency=urgency, status=status,
                auto_exec=auto_exec, hours_ago=hours_ago,
                reason="daily_generated",
                rejection_stage=rejection_stage,
            ))
    return rows


# =========================================================================
# 2026-05-15 v3 사용자 결정: D-0/미래 row 는 시드에서 생성 X.
# 시연 시 Plan 페이지의 [🎬 시연 발의] 버튼 → decision-svc /plan-daily 호출 →
# PENDING cascade 4 stage 자동 생성 → 양측 ✓ → APPROVED → dispatch → IN_TRANSIT → receive → EXECUTED 흐름.
# 시드 시점에는 PENDING/APPROVED/IN_TRANSIT 모두 0. 과거 (D-1 이전) 만 완료 상태 row.


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
    # gen_inventory · gen_pending_orders 가 같은 list 사용 → 정합 보장.
    scenario_b_isbns = [b["isbn13"] for b in books[:8]]
    # PUBLISHER cascade 강제 — 2 도서 모든 location on_hand=0
    # → stage 0/1/2 fail → stage 3 PUBLISHER_ORDER 발의 자연스럽게 생성 (2026-05-17: 5→2 축소)
    # 2026-05-19: base_demand 인플레이션 제거 — 트리거는 on_hand=0 만으로 (BQ 실예측 기반 발주량).
    publisher_force_isbns = [b["isbn13"] for b in books[60:62]]
    # WH_TRANSFER cascade — 1 도서 영남 권역(매장+WH) 부족 · 수도권 WH 충분
    # → REBALANCE/WH_TO_STORE fail → 수도권→영남 WH_TRANSFER 발의 (2026-05-17 추가)
    wh_transfer_force_isbns = [b["isbn13"] for b in books[62:63]]
    # WH_TO_STORE cascade (stage 1) — 1 도서 수도권(wh_id=1) 매장 전부(loc 1~6) 부족 · 수도권 WH 본체 충분
    # → 같은 wh 매장 surplus 없어 REBALANCE fail → 자기 wh 본체→매장 WH_TO_STORE 발의 (2026-05-19 추가)
    wh_to_store_force_isbns = [books[63]["isbn13"]]

    # forecast 가 먼저 — base_demand 산출 → inventory.safety_stock = base_demand × 5
    forecast_cache, base_demand = gen_forecast_cache(books, days=7)
    # WH row 추가 (자기 권역 매장 합산 · 오프라인+온라인) — 사용자 결정 2026-05-13
    append_wh_forecast(forecast_cache, locations)
    inventory = gen_inventory(books, locations, scenario_b_isbns, base_demand,
                              publisher_force_isbns=publisher_force_isbns,
                              wh_transfer_force_isbns=wh_transfer_force_isbns,
                              wh_to_store_force_isbns=wh_to_store_force_isbns)
    reservations = gen_reservations(books, locations)
    # 시연 정합: D-1~D-6 처리완료 (600 row) · D-0 0 row (cascade 발의 버튼이 동적 생성)
    # D-7 ~ D-365 history (~17950 row) — 일자별 상세 history view 용.
    # 기존 scenario fixture (gen_pending_orders) 는 D-0 PENDING 포함이라 제외.
    from gen_history_365 import gen_pending_orders_history_365
    pending_recent  = gen_pending_orders_daily(books, locations, days=7, per_day=100)
    pending_history = gen_pending_orders_history_365(books, locations, per_day=50)
    pending_orders  = pending_recent + pending_history
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
