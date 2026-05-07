"""
BookFlow Offline Sim  ·  v2.1
───────────────────────────────────────────────────────────────────────────────
흐름
  1. 기동 시 inventory-api /books/active 호출 → ISBN + 가격 카탈로그 로드
  2. 루프마다 오프라인 매장 1곳 선택 → 해당 매장 재고 조회
  3. available > 0 인 ISBN들 중 랜덤으로 1-3건 거래 생성 → Kinesis PUT
  4. 배치 사이 30-90초 대기 (실제 POS 트랜잭션 패턴)

위치 구성
  수도권 STORE_OFFLINE: 3(강남), 4(홍대), 5(잠실), 6(신촌), 7(수원)  → wh_id=1
  영남   STORE_OFFLINE: 9(부산), 10(대구), 11(광주), 12(대전), 13(울산) → wh_id=2

Kinesis 스키마 (Glue raw_pos_mart 호환 + 확장 필드)
  tx_id, isbn13, qty, unit_price, total_price, channel, location_id, ts  ← 기존
  wh_id, discount, revenue, payment_method, title, category_name, store_name, api_verified ← v2.0
  customer_id, customer_segment, age_group, membership_grade,            ← v2.1
  customer_region, is_new_customer                                        ← v2.1 (사용자 기반 수요예측용)
  ※ 오프라인은 비회원(현금) 구매 40% → customer_id=None 익명 처리
"""
import json
import logging
import os
import random
import time
import uuid
from datetime import datetime, timezone

import boto3
import requests

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("offline-sim")

# ── 환경 변수 ─────────────────────────────────────────────────────────────────
STREAM_NAME       = os.environ.get("KINESIS_STREAM_NAME", "bookflow-pos-events")
REGION            = os.environ.get("AWS_REGION", "ap-northeast-1")
INVENTORY_API_URL = os.environ.get("INVENTORY_API_URL", "").rstrip("/")
ECS_CLUSTER_NAME  = os.environ.get("ECS_CLUSTER_NAME", "bookflow-ecs")
INTERVAL_SEC      = (int(os.environ.get("INTERVAL_MIN", "30")),
                     int(os.environ.get("INTERVAL_MAX", "90")))
CATALOG_TTL       = 600
STOCK_CACHE_TTL   = 45

# 오프라인 매장 마스터 (location_id → 메타)
OFFLINE_STORES = {
    3:  {"name": "강남점",  "wh_id": 1, "region": "서울 강남구",  "size": "L"},
    4:  {"name": "홍대점",  "wh_id": 1, "region": "서울 마포구",  "size": "M"},
    5:  {"name": "잠실점",  "wh_id": 1, "region": "서울 송파구",  "size": "M"},
    6:  {"name": "신촌점",  "wh_id": 1, "region": "서울 서대문구","size": "S"},
    7:  {"name": "수원점",  "wh_id": 1, "region": "경기 수원시",  "size": "S"},
    9:  {"name": "부산점",  "wh_id": 2, "region": "부산 해운대구","size": "L"},
    10: {"name": "대구점",  "wh_id": 2, "region": "대구 중구",    "size": "M"},
    11: {"name": "광주점",  "wh_id": 2, "region": "광주 동구",    "size": "M"},
    12: {"name": "대전점",  "wh_id": 2, "region": "대전 서구",    "size": "S"},
    13: {"name": "울산점",  "wh_id": 2, "region": "울산 남구",    "size": "S"},
}

# 매장 크기별 거래 건수 범위 (한 배치)
BATCH_QTY_RANGE = {"L": (3, 8), "M": (2, 5), "S": (1, 3)}

# 오프라인 특성: 교재/선물용 현금 비율 높음
PAYMENT_METHODS = ["CARD", "CARD", "CARD", "CARD", "CASH", "CASH", "MOBILE_PAY"]

# ── 사용자 프로필 마스터 (수요예측 확장성용) ──────────────────────────────────
# 오프라인은 비회원(현금) 구매자 40% → anonymous=True
_SEGMENTS = ["STUDENT", "PROFESSIONAL", "GENERAL", "SENIOR"]
_SEG_W    = [0.20, 0.30, 0.35, 0.15]   # 오프라인: 직장인·일반 비중 높음

