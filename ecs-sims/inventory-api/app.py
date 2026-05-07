"""
BookFlow Inventory API  ·  v2.0  (FastAPI + psycopg2)

Endpoints
---------
GET /health                             DB 연결 포함 헬스체크
GET /stock?isbn13={}&location_id={}     단일 위치 재고 (시뮬 핵심 호출)
GET /availability/{isbn13}              전체 위치별 가용 재고
GET /books/active?limit={}              시뮬 부트스트랩용 활성 도서 목록
GET /locations?location_type={}         위치 마스터

Location 매핑
  WH          location_id 1(수도권), 2(영남)           → 직접 재고
  STORE_OFFLINE location_id 3-7(수도권점), 9-13(영남점) → 직접 재고
  STORE_ONLINE  location_id 8, 14 (가상)              → 재고 없음 (WH 참조)
"""
import json
import logging
import os
import time
from contextlib import contextmanager

import boto3
import psycopg2
import psycopg2.extras
import psycopg2.pool
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    level=os.environ.get("LOG_LEVEL", "INFO"),
)
log = logging.getLogger("inventory-api")

# ── 환경 변수 ─────────────────────────────────────────────────────────────────
RDS_HOST       = os.environ["RDS_ENDPOINT"]
RDS_PORT       = int(os.environ.get("RDS_PORT", "5432"))
RDS_DBNAME     = os.environ["RDS_DBNAME"]
RDS_SECRET_ARN = os.environ["RDS_SECRET_ARN"]
AWS_REGION     = os.environ.get("AWS_REGION", "ap-northeast-1")

# ── DB 커넥션 풀 (지연 초기화) ───────────────────────────────────────────────
_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_creds() -> tuple[str, str]:
    client = boto3.client("secretsmanager", region_name=AWS_REGION)
    secret = client.get_secret_value(SecretId=RDS_SECRET_ARN)
    creds  = json.loads(secret["SecretString"])
    return creds["username"], creds["password"]


def _pool_init() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        user, password = _get_creds()
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=10,
            host=RDS_HOST, port=RDS_PORT, dbname=RDS_DBNAME,
            user=user, password=password,
            connect_timeout=5,
            options="-c application_name=inventory-api",
        )
        log.info("DB pool initialized (min=2 max=10)")
    return _pool


@contextmanager
def get_cursor():
    pool = _pool_init()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# ── FastAPI ──────────────────────────────────────────────────────────────────
app = FastAPI(
    title="BookFlow Inventory API",
    description="Read-Only 재고 조회 API — 판매 시뮬레이션 엔진 전용",
    version="2.0.0",
)


@app.get("/health")
def health():
    try:
        with get_cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM inventory")
            row = cur.fetchone()
        return {"status": "ok", "inventory_rows": row["cnt"], "ts": time.time()}
    except Exception as e:
        log.error("health check failed: %s", e)
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


@app.get("/stock")
def get_stock(
    isbn13: str      = Query(..., min_length=13, max_length=13, description="ISBN-13"),
    location_id: int = Query(..., ge=1, le=99,  description="재고 조회 위치 ID (WH or STORE_OFFLINE)"),
):
    """
    단일 위치의 실시간 재고 반환.

    시뮬 엔진 호출 패턴
      온라인 심 → location_id=1(수도권WH) or 2(영남WH)
      오프라인 심 → location_id=3-7, 9-13 (오프라인 매장)

    응답 available=0 이면 시뮬 엔진이 해당 ISBN 건너뜀.
    """
    sql = """
        SELECT
            i.isbn13,
            i.location_id,
            l.location_type,
            l.wh_id,
            l.name                                              AS location_name,
            l.region,
            i.on_hand,
            i.reserved_qty,
            GREATEST(i.on_hand - i.reserved_qty, 0)            AS available,
            i.safety_stock,
            b.title,
            b.author,
            b.publisher,
            b.category_name,
            b.price_standard,
            b.price_sales,
            i.updated_at
        FROM inventory  i
        JOIN locations  l ON l.location_id = i.location_id
        JOIN books      b ON b.isbn13      = i.isbn13
        WHERE i.isbn13      = %(isbn13)s
          AND i.location_id = %(location_id)s
          AND l.active      = TRUE
          AND b.active      = TRUE
    """
    with get_cursor() as cur:
        cur.execute(sql, {"isbn13": isbn13, "location_id": location_id})
        row = cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"isbn13={isbn13} location_id={location_id} 재고 없음")

    return dict(row)


@app.get("/availability/{isbn13}")
def get_availability(isbn13: str):
    """
    한 ISBN의 전체 위치별 가용 재고 목록.
    available > 0 인 위치만 필터링해서 반환.
    """
    sql = """
        SELECT
            i.location_id,
            l.location_type,
            l.wh_id,
            l.name                                          AS location_name,
            l.region,
            l.is_virtual,
            i.on_hand,
            i.reserved_qty,
            GREATEST(i.on_hand - i.reserved_qty, 0)        AS available,
            i.safety_stock,
            b.price_sales
        FROM inventory  i
        JOIN locations  l ON l.location_id = i.location_id
        JOIN books      b ON b.isbn13      = i.isbn13
        WHERE i.isbn13 = %(isbn13)s
          AND l.active = TRUE
          AND b.active = TRUE
          AND (i.on_hand - i.reserved_qty) > 0
        ORDER BY i.location_id
    """
    with get_cursor() as cur:
        cur.execute(sql, {"isbn13": isbn13})
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail=f"isbn13={isbn13} 재고 있는 위치 없음")

    return [dict(r) for r in rows]


@app.get("/books/active")
def get_active_books(limit: int = Query(1000, ge=1, le=5000, description="최대 반환 도서 수")):
    """
    시뮬 엔진 부트스트랩 호출.
    활성(active=TRUE, discontinue_mode=NONE) 도서의 ISBN + 가격 목록 반환.
    """
    sql = """
        SELECT
            b.isbn13,
            b.title,
            b.author,
            b.publisher,
            b.category_id,
            b.category_name,
            b.price_standard,
            b.price_sales
        FROM books b
        WHERE b.active           = TRUE
          AND b.discontinue_mode = 'NONE'
        ORDER BY b.isbn13
        LIMIT %(limit)s
    """
    with get_cursor() as cur:
        cur.execute(sql, {"limit": limit})
        rows = cur.fetchall()

    log.info("books/active → %d rows", len(rows))
    return [dict(r) for r in rows]


@app.get("/locations")
def get_locations(
    location_type: str | None = Query(None, description="WH | STORE_OFFLINE | STORE_ONLINE"),
):
    """위치 마스터 목록 (active만)"""
    if location_type:
        sql    = "SELECT * FROM locations WHERE active=TRUE AND location_type=%(t)s ORDER BY location_id"
        params = {"t": location_type}
    else:
        sql    = "SELECT * FROM locations WHERE active=TRUE ORDER BY location_id"
        params = {}

    with get_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [dict(r) for r in rows]
