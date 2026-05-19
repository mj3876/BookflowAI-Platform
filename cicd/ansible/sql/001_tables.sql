-- BOOKFLOW V6.2 RDS PostgreSQL DDL
-- 19 tables + 2 views (source: BOOKFLOW_Data_Schema_v3.xlsx sheet 03_Master_RDS상세DDL)
-- Excluded: stock_movements (out of BookFlow scope per schema doc)
-- Idempotent: CREATE TABLE IF NOT EXISTS

-- =========================================================================
-- 1. books (catalog master)
-- =========================================================================
CREATE TABLE IF NOT EXISTS books (
    isbn13                  CHAR(13) PRIMARY KEY,
    isbn10                  VARCHAR(10),
    aladin_item_id          BIGINT,
    title                   VARCHAR(500) NOT NULL,
    author                  VARCHAR(200),
    publisher               VARCHAR(100),
    pub_date                DATE,
    category_id             INTEGER,
    category_name           VARCHAR(200),
    price_standard          INTEGER,
    price_sales             INTEGER,
    cover_url               VARCHAR(500),
    description             TEXT,
    active                  BOOLEAN     NOT NULL DEFAULT TRUE,
    discontinue_mode        VARCHAR(20)          DEFAULT 'NONE',
    discontinue_reason      VARCHAR(100),
    discontinue_at          TIMESTAMPTZ,
    discontinue_by          VARCHAR(50),
    reactivated_at          TIMESTAMPTZ,
    expected_soldout_at     DATE,
    source                  VARCHAR(20) NOT NULL DEFAULT 'ALADIN',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================================
-- 2. authors
-- =========================================================================
CREATE TABLE IF NOT EXISTS authors (
    author_id               SERIAL       PRIMARY KEY,
    name                    VARCHAR(200) NOT NULL UNIQUE,
    debut_year              SMALLINT,
    past_books_count        INTEGER      NOT NULL DEFAULT 0
);

-- =========================================================================
-- 3. publishers
-- =========================================================================
CREATE TABLE IF NOT EXISTS publishers (
    publisher_id            SERIAL       PRIMARY KEY,
    name                    VARCHAR(200) NOT NULL UNIQUE,
    contact_email           VARCHAR(200)
);

-- =========================================================================
-- 4. warehouses (1=수도권, 2=영남)
-- =========================================================================
CREATE TABLE IF NOT EXISTS warehouses (
    wh_id                   SMALLINT    PRIMARY KEY,
    name                    VARCHAR(50) NOT NULL,
    region                  VARCHAR(50),
    capacity                INTEGER
);

-- =========================================================================
-- 5. locations (offline 10 + online 2 virtual + WH 2 = 14)
-- =========================================================================
CREATE TABLE IF NOT EXISTS locations (
    location_id             INTEGER      PRIMARY KEY,
    location_type           VARCHAR(20)  NOT NULL,
    wh_id                   SMALLINT     NOT NULL REFERENCES warehouses(wh_id),
    name                    VARCHAR(100),
    size                    VARCHAR(5),
    region                  VARCHAR(50),
    is_virtual              BOOLEAN      NOT NULL DEFAULT FALSE,
    active                  BOOLEAN      NOT NULL DEFAULT TRUE
);

-- =========================================================================
-- 6. inventory
-- =========================================================================
CREATE TABLE IF NOT EXISTS inventory (
    isbn13                  CHAR(13)    NOT NULL REFERENCES books(isbn13),
    location_id             INTEGER     NOT NULL REFERENCES locations(location_id),
    on_hand                 INTEGER     NOT NULL DEFAULT 0,
    reserved_qty            INTEGER     NOT NULL DEFAULT 0,
    safety_stock            INTEGER              DEFAULT 0,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by              VARCHAR(50),
    PRIMARY KEY (isbn13, location_id)
);

-- =========================================================================
-- 7. reservations
-- =========================================================================
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id          UUID        PRIMARY KEY,
    isbn13                  CHAR(13)    NOT NULL,
    location_id             INTEGER     NOT NULL REFERENCES locations(location_id),
    qty                     INTEGER     NOT NULL,
    reason                  VARCHAR(30) NOT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    ttl                     TIMESTAMPTZ,
    created_by              VARCHAR(50),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================================
-- 8. pending_orders (rebalance / wh-transfer / publisher-order unified queue)
-- =========================================================================
CREATE TABLE IF NOT EXISTS pending_orders (
    order_id                UUID        PRIMARY KEY,
    order_type              VARCHAR(20) NOT NULL,
    isbn13                  CHAR(13)    NOT NULL,
    source_location_id      INTEGER,
    target_location_id      INTEGER,
    qty                     INTEGER     NOT NULL,
    est_lead_time_hours     INTEGER,
    est_cost                INTEGER,
    forecast_rationale      JSONB,
    urgency_level           VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    auto_execute_eligible   BOOLEAN     NOT NULL DEFAULT FALSE,
    stock_days_remaining    NUMERIC(5,2),
    demand_confidence_ratio NUMERIC(5,2),
    demand_cv               NUMERIC(5,2),
    status                  VARCHAR(20),
    execution_reason        VARCHAR(30),
    reject_reason           VARCHAR(50),
    reject_count            SMALLINT    NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at             TIMESTAMPTZ,
    executed_at             TIMESTAMPTZ
);

-- =========================================================================
-- 9. order_approvals (2-stage WH transfer SOURCE/TARGET approvals)
-- =========================================================================
CREATE TABLE IF NOT EXISTS order_approvals (
    approval_id             UUID        PRIMARY KEY,
    order_id                UUID        NOT NULL REFERENCES pending_orders(order_id),
    approver_id             VARCHAR(50) NOT NULL,
    approver_role           VARCHAR(30),
    approver_wh_id          SMALLINT,
    approval_side           VARCHAR(10) NOT NULL,
    decision                VARCHAR(20) NOT NULL,
    reject_reason           VARCHAR(50),
    decided_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (order_id, approval_side)
);

-- =========================================================================
-- 10. returns
-- =========================================================================
CREATE TABLE IF NOT EXISTS returns (
    return_id               UUID        PRIMARY KEY,
    isbn13                  CHAR(13)    NOT NULL,
    location_id             INTEGER     NOT NULL REFERENCES locations(location_id),
    qty                     INTEGER     NOT NULL,
    reason                  VARCHAR(50) NOT NULL,
    status                  VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    requested_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hq_approved_at          TIMESTAMPTZ,
    rejected_at             TIMESTAMPTZ,    -- A4 FR-A6.8: HQ 거부 시점 (migrations/002)
    reject_reason           VARCHAR(200),   -- A4 FR-A6.8: HQ 거부 사유 (migrations/002)
    executed_at             TIMESTAMPTZ
);

-- =========================================================================
-- 11. audit_log
-- =========================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    log_id                  BIGSERIAL    PRIMARY KEY,
    ts                      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    actor_type              VARCHAR(20),
    actor_id                VARCHAR(100),
    action                  VARCHAR(50),
    entity_type             VARCHAR(30),
    entity_id               VARCHAR(100),
    before_state            JSONB,
    after_state             JSONB,
    source_ip               INET,
    request_id              UUID
);

