-- BOOKFLOW V6.2 RDS PostgreSQL Indexes
-- Read pattern based: dashboard fan-in / pod hot paths / decision queue / audit search
-- Idempotent: CREATE INDEX IF NOT EXISTS

-- =========================================================================
-- books (catalog browse / inactive filter)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_books_active           ON books (active) WHERE active = TRUE;
CREATE INDEX IF NOT EXISTS idx_books_publisher        ON books (publisher);
CREATE INDEX IF NOT EXISTS idx_books_category         ON books (category_id);
CREATE INDEX IF NOT EXISTS idx_books_discontinue_mode ON books (discontinue_mode) WHERE discontinue_mode <> 'NONE';

-- =========================================================================
-- inventory (already PK = (isbn13, location_id) covers JOIN)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_inventory_location  ON inventory (location_id);
CREATE INDEX IF NOT EXISTS idx_inventory_low_stock ON inventory ((on_hand - reserved_qty)) WHERE on_hand - reserved_qty <= 5;

-- =========================================================================
-- reservations (TTL expiry sweep / status filter)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_reservations_status_ttl ON reservations (status, ttl) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_reservations_isbn_loc   ON reservations (isbn13, location_id);

-- =========================================================================
-- pending_orders (HQ Decision/Approval page hot paths)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_pending_orders_status_created ON pending_orders (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_orders_urgency        ON pending_orders (urgency_level, status) WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_pending_orders_isbn           ON pending_orders (isbn13);
CREATE INDEX IF NOT EXISTS idx_pending_orders_target         ON pending_orders (target_location_id);
CREATE INDEX IF NOT EXISTS idx_pending_orders_auto_eligible  ON pending_orders (auto_execute_eligible, status) WHERE auto_execute_eligible = TRUE;

-- =========================================================================
-- order_approvals (audit trail per order)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_order_approvals_approver ON order_approvals (approver_id, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_approvals_decided  ON order_approvals (decided_at DESC);

-- =========================================================================
-- returns (HQ Returns page · status workflow)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_returns_status_requested ON returns (status, requested_at DESC);
CREATE INDEX IF NOT EXISTS idx_returns_location         ON returns (location_id);

-- =========================================================================
-- audit_log (forensic / per-actor / per-entity searches)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_audit_log_ts        ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor     ON audit_log (actor_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_entity    ON audit_log (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action    ON audit_log (action, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_request   ON audit_log (request_id);

-- =========================================================================
-- users (login / role-based listing)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_users_email      ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_role_scope ON users (role, scope_wh_id, scope_store_id);

-- =========================================================================
-- forecast_cache (snapshot_date is part of PK · individual book lookup)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_forecast_cache_isbn ON forecast_cache (isbn13, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_forecast_cache_store ON forecast_cache (store_id, snapshot_date DESC);

-- =========================================================================
-- new_book_requests (HQ Requests workflow)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_new_book_requests_status   ON new_book_requests (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_new_book_requests_publisher ON new_book_requests (publisher_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_new_book_requests_isbn      ON new_book_requests (isbn13);

-- =========================================================================
-- spike_events (recent active spikes)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_spike_events_active   ON spike_events (resolved_at, detected_at DESC) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_spike_events_isbn     ON spike_events (isbn13, detected_at DESC);

-- =========================================================================
-- notifications_log (correlation / event_type tracking)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_notifications_correlation ON notifications_log (correlation_id);
CREATE INDEX IF NOT EXISTS idx_notifications_event_sent  ON notifications_log (event_type, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_status      ON notifications_log (status) WHERE status IN ('FAILED','RETRYING');

-- =========================================================================
-- sales_realtime (dashboard hot path · "today" queries)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_sales_realtime_event_ts  ON sales_realtime (event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sales_realtime_store_ts  ON sales_realtime (store_id, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sales_realtime_isbn_ts   ON sales_realtime (isbn13, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_sales_realtime_channel   ON sales_realtime (channel, event_ts DESC);

-- =========================================================================
-- inventory_snapshot_daily (already PK = (snapshot_date, isbn13, location_id))
-- BQ archive query support
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_inventory_snapshot_date ON inventory_snapshot_daily (snapshot_date DESC);

-- =========================================================================
-- kpi_daily (already PK covers most queries)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_kpi_daily_date ON kpi_daily (kpi_date DESC);