_AGE_BY_SEG = {
    "STUDENT":      ["10s", "20s", "20s", "20s"],
    "PROFESSIONAL": ["20s", "30s", "30s", "40s"],
    "GENERAL":      ["20s", "30s", "40s", "50s+"],
    "SENIOR":       ["50s+", "50s+", "40s"],
}

_GRADES   = ["NONE", "BRONZE", "SILVER", "GOLD", "VIP"]
_GRADE_W  = [0.40,   0.28,    0.18,    0.09,  0.05]   # 오프라인: 멤버십 가입률 낮음

_REGIONS  = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "경남", "경북", "기타"]
_REGION_W = [0.28,  0.22,  0.07,  0.08,  0.05,  0.04,  0.04,  0.03,  0.05,  0.04,  0.10]


def make_user(anonymous: bool = False) -> dict:
    """사용자 프로필 생성 — 수요예측 피처 기초 데이터"""
    if anonymous:
        return {
            "customer_id":      None,
            "customer_segment": None,
            "age_group":        None,
            "membership_grade": "NONE",
            "customer_region":  None,
            "is_new_customer":  False,
        }
    segment = random.choices(_SEGMENTS, weights=_SEG_W)[0]
    return {
        "customer_id":      f"USR-{uuid.uuid4().hex[:8].upper()}",
        "customer_segment": segment,
        "age_group":        random.choice(_AGE_BY_SEG[segment]),
        "membership_grade": random.choices(_GRADES, weights=_GRADE_W)[0],
        "customer_region":  random.choices(_REGIONS, weights=_REGION_W)[0],
        "is_new_customer":  random.random() < 0.05,   # 신규 고객 5% (오프라인 재방문율 높음)
    }

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
kinesis          = boto3.client("kinesis", region_name=REGION)
_catalog: list   = []
_catalog_ts: float = 0
_stock_cache: dict = {}


# ── inventory-api URL 자동 탐색 ───────────────────────────────────────────────
def _discover_api_url() -> str:
    try:
        ecs = boto3.client("ecs", region_name=REGION)
        arns = ecs.list_tasks(
            cluster=ECS_CLUSTER_NAME,
            serviceName="inventory-api",
            desiredStatus="RUNNING",
        )["taskArns"]
        if not arns:
            log.warning("[discovery] inventory-api 태스크 없음")
            return ""
        tasks = ecs.describe_tasks(cluster=ECS_CLUSTER_NAME, tasks=arns[:1])["tasks"]
        ip = tasks[0]["containers"][0]["networkInterfaces"][0]["privateIpv4Address"]
        url = f"http://{ip}:8080"
        log.info("[discovery] inventory-api → %s", url)
        return url
    except Exception as e:
        log.warning("[discovery] 실패: %s", e)
        return ""


def get_api_url() -> str:
    global INVENTORY_API_URL
    if not INVENTORY_API_URL:
        INVENTORY_API_URL = _discover_api_url()
    return INVENTORY_API_URL


# ── 카탈로그 로드 ─────────────────────────────────────────────────────────────
def load_catalog() -> list:
    global _catalog, _catalog_ts
    url = get_api_url()
    if not url:
        return []
    try:
        resp = requests.get(f"{url}/books/active", timeout=15)
        resp.raise_for_status()
        books = resp.json()
        _catalog    = [b for b in books if b.get("price_sales", 0) > 0]
        _catalog_ts = time.time()
        log.info("[catalog] %d권 로드 완료", len(_catalog))
        return _catalog
    except Exception as e:
        log.error("[catalog] 로드 실패: %s", e)
        return _catalog


def get_catalog() -> list:
    if not _catalog or (time.time() - _catalog_ts) > CATALOG_TTL:
        load_catalog()
    return _catalog