-- =========================================================================
-- 12. users (Entra ID mirror)
-- =========================================================================
CREATE TABLE IF NOT EXISTS users (
    user_id                 VARCHAR(50) PRIMARY KEY,
    email                   VARCHAR(200),
    display_name            VARCHAR(100),
    role                    VARCHAR(30) NOT NULL,
    scope_wh_id             SMALLINT,
    scope_store_id          INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at           TIMESTAMPTZ
);

-- =========================================================================
-- 13. forecast_cache (D+1 only - D+2~5 in BigQuery)
-- =========================================================================
CREATE TABLE IF NOT EXISTS forecast_cache (
    snapshot_date           DATE         NOT NULL,
    isbn13                  CHAR(13)     NOT NULL,
    store_id                INTEGER      NOT NULL,
    predicted_demand        NUMERIC(10,2),
    confidence_low          NUMERIC(10,2),
    confidence_high         NUMERIC(10,2),
    model_version           VARCHAR(30),
    synced_at               TIMESTAMPTZ,
    PRIMARY KEY (snapshot_date, isbn13, store_id)
);

-- =========================================================================
-- 14. new_book_requests (publisher external requests)
-- =========================================================================
CREATE TABLE IF NOT EXISTS new_book_requests (
    id                      BIGSERIAL    PRIMARY KEY,
    publisher_id            VARCHAR(50)  NOT NULL,
    isbn13                  CHAR(13)     NOT NULL,
    title                   VARCHAR(500),
    author                  VARCHAR(200),
    genre                   VARCHAR(100),
    expected_pub_date       DATE,
    estimated_initial_sales INTEGER,
    marketing_plan          TEXT,
    similar_books           JSONB,
    target_segments         JSONB,
    status                  VARCHAR(20) NOT NULL DEFAULT 'NEW',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fetched_at              TIMESTAMPTZ,
    approved_at             TIMESTAMPTZ
);

-- =========================================================================
-- 15. spike_events (SNS spike detection log)
-- =========================================================================
CREATE TABLE IF NOT EXISTS spike_events (
    event_id                UUID        PRIMARY KEY,
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    isbn13                  CHAR(13)    NOT NULL,
    z_score                 NUMERIC(5,2),
    mentions_count          INTEGER,
    triggered_order_id      UUID,
    resolved_at             TIMESTAMPTZ,
    -- SNS 급등 자동 발주 (2026-05-19): spike-detect Lambda 가 z-score 기반 선제 발주 추정량 기록.
    -- 본사 직원이 대시보드에서 이 값을 보고 SNS 급등 발주 plan 을 승인 → pending_orders 생성.
    predicted_qty           INTEGER,
    forecast_meta           JSONB
);

