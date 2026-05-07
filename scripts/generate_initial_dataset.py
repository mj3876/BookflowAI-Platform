"""
generate_initial_dataset.py
===========================
BOOKFLOW - Vertex AI 초기 학습용 합성 데이터 생성기

[실행 전 필요]
  pip install httpx pandas numpy pyarrow python-dotenv faker scipy

[.env 파일 필요 (같은 폴더)]
  ALADIN_TTB_KEY=ttbxxxxxxxxxxxxxxxx
  HOLIDAY_API_KEY=your_data_go_kr_service_key   # data.go.kr 에서 발급
  OUTPUT_DIR=./output/historical                 # 선택 (기본값 사용 가능)

[출력 파일 - OUTPUT_DIR 하위]
  books_seed.parquet          1,000 행  (알라딘 실데이터)
  stores_seed.parquet            12 행
  locations_seed.parquet         14 행
  sales_fact_2y.parquet        ~260만 행
  inventory_daily_2y.parquet   ~350만 행
  features_2y.parquet           ~73만 행

[실행]
  python generate_initial_dataset.py
  python generate_initial_dataset.py --days 365   # 1년치만
  python generate_initial_dataset.py --books 500  # 도서 500권
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from faker import Faker

# ── 환경변수 로드 ──────────────────────────────────────────────────────────────
load_dotenv()

ALADIN_TTB_KEY   = os.getenv("ALADIN_TTB_KEY", "")
HOLIDAY_API_KEY  = os.getenv("HOLIDAY_API_KEY", "")
OUTPUT_DIR       = Path(os.getenv("OUTPUT_DIR", "./output/historical"))

# ── 고정 마스터 데이터 ─────────────────────────────────────────────────────────

STORES = [
    # store_id, location_id, wh_id, name,        size, region,    is_online
    (1,  3,  1, "강남점",   "L", "서울 강남구",  False),
    (2,  4,  1, "홍대점",   "M", "서울 마포구",  False),
    (3,  5,  1, "잠실점",   "M", "서울 송파구",  False),
    (4,  6,  1, "신촌점",   "S", "서울 서대문구", False),
    (5,  7,  1, "수원점",   "S", "경기 수원시",  False),
    (6,  8,  1, "WH1온라인", None, "서울",        True),   # 가상 · WH1 재고 참조
    (7,  9,  2, "부산점",   "L", "부산 해운대구", False),
    (8,  10, 2, "대구점",   "M", "대구 중구",    False),
    (9,  11, 2, "광주점",   "M", "광주 동구",    False),
    (10, 12, 2, "대전점",   "S", "대전 서구",    False),
    (11, 13, 2, "울산점",   "S", "울산 남구",    False),
    (12, 14, 2, "WH2온라인", None, "부산",        True),   # 가상 · WH2 재고 참조
]

WAREHOUSES = [
    (1, "수도권 물류센터", "수도권"),
    (2, "영남 물류센터",   "영남"),
]

# ── 알라딘 API ─────────────────────────────────────────────────────────────────

ALADIN_BASE = "https://www.aladin.co.kr/ttb/api/ItemList.aspx"
ALADIN_PARAMS_TEMPLATE = {
    "ttbkey":      ALADIN_TTB_KEY,
    "QueryType":   "ItemNewAll",
    "MaxResults":  50,
    "Output":      "js",
    "Cover":       "Big",
    "Version":     "20131101",
}
ALADIN_QUERY_TYPES = ["ItemNewAll", "Bestseller", "ItemNewSpecial"]


def fetch_aladin_books(target: int = 1_000) -> list[dict]:
    """알라딘 Open API 로 실제 도서 데이터를 수집합니다."""
    if not ALADIN_TTB_KEY:
        print("[WARN] ALADIN_TTB_KEY 가 없습니다 → Faker 합성 도서로 대체합니다.")
        return []

    books: dict[str, dict] = {}   # isbn13 → item (중복 제거)
    client = httpx.Client(timeout=15)

    for query_type in ALADIN_QUERY_TYPES:
        if len(books) >= target:
            break

        print(f"  알라딘 API 조회 중 [{query_type}] ...")
        for start in range(1, 3_000, 50):
            if len(books) >= target:
                break

            params = {**ALADIN_PARAMS_TEMPLATE, "QueryType": query_type, "Start": start}
            response = _aladin_get(client, params)
            if response is None:
                break

            items = response.get("item", [])
            if not items:
                break

            for item in items:
                isbn13 = str(item.get("isbn13", "")).strip()
                if len(isbn13) == 13 and isbn13 not in books:
                    books[isbn13] = item

            time.sleep(0.2)   # rate limit: 초당 5회

    client.close()
    result = list(books.values())[:target]
    print(f"  알라딘 도서 수집 완료: {len(result)}권")
    return result


def _aladin_get(client: httpx.Client, params: dict, retries: int = 4) -> dict | None:
    for attempt in range(retries):
        try:
            resp = client.get(ALADIN_BASE, params=params)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code >= 500:
                wait = 2 ** attempt
                print(f"    5xx 오류 → {wait}s 후 재시도 (시도 {attempt + 1}/{retries})")
                time.sleep(wait)
        except Exception as exc:
            print(f"    요청 실패: {exc}")
            time.sleep(2 ** attempt)
    return None


def _parse_aladin_item(item: dict, fake: Faker) -> dict:
    """알라딘 응답 item → books_seed 스키마 dict"""
    author_raw = str(item.get("author", "")).strip()
    author = author_raw.split("(")[0].strip() or fake.name()

    price_std = int(item.get("priceStandard") or 15_000)
    price_sal = int(item.get("priceSales")    or price_std)
    sales_pt  = int(item.get("salesPoint")    or 0)

    pub_date_str = str(item.get("pubDate", "")).strip()
    try:
        pub_date = date.fromisoformat(pub_date_str)
    except ValueError:
        pub_date = date.today() - timedelta(days=random.randint(1, 1_000))

    return {
        "isbn13":            str(item.get("isbn13", "")),
        "title":             str(item.get("title",  fake.sentence(nb_words=4)))[:200],
        "author":            author[:100],
        "publisher":         str(item.get("publisher", fake.company()))[:100],
        "pub_date":          pub_date.isoformat(),
        "category_id":       int(item.get("categoryId") or 0),
        "category_name":     str(item.get("categoryName", ""))[:200],
        "price_standard":    price_std,
        "price_sales":       price_sal,
        "price_tier":        _price_tier(price_std),
        "cover_url":         str(item.get("cover", ""))[:500],
        "description":       _strip_html(str(item.get("description", "")))[:1_000],
        "sales_point":       sales_pt,
        "item_page":         int((item.get("subInfo") or {}).get("itemPage") or random.randint(100, 600)),
        "is_bestseller_flag": sales_pt >= 50_000,
        "active":            True,
        "source":            "ALADIN",
    }


def _price_tier(price: int) -> str:
    if price < 10_000: return "LOW"
    if price < 20_000: return "MID"
    return "HIGH"


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", text).strip()


# ── 특일정보 API ───────────────────────────────────────────────────────────────

HOLIDAY_BASE = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService"
HOLIDAY_APIS = {
    "holidays":      "getHoliDeInfo",
    "anniversaries": "getAnniversaryInfo",
    "divisions_24":  "get24DivisionsInfo",
    "sundries":      "getSundryDayInfo",
}
PRIORITY = {"holidays": 1, "anniversaries": 2, "divisions_24": 3, "sundries": 4}


def fetch_holidays(start_year: int, end_year: int) -> dict[date, str]:
    """
    특일정보 API 로 공휴일·기념일·절기를 가져옵니다.
    키 없으면 내장 한국 공휴일 테이블로 대체합니다.
    반환: {date: holiday_name} (우선순위 높은 것 1개)
    """
    if not HOLIDAY_API_KEY:
        print("[WARN] HOLIDAY_API_KEY 가 없습니다 → 내장 공휴일 테이블로 대체합니다.")
        return _builtin_holidays(start_year, end_year)

    raw: list[tuple[date, str, int]] = []   # (date, name, priority)
    client = httpx.Client(timeout=15)

    for year in range(start_year, end_year + 1):
        for api_name, method in HOLIDAY_APIS.items():
            url = f"{HOLIDAY_BASE}/{method}"
            params = {
                "serviceKey": HOLIDAY_API_KEY,
                "solYear":    year,
                "numOfRows":  100,
                "_type":      "json",
            }
            try:
                resp = client.get(url, params=params, timeout=10)
                data = resp.json()
                items = (
                    data.get("response", {})
                        .get("body", {})
                        .get("items", {})
                        .get("item", [])
                )
                if isinstance(items, dict):
                    items = [items]
                for item in items:
                    locdate = str(item.get("locdate", ""))
                    if len(locdate) == 8:
                        d = date(int(locdate[:4]), int(locdate[4:6]), int(locdate[6:]))
                        raw.append((d, str(item.get("dateName", "")), PRIORITY[api_name]))
                time.sleep(0.1)
            except Exception as exc:
                print(f"    특일 API 실패 [{api_name} {year}]: {exc}")

    client.close()

    # 같은 날 여러 항목 → 우선순위 높은 것 1개만
    best: dict[date, tuple[str, int]] = {}
    for d, name, prio in raw:
        if d not in best or prio < best[d][1]:
            best[d] = (name, prio)

    return {d: name for d, (name, _) in best.items()}


def _builtin_holidays(start_year: int, end_year: int) -> dict[date, str]:
    """API 키 없을 때 사용하는 한국 고정 공휴일 (음력 제외)"""
    fixed = [
        (1,  1,  "신정"),
        (3,  1,  "삼일절"),
        (5,  5,  "어린이날"),
        (6,  6,  "현충일"),
        (8,  15, "광복절"),
        (10, 3,  "개천절"),
        (10, 9,  "한글날"),
        (12, 25, "크리스마스"),
    ]
    result = {}
    for year in range(start_year, end_year + 1):
        for month, day, name in fixed:
            try:
                result[date(year, month, day)] = name
            except ValueError:
                pass
    return result


# ── 합성 데이터 생성 ───────────────────────────────────────────────────────────

def build_books_seed(aladin_items: list[dict], target: int, fake: Faker) -> pd.DataFrame:
    """books_seed.parquet 생성"""
    rows = [_parse_aladin_item(item, fake) for item in aladin_items]

    # 알라딘 데이터가 부족하면 Faker 로 보충
    used_isbn = {r["isbn13"] for r in rows}
    while len(rows) < target:
        isbn = _make_isbn13(used_isbn)
        used_isbn.add(isbn)
        price_std = random.randrange(9_000, 38_000, 500)
        debut_year = random.randint(1980, date.today().year - 1)
        rows.append({
            "isbn13":            isbn,
            "title":             fake.sentence(nb_words=random.randint(2, 6))[:200],
            "author":            fake.name()[:100],
            "publisher":         fake.company()[:100],
            "pub_date":          (date.today() - timedelta(days=random.randint(0, 3_650))).isoformat(),
            "category_id":       random.choice([101, 102, 103, 104, 105, 106]),
            "category_name":     random.choice([
                "국내도서>소설/시/희곡>한국소설",
                "국내도서>경제경영>경영전략",
                "국내도서>인문학>철학",
                "국내도서>과학>자연과학",
                "국내도서>아동>어린이",
                "국내도서>자기계발",
            ]),
            "price_standard":    price_std,
            "price_sales":       int(price_std * random.uniform(0.82, 0.95)),
            "price_tier":        _price_tier(price_std),
            "cover_url":         f"https://example.com/covers/{isbn}.jpg",
            "description":       fake.sentence(nb_words=12)[:1_000],
            "sales_point":       random.randint(50, 100_000),
            "item_page":         random.randint(90, 850),
            "is_bestseller_flag": random.random() < 0.05,
            "active":            True,
            "source":            "ALADIN",
        })

    df = pd.DataFrame(rows[:target])

    # author 피처 추가 (books_static 에서 JOIN 할 컬럼)
    debut_years = [
        int(str(r.get("pub_date", "2020-01-01"))[:4]) - random.randint(0, 20)
        for r in rows[:target]
    ]
    df["author_debut_year"]        = [max(1970, y) for y in debut_years]
    df["author_experience_years"]  = date.today().year - df["author_debut_year"]
    df["author_past_books_count"]  = [random.randint(0, 35) for _ in range(target)]

    return df


def build_stores_seed() -> pd.DataFrame:
    rows = [
        {
            "store_id":    sid,
            "location_id": lid,
            "wh_id":       wh,
            "name":        name,
            "size":        size,
            "region":      region,
            "is_online":   online,
        }
        for sid, lid, wh, name, size, region, online in STORES
    ]
    return pd.DataFrame(rows)


def build_locations_seed() -> pd.DataFrame:
    rows = [
        {
            "location_id":   1,
            "location_type": "WH",
            "wh_id":         1,
            "name":          "수도권 물류센터",
            "size":          "L",
            "region":        "경기 이천시",
            "is_virtual":    False,
            "active":        True,
        },
        {
            "location_id":   2,
            "location_type": "WH",
            "wh_id":         2,
            "name":          "영남 물류센터",
            "size":          "L",
            "region":        "경남 양산시",
            "is_virtual":    False,
            "active":        True,
        },
    ]
    for sid, lid, wh, name, size, region, online in STORES:
        rows.append({
            "location_id":   lid,
            "location_type": "STORE_ONLINE" if online else "STORE_OFFLINE",
            "wh_id":         wh,
            "name":          name,
            "size":          size,
            "region":        region,
            "is_virtual":    online,
            "active":        True,
        })
    return pd.DataFrame(rows)


def build_sales_fact(books: pd.DataFrame, stores: pd.DataFrame,
                     date_range: list[date]) -> pd.DataFrame:
    """
    sales_fact_2y.parquet 생성
    - 1,000권 × 12지점 × 730일 = 876만 잠재
    - qty=0 70% 제거 → ~260만
    """
    print("  sales_fact 생성 중 ...")
    isbn_list   = books["isbn13"].tolist()
    n_isbn      = len(isbn_list)

    # 베스트셀러 상위 20권 (sales_point 기준)
    top20 = set(books.nlargest(20, "sales_point")["isbn13"].tolist())

    rows = []
    for d in date_range:
        for sid, lid, wh, name, size, region, online in STORES:
            channel = "online" if online else "offline"

            # 지점 크기별 활성 도서 비율
            active_ratio = {"L": 0.40, "M": 0.30, "S": 0.20}.get(size or "M", 0.30)
            if online:
                active_ratio = 0.25

            for isbn in isbn_list:
                is_top20 = isbn in top20

                # 판매 발생 확률: 상위 30% 확률, 일반 9%
                sale_prob = 0.30 if is_top20 else active_ratio * 0.30

                if random.random() > sale_prob:
                    continue   # qty=0 → 제외 (희소화)

                # 수량 분포: qty=1 85%, 2 12%, 3 3%
                qty = random.choices([1, 2, 3], weights=[85, 12, 3])[0]
                price = int(books.loc[books["isbn13"] == isbn, "price_sales"].iloc[0])
                revenue = qty * price

                rows.append({
                    "sale_date":   d.isoformat(),
                    "isbn13":      isbn,
                    "store_id":    sid,
                    "wh_id":       wh,
                    "channel":     channel,
                    "qty_sold":    qty,
                    "revenue":     float(revenue),
                    "avg_price":   float(price),
                    "tx_count":    random.randint(1, qty),
                    "synthetic":   True,
                })

        if d.day == 1:
            print(f"    {d.isoformat()} 처리 중 ... (현재 {len(rows):,}행)")

    df = pd.DataFrame(rows)
    print(f"  sales_fact 완료: {len(df):,}행")
    return df


def build_inventory_daily(books: pd.DataFrame, date_range: list[date]) -> pd.DataFrame:
    """
    inventory_daily_2y.parquet 생성
    - 12 물리 location × 1,000권 × 730일 × ~40% 재고 보유 → ~350만
    """
    print("  inventory_daily 생성 중 ...")
    isbn_list     = books["isbn13"].tolist()
    # 물리 location (가상 location 8, 14 제외)
    phys_locs     = [(sid, lid, wh) for sid, lid, wh, *_ in STORES if lid not in (8, 14)]

    rows = []
    for d in date_range:
        for sid, lid, wh in phys_locs:
            for isbn in isbn_list:
                if random.random() > 0.40:
                    continue   # 재고 없는 케이스 제외

                on_hand      = random.randint(0, 500)
                reserved_qty = random.randint(0, min(on_hand, 80))
                safety_stock = random.randint(5, 50)

                rows.append({
                    "snapshot_date": d.isoformat(),
                    "isbn13":        isbn,
                    "location_id":   lid,
                    "on_hand":       on_hand,
                    "reserved_qty":  reserved_qty,
                    "safety_stock":  safety_stock,
                    "synthetic":     True,
                })

        if d.day == 1:
            print(f"    {d.isoformat()} 처리 중 ... (현재 {len(rows):,}행)")

    df = pd.DataFrame(rows)
    print(f"  inventory_daily 완료: {len(df):,}행")
    return df


def build_features(books: pd.DataFrame, date_range: list[date],
                   holidays: dict[date, str]) -> pd.DataFrame:
    """
    features_2y.parquet 생성
    - 1,000권 × 730일 = 730,000행 (~73만)
    - 공휴일은 실제 특일정보 API 결과 사용
    """
    print("  features 생성 중 ...")
    isbn_list = books["isbn13"].tolist()
    isbn_arr  = np.array(isbn_list)

    pub_dates = {}
    for _, row in books.iterrows():
        try:
            pub_dates[row["isbn13"]] = date.fromisoformat(str(row["pub_date"]))
        except Exception:
            pub_dates[row["isbn13"]] = date.today() - timedelta(days=365)

    rows = []
    for d in date_range:
        is_holiday   = d in holidays
        holiday_name = holidays.get(d, "")
        season       = _season(d.month)
        dow          = d.isoweekday()          # 1=월 ~ 7=일
        is_weekend   = dow >= 6
        month        = d.month

        # 다음 공휴일까지 일수
        nearby = 0
        for i in range(1, 31):
            if (d + timedelta(days=i)) in holidays:
                nearby = i
                break

        for isbn in isbn_list:
            pd_date  = pub_dates[isbn]
            age_days = (d - pd_date).days if d >= pd_date else 0

            rows.append({
                "feature_date":            d.isoformat(),
                "isbn13":                  isbn,
                "is_holiday":              is_holiday,
                "holiday_name":            holiday_name,
                "season":                  season,
                "day_of_week":             dow,
                "is_weekend":              is_weekend,
                "month":                   month,
                "event_nearby_days":       nearby,
                "sns_mentions_1d":         _sns_mentions(),
                "sns_mentions_7d":         _sns_mentions() * 7,
                "book_age_days":           max(0, age_days),
                "is_bestseller_flag":      bool(books.loc[books["isbn13"] == isbn, "is_bestseller_flag"].iloc[0]),
                "on_hand_total":           random.randint(0, 5_000),
                "days_since_last_stockout": random.randint(0, 365),
                "loaded_at":               d.isoformat(),
                "synthetic":               True,
            })

        if d.day == 1:
            print(f"    {d.isoformat()} 처리 중 ... (현재 {len(rows):,}행)")

    df = pd.DataFrame(rows)
    print(f"  features 완료: {len(df):,}행")
    return df


# ── 유틸 함수 ──────────────────────────────────────────────────────────────────

def _season(month: int) -> str:
    if month in (3, 4, 5):   return "SPRING"
    if month in (6, 7, 8):   return "SUMMER"
    if month in (9, 10, 11): return "FALL"
    return "WINTER"


def _sns_mentions() -> int:
    """멘션 수: 대부분 소량, 간헐적 급등 시뮬"""
    if random.random() < 0.05:          # 5% 확률 급등
        return random.randint(500, 5_000)
    return int(abs(random.gauss(30, 40)))


def _make_isbn13(used: set[str]) -> str:
    while True:
        prefix = "979" + "".join(str(random.randint(0, 9)) for _ in range(9))
        check  = _isbn13_check(prefix)
        isbn   = prefix + str(check)
        if isbn not in used:
            used.add(isbn)
            return isbn


def _isbn13_check(first12: str) -> int:
    total = sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(first12))
    return (10 - total % 10) % 10


# ── 메인 ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BOOKFLOW 초기 학습 데이터 생성기")
    p.add_argument("--books", type=int, default=1_000, help="수집할 도서 수 (기본: 1000)")
    p.add_argument("--days",  type=int, default=730,   help="생성할 기간(일) (기본: 730 = 2년)")
    p.add_argument("--seed",  type=int, default=42,    help="랜덤 시드")
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="출력 디렉터리")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # 검증
    if not ALADIN_TTB_KEY:
        print("[ERROR] .env 에 ALADIN_TTB_KEY 가 없습니다.")
        print("  → .env 파일을 만들고 ALADIN_TTB_KEY=ttbxxxxxxxx 를 추가하세요.")
        sys.exit(1)

    random.seed(args.seed)
    fake = Faker("ko_KR")
    Faker.seed(args.seed)

    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"출력 경로: {out_dir.resolve()}")

    # 날짜 범위 (오늘 기준 과거 args.days 일)
    today      = date.today()
    start_date = today - timedelta(days=args.days - 1)
    date_range = [start_date + timedelta(days=i) for i in range(args.days)]
    print(f"날짜 범위: {start_date} ~ {today} ({args.days}일)")

    # ── 1. 알라딘 도서 수집 ──────────────────────────────────────────────────
    print("\n[1/6] 알라딘 도서 수집 ...")
    aladin_items = fetch_aladin_books(args.books)
    books_df     = build_books_seed(aladin_items, args.books, fake)
    _save(books_df, out_dir / "books_seed.parquet")

    # ── 2. 지점 마스터 ───────────────────────────────────────────────────────
    print("\n[2/6] 지점·위치 마스터 생성 ...")
    stores_df    = build_stores_seed()
    locations_df = build_locations_seed()
    _save(stores_df,    out_dir / "stores_seed.parquet")
    _save(locations_df, out_dir / "locations_seed.parquet")

    # ── 3. 공휴일 데이터 ─────────────────────────────────────────────────────
    print("\n[3/6] 특일정보 API 공휴일 수집 ...")
    holidays = fetch_holidays(start_date.year, today.year)
    print(f"  공휴일 총 {len(holidays)}일 수집 완료")

    # ── 4. sales_fact ────────────────────────────────────────────────────────
    print("\n[4/6] sales_fact 생성 ...")
    sales_df = build_sales_fact(books_df, stores_df, date_range)
    _save(sales_df, out_dir / "sales_fact_2y.parquet")

    # ── 5. inventory_daily ───────────────────────────────────────────────────
    print("\n[5/6] inventory_daily 생성 ...")
    inv_df = build_inventory_daily(books_df, date_range)
    _save(inv_df, out_dir / "inventory_daily_2y.parquet")

    # ── 6. features ──────────────────────────────────────────────────────────
    print("\n[6/6] features 생성 ...")
    feat_df = build_features(books_df, date_range, holidays)
    _save(feat_df, out_dir / "features_2y.parquet")

    # ── 완료 요약 ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("✅ 생성 완료")
    print("=" * 60)
    total = 0
    for fname in ["books_seed", "stores_seed", "locations_seed",
                  "sales_fact_2y", "inventory_daily_2y", "features_2y"]:
        path = out_dir / f"{fname}.parquet"
        rows = len(pd.read_parquet(path))
        size = path.stat().st_size / 1_024 / 1_024
        print(f"  {fname:30s}  {rows:>9,}행  ({size:.1f} MB)")
        total += rows
    print(f"\n  합계: {total:,}행")
    print(f"  경로: {out_dir.resolve()}")
    print("\n다음 단계:")
    print("  1. output/historical/*.parquet → GCS Staging 업로드")
    print("  2. Cloud Functions 로 BigQuery LOAD")
    print("  3. Vertex AI Pipelines 학습 시작")


def _save(df: pd.DataFrame, path: Path) -> None:
    df.to_parquet(path, index=False, compression="snappy")
    size_mb = path.stat().st_size / 1_024 / 1_024
    print(f"  저장: {path.name}  ({len(df):,}행, {size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