# ── 재고 조회 (한 ISBN × 한 매장) ────────────────────────────────────────────
def get_stock(isbn13: str, location_id: int) -> dict | None:
    cache_key = (isbn13, location_id)
    cached_val, cached_ts = _stock_cache.get(cache_key, (None, 0))
    if cached_val is not None and (time.time() - cached_ts) < STOCK_CACHE_TTL:
        return cached_val

    url = get_api_url()
    if not url:
        return None
    try:
        resp = requests.get(
            f"{url}/stock",
            params={"isbn13": isbn13, "location_id": location_id},
            timeout=8,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        _stock_cache[cache_key] = (data, time.time())
        return data
    except Exception as e:
        log.warning("[stock] isbn=%s loc=%d 실패: %s", isbn13, location_id, e)
        global INVENTORY_API_URL
        INVENTORY_API_URL = ""
        return None


# ── 거래 레코드 생성 (오프라인 매장 특성 반영) ────────────────────────────────
def make_record(book: dict, stock: dict, location_id: int, store: dict) -> dict:
    max_qty    = min(5, stock["available"])
    qty        = random.randint(1, max(1, max_qty))
    unit_price = book["price_sales"]

    # 오프라인 매장: 10% 확률로 5~15% 할인 (문화상품권, 멤버십)
    if random.random() < 0.10:
        discount_rate = random.uniform(0.05, 0.15)
        discount      = int(unit_price * qty * discount_rate)
    else:
        discount = 0

    revenue  = unit_price * qty - discount
    payment  = random.choice(PAYMENT_METHODS)
    # 현금 결제 or 40% 확률 → 비회원 익명 처리 (실제 오프라인 비회원 구매 패턴)
    anonymous = (payment == "CASH") or (random.random() < 0.40)
    user      = make_user(anonymous=anonymous)

    return {
        # ── Glue raw_pos_mart 호환 필드 ──────────────────────────────────
        "tx_id":       str(uuid.uuid4()),
        "isbn13":      book["isbn13"],
        "qty":         qty,
        "unit_price":  unit_price,
        "total_price": unit_price * qty,    # 정가 기준 (Glue 집계용)
        "channel":     "OFFLINE",
        "location_id": location_id,
        "ts":          datetime.now(timezone.utc).isoformat(),
        # ── v2.0 확장 필드 ───────────────────────────────────────────────
        "wh_id":          store["wh_id"],
        "discount":       discount,
        "revenue":        revenue,          # 실수령액 (할인 후)
        "payment_method": payment,
        "store_name":     store["name"],
        "region":         store["region"],
        "title":          book["title"],
        "category_name":  book.get("category_name", ""),
        "api_verified":   True,
        # ── v2.1 사용자 필드 (수요예측 확장용) ───────────────────────────
        **user,
    }


# ── 배치 루프 (한 매장, 여러 거래) ───────────────────────────────────────────
def run_batch(catalog: list) -> int:
    """한 매장에서 복수 거래 생성. 성공한 거래 수 반환."""
    if not catalog:
        return 0

    location_id, store = random.choice(list(OFFLINE_STORES.items()))
    batch_min, batch_max = BATCH_QTY_RANGE[store["size"]]
    n_tx = random.randint(batch_min, batch_max)

    records_put = 0
    kinesis_batch = []

    candidates = random.sample(catalog, min(n_tx * 3, len(catalog)))  # 여유 있게 샘플
    for book in candidates:
        if records_put >= n_tx:
            break

        stock = get_stock(book["isbn13"], location_id)
        if stock is None or stock.get("available", 0) <= 0:
            continue

        rec = make_record(book, stock, location_id, store)
        kinesis_batch.append({
            "Data":         json.dumps(rec, ensure_ascii=False).encode(),
            "PartitionKey": book["isbn13"],
        })

        # 낙관적 캐시 업데이트
        cache_key = (book["isbn13"], location_id)
        if cache_key in _stock_cache:
            cached, ts = _stock_cache[cache_key]
            updated = {**cached, "available": max(0, cached["available"] - rec["qty"])}
            _stock_cache[cache_key] = (updated, ts)

        records_put += 1

    if kinesis_batch:
        kinesis.put_records(StreamName=STREAM_NAME, Records=kinesis_batch)
        log.info(
            "[batch] %s(%s) %d건 PUT · isbn 샘플=%s",
            store["name"], store["region"], len(kinesis_batch),
            kinesis_batch[0]["PartitionKey"] if kinesis_batch else "-",
        )

    return records_put


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("offline-sim v2.1 시작 · stream=%s", STREAM_NAME)

    for attempt in range(5):
        catalog = get_catalog()
        if catalog:
            break
        log.warning("카탈로그 로드 재시도 (%d/5)...", attempt + 1)
        time.sleep(10)

    if not catalog:
        log.error("카탈로그 로드 실패 — 종료")
        return

    log.info("루프 시작 · 배치 간격=%s-%s초", *INTERVAL_SEC)
    while True:
        try:
            run_batch(get_catalog())
        except Exception as e:
            log.error("[loop] 오류: %s", e)
        time.sleep(random.uniform(*INTERVAL_SEC))


if __name__ == "__main__":
    main()