-- 기존 DB 호환 (CREATE TABLE IF NOT EXISTS 는 컬럼 추가 안 함) — idempotent ALTER.
ALTER TABLE spike_events ADD COLUMN IF NOT EXISTS predicted_qty INTEGER;
ALTER TABLE spike_events ADD COLUMN IF NOT EXISTS forecast_meta JSONB;

-- =========================================================================
-- 16. notifications_log (Logic Apps send log)
-- =========================================================================
CREATE TABLE IF NOT EXISTS notifications_log (
    notification_id         UUID        PRIMARY KEY,
    event_type              VARCHAR(50) NOT NULL,
    correlation_id          UUID,
    severity                VARCHAR(20),
    recipients              JSONB,
    channels                VARCHAR(50),
    payload_summary         JSONB,
    sent_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status                  VARCHAR(20) NOT NULL DEFAULT 'SENT'
);

-- =========================================================================
-- 17. sales_realtime (POS transactions - 14d retention)
-- =========================================================================
CREATE TABLE IF NOT EXISTS sales_realtime (
    txn_id                  UUID        PRIMARY KEY,
    event_ts                TIMESTAMPTZ NOT NULL,
    store_id                INTEGER     NOT NULL,
    wh_id                   SMALLINT    NOT NULL,
    channel                 VARCHAR(10) NOT NULL,
    isbn13                  CHAR(13)    NOT NULL,
    qty                     SMALLINT    NOT NULL,
    unit_price              INTEGER     NOT NULL,
    discount                INTEGER     NOT NULL DEFAULT 0,
    revenue                 INTEGER     NOT NULL,
    payment_method          VARCHAR(10),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================================================
-- 18. inventory_snapshot_daily (00:00 KST - 90d retention)
-- =========================================================================
CREATE TABLE IF NOT EXISTS inventory_snapshot_daily (
    snapshot_date           DATE         NOT NULL,
    isbn13                  CHAR(13)     NOT NULL,
    location_id             INTEGER      NOT NULL,
    on_hand                 INTEGER      NOT NULL DEFAULT 0,
    reserved_qty            INTEGER      NOT NULL DEFAULT 0,
    available               INTEGER      NOT NULL DEFAULT 0,
    safety_stock            INTEGER,
    snapshot_taken_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, isbn13, location_id)
);

-- =========================================================================
-- 19. kpi_daily (pre-aggregated KPIs - 365d retention)
-- =========================================================================
CREATE TABLE IF NOT EXISTS kpi_daily (
    kpi_date                DATE         NOT NULL,
    store_id                INTEGER      NOT NULL,
    category_id             INTEGER      NOT NULL DEFAULT 0,
    channel                 VARCHAR(10)  NOT NULL DEFAULT 'ALL',
    qty_sold                INTEGER      NOT NULL DEFAULT 0,
    revenue                 BIGINT       NOT NULL DEFAULT 0,
    tx_count                INTEGER      NOT NULL DEFAULT 0,
    avg_price               INTEGER,
    unique_isbn_count       INTEGER,
    top_isbn                CHAR(13),
    synced_from_bq_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (kpi_date, store_id, category_id, channel)
);

-- =========================================================================
-- VIEW 1: v_inventory_available
-- =========================================================================
CREATE OR REPLACE VIEW v_inventory_available AS
SELECT
    i.isbn13,
    i.location_id,
    l.location_type,
    l.wh_id,
    l.name AS location_name,
    i.on_hand,
    i.reserved_qty,
    GREATEST(i.on_hand - i.reserved_qty, 0) AS available,
    i.safety_stock,
    i.updated_at
FROM inventory i
JOIN locations l ON l.location_id = i.location_id
WHERE l.active = TRUE;

-- =========================================================================
-- VIEW 2: v_online_store_available
-- Online (virtual) stores reference WH stock
-- =========================================================================
CREATE OR REPLACE VIEW v_online_store_available AS
SELECT
    online.location_id   AS online_location_id,
    online.name          AS online_location_name,
    online.wh_id,
    wh.location_id       AS wh_location_id,
    i.isbn13,
    GREATEST(i.on_hand - i.reserved_qty, 0) AS available
FROM locations online
JOIN locations wh
  ON wh.wh_id = online.wh_id
 AND wh.location_type = 'WH'
JOIN inventory i
  ON i.location_id = wh.location_id
WHERE online.location_type = 'STORE_ONLINE'
  AND online.is_virtual = TRUE
  AND online.active = TRUE;
