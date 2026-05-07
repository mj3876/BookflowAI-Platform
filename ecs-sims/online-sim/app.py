"""
BookFlow Online Sim  ·  v2.1
───────────────────────────────────────────────────────────────────────────────
흐름
  1. 기동 시 inventory-api /books/active 호출 → ISBN + 가격 카탈로그 로드
  2. 루프마다 랜덤 ISBN 선택 → /stock?location_id=WH 재고 확인
  3. available > 0 이면 거래 레코드 생성 → Kinesis PUT
  4. 10분마다 카탈로그 & 재고 캐시 갱신

Kinesis 스키마 (Glue raw_pos_mart 호환 + 확장 필드)
  tx_id, isbn13, qty, unit_price, total_price, channel, location_id, ts  ← 기존
  wh_id, revenue, payment_method, title, category_name, api_verified     ← v2.0
  customer_id, customer_segment, age_group, membership_grade,            ← v2.1
  customer_region, is_new_customer                                        ← v2.1 (사용자 기반 수요예측용)
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
log = logging.getLogger("online-sim")

# ── 환경 변수 ─────────────────────────────────────────────────────────────────
STREAM_NAME          = os.environ.get("KINESIS_STREAM_NAME", "bookflow-pos-events")
REGION               = os.environ.get("AWS_REGION", "ap-northeast-1")
INVENTORY_API_URL    = os.environ.get("INVENTORY_API_URL", "").rstrip("/")
ECS_CLUSTER_NAME     = os.environ.get("ECS_CLUSTER_NAME", "bookflow-ecs")
INTERVAL_SEC         = (int(os.environ.get("INTERVAL_MIN", "10")),
                        int(os.environ.get("INTERVAL_MAX", "30")))
CATALOG_TTL          = 600   # 카탈로그 갱신 주기 (초)
STOCK_CACHE_TTL      = 30    # 재고 캐시 TTL (초)

# 온라인 가상 스토어 → 실제 조회할 WH location_id 매핑
ONLINE_LOCATIONS = {
    8:  {"wh_location_id": 1, "wh_id": 1, "name": "WH1온라인(수도권)"},
    14: {"wh_location_id": 2, "wh_id": 2, "name": "WH2온라인(영남)"},
}
PAYMENT_METHODS = ["CARD", "CARD", "CARD", "MOBILE_PAY", "MOBILE_PAY", "POINT", "TRANSFER"]

# ── 사용자 프로필 마스터 (수요예측 확장성용) ──────────────────────────────────
# segment × 가중치: 온라인은 학생·직장인 비중 높음
_SEGMENTS = ["STUDENT", "PROFESSIONAL", "GENERAL", "SENIOR"]
_SEG_W    = [0.32, 0.28, 0.30, 0.10]

# segment별 연령대 분포 (수요예측 시 세그먼트-연령 조인 가능)
_AGE_BY_SEG = {
    "STUDENT":      ["10s", "20s", "20s", "20s"],
    "PROFESSIONAL": ["20s", "30s", "30s", "40s"],
    "GENERAL":      ["20s", "30s", "40s", "50s+"],
    "SENIOR":       ["50s+", "50s+", "40s"],
}

# 멤버십 등급 가중치: 온라인 구매자 멤버십 가입률 높음
_GRADES   = ["NONE", "BRONZE", "SILVER", "GOLD", "VIP"]
_GRADE_W  = [0.15,   0.30,    0.28,    0.17,  0.10]

# 거주 지역 분포 (실제 e-커머스 배송지 분포 참고)
_REGIONS  = ["서울", "경기", "인천", "부산", "대구", "광주", "대전", "울산", "경남", "경북", "기타"]
_REGION_W = [0.28,  0.22,  0.07,  0.08,  0.05,  0.04,  0.04,  0.03,  0.05,  0.04,  0.10]


def make_user(anonymous: bool = False) -> dict:
    """사용자 프로필 생성 — 수요예측 피처 기초 데이터"""
    if anonymous:
        return {
            "customer_id":       None,
            "customer_segment":  None,
            "age_group":         None,
            "membership_grade":  "NONE",
            "customer_region":   None,
            "is_new_customer":   False,
        }
    segment = random.choices(_SEGMENTS, weights=_SEG_W)[0]
    return {
        "customer_id":      f"USR-{uuid.uuid4().hex[:8].upper()}",
        "customer_segment": segment,
        "age_group":        random.choice(_AGE_BY_SEG[segment]),
        "membership_grade": random.choices(_GRADES, weights=_GRADE_W)[0],
        "customer_region":  random.choices(_REGIONS, weights=_REGION_W)[0],
        "is_new_customer":  random.random() < 0.08,   # 신규 고객 8%
    }

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
kinesis          = boto3.client("kinesis", region_name=REGION)
_catalog: list   = []
_catalog_ts: float = 0
_stock_cache: dict = {}   # (isbn13, location_id) → (available, ts)


# ── inventory-api URL 자동 탐색 ───────────────────────────────────────────────
def _discover_api_url() -> str:
    """ECS 태스크 IP를 동적으로 탐색 (INVENTORY_API_URL 미설정 시)"""
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
        log.error("inventory-api URL을 알 수 없습니다 — 빈 카탈로그 사용")
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
        return _catalog  # 이전 캐시 유지


def get_catalog() -> list:
    if not _catalog or (time.time() - _catalog_ts) > CATALOG_TTL:
        load_catalog()
    return _catalog


# ── 재고 조회 ─────────────────────────────────────────────────────────────────
def get_stock(isbn13: str, wh_location_id: int) -> dict | None:
    cache_key = (isbn13, wh_location_id)
    cached_val, cached_ts = _stock_cache.get(cache_key, (None, 0))
    if cached_val is not None and (time.time() - cached_ts) < STOCK_CACHE_TTL:
        return cached_val

    url = get_api_url()
    if not url:
        return None
    try:
        resp = requests.get(
            f"{url}/stock",
            params={"isbn13": isbn13, "location_id": wh_location_id},
            timeout=8,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        _stock_cache[cache_key] = (data, time.time())
        return data
    except Exception as e:
        log.warning("[stock] isbn=%s loc=%d 조회 실패: %s", isbn13, wh_location_id, e)
        # URL 재탐색 트리거
        global INVENTORY_API_URL
        INVENTORY_API_URL = ""
        return None


# ── 거래 레코드 생성 ──────────────────────────────────────────────────────────
def make_record(book: dict, stock: dict, online_location_id: int, wh_id: int) -> dict:
    max_qty    = min(3, stock["available"])
    qty        = random.randint(1, max(1, max_qty))
    unit_price = book["price_sales"]
    total      = qty * unit_price
    channel    = random.choices(["ONLINE_APP", "ONLINE_WEB"], weights=[70, 30])[0]
    payment    = random.choice(PAYMENT_METHODS)
    user       = make_user(anonymous=False)   # 온라인 = 항상 로그인 사용자

    return {
        # ── Glue raw_pos_mart 호환 필드 ──────────────────────────────────
        "tx_id":       str(uuid.uuid4()),
        "isbn13":      book["isbn13"],
        "qty":         qty,
        "unit_price":  unit_price,
        "total_price": total,
        "channel":     channel,
        "location_id": online_location_id,
        "ts":          datetime.now(timezone.utc).isoformat(),
        # ── v2.0 확장 필드 ───────────────────────────────────────────────
        "wh_id":          wh_id,
        "revenue":        total,
        "payment_method": payment,
        "title":          book["title"],
        "category_name":  book.get("category_name", ""),
        "api_verified":   True,
        # ── v2.1 사용자 필드 (수요예측 확장용) ───────────────────────────
        **user,
    }


# ── 메인 루프 ─────────────────────────────────────────────────────────────────
def run_once(catalog: list) -> bool:
    """한 사이클 실행. 성공 시 True 반환."""
    if not catalog:
        return False

    book = random.choice(catalog)
    isbn13 = book["isbn13"]

    # 온라인 채널 선택 (8: 수도권 / 14: 영남)
    online_loc_id, meta = random.choice(list(ONLINE_LOCATIONS.items()))
    wh_loc_id = meta["wh_location_id"]
    wh_id     = meta["wh_id"]

    stock = get_stock(isbn13, wh_loc_id)
    if stock is None or stock.get("available", 0) <= 0:
        log.debug("[skip] isbn=%s 재고 없음 (loc=%d)", isbn13, wh_loc_id)
        return False

    rec = make_record(book, stock, online_loc_id, wh_id)
    payload = json.dumps(rec, ensure_ascii=False).encode()

    kinesis.put_record(
        StreamName=STREAM_NAME,
        Data=payload,
        PartitionKey=isbn13,
    )
    log.info(
        "[put] %s isbn=%s qty=%d price=%s total=%s avail=%d→%d",
        rec["channel"], isbn13, rec["qty"],
        f"{rec['unit_price']:,}", f"{rec['total_price']:,}",
        stock["available"], stock["available"] - rec["qty"],
    )
    # 로컬 캐시 낙관적 업데이트 (실제 DB 반영과 무관)
    cache_key = (isbn13, wh_loc_id)
    if cache_key in _stock_cache:
        cached, ts = _stock_cache[cache_key]
        updated = {**cached, "available": max(0, cached["available"] - rec["qty"])}
        _stock_cache[cache_key] = (updated, ts)

    return True


def main() -> None:
    log.info("online-sim v2.0 시작 · stream=%s", STREAM_NAME)

    # 초기 카탈로그 로드 (재시도 포함)
    for attempt in range(5):
        catalog = get_catalog()
        if catalog:
            break
        log.warning("카탈로그 로드 재시도 (%d/5)...", attempt + 1)
        time.sleep(10)

    if not catalog:
        log.error("카탈로그 로드 실패 — 종료")
        return

    log.info("루프 시작 · 간격=%s-%s초", *INTERVAL_SEC)
    while True:
        try:
            run_once(get_catalog())
        except Exception as e:
            log.error("[loop] 오류: %s", e)
        time.sleep(random.uniform(*INTERVAL_SEC))


if __name__ == "__main__":
    main()
