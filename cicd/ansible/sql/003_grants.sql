-- BOOKFLOW V6.2 RDS PostgreSQL Grants
-- Roles: bookflow_app (app pod superset) + per-pod least-privilege roles
-- Idempotent: CREATE ROLE IF NOT EXISTS pattern via DO block

-- =========================================================================
-- Application umbrella role (NOLOGIN container · pods inherit via GRANT)
-- =========================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bookflow_app') THEN
        CREATE ROLE bookflow_app NOLOGIN;
    END IF;
END $$;

-- =========================================================================
-- Per-pod login roles (Pods authenticate via these)
-- Master credential rotated separately via Secrets Manager rotation
-- =========================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'auth_pod')         THEN CREATE ROLE auth_pod         LOGIN PASSWORD 'CHANGE_ME_AUTH'        IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'inventory_svc')    THEN CREATE ROLE inventory_svc    LOGIN PASSWORD 'CHANGE_ME_INVENTORY'   IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'forecast_svc')     THEN CREATE ROLE forecast_svc     LOGIN PASSWORD 'CHANGE_ME_FORECAST'    IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'decision_svc')     THEN CREATE ROLE decision_svc     LOGIN PASSWORD 'CHANGE_ME_DECISION'    IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'intervention_svc') THEN CREATE ROLE intervention_svc LOGIN PASSWORD 'CHANGE_ME_INTERVENTION' IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'notification_svc') THEN CREATE ROLE notification_svc LOGIN PASSWORD 'CHANGE_ME_NOTIFICATION' IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard_svc')    THEN CREATE ROLE dashboard_svc    LOGIN PASSWORD 'CHANGE_ME_DASHBOARD'   IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'publish_watcher')  THEN CREATE ROLE publish_watcher  LOGIN PASSWORD 'CHANGE_ME_PUBLISH'     IN ROLE bookflow_app; END IF;
    -- Lambda actors (POS ingestor / spike detect / aladin sync)
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pos_ingestor')     THEN CREATE ROLE pos_ingestor     LOGIN PASSWORD 'CHANGE_ME_POS'         IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'spike_detect')     THEN CREATE ROLE spike_detect     LOGIN PASSWORD 'CHANGE_ME_SPIKE'       IN ROLE bookflow_app; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'aladin_sync')      THEN CREATE ROLE aladin_sync      LOGIN PASSWORD 'CHANGE_ME_ALADIN'      IN ROLE bookflow_app; END IF;
END $$;

-- =========================================================================
-- bookflow_app baseline: SELECT on all tables (read everywhere · write per role)
-- =========================================================================
GRANT USAGE ON SCHEMA public TO bookflow_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bookflow_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bookflow_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO bookflow_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO bookflow_app;

-- =========================================================================
-- Per-pod write privileges (least privilege)
-- =========================================================================

-- inventory-svc: single writer for inventory + reservations
GRANT INSERT, UPDATE, DELETE ON inventory       TO inventory_svc;
GRANT INSERT, UPDATE, DELETE ON reservations    TO inventory_svc;
GRANT INSERT                ON audit_log        TO inventory_svc;

-- forecast-svc: forecast cache writer (BQ -> RDS sync)
GRANT INSERT, UPDATE, DELETE ON forecast_cache  TO forecast_svc;
GRANT INSERT                ON audit_log        TO forecast_svc;

-- decision-svc: pending_orders creator + plan-daily 멱등성 (같은 snapshot_date 재호출 시 기존 plan rows cleanup)
GRANT INSERT, UPDATE, DELETE ON pending_orders   TO decision_svc;
GRANT DELETE                 ON order_approvals  TO decision_svc;
GRANT INSERT                 ON audit_log        TO decision_svc;

-- intervention-svc: approval + execute + returns finalizer + HQ master controls
GRANT INSERT, UPDATE        ON pending_orders   TO intervention_svc;
GRANT INSERT, UPDATE        ON order_approvals  TO intervention_svc;
GRANT INSERT, UPDATE        ON returns          TO intervention_svc;
GRANT INSERT                ON audit_log        TO intervention_svc;
-- HQ 도서 ON/OFF + 소진 모드 (FR-A6.1·A6.2): active + discontinue_* 컬럼만
GRANT UPDATE (active, discontinue_mode, discontinue_reason, discontinue_at, discontinue_by, reactivated_at)
                            ON books            TO intervention_svc;

-- notification-svc: notifications log writer
GRANT INSERT, UPDATE        ON notifications_log TO notification_svc;
GRANT INSERT                ON audit_log         TO notification_svc;

-- dashboard-svc: read only (fan-in HUB · WebSocket broker · no direct writes)
-- (already covered by bookflow_app SELECT)

-- auth-pod: OIDC self-provisioning · INSERT new users + UPDATE info on each login (Phase γ)
-- UPN 패턴 매핑 (2026-05-13) — role/scope_wh_id/scope_store_id 도 매 로그인 refresh.
GRANT SELECT                                            ON users     TO auth_pod;
GRANT INSERT                                            ON users     TO auth_pod;
GRANT UPDATE (email, display_name, last_login_at,
              role, scope_wh_id, scope_store_id)        ON users     TO auth_pod;
GRANT INSERT                                            ON audit_log TO auth_pod;

-- publish-watcher: new_book_requests writer (external publisher source)
GRANT INSERT, UPDATE        ON new_book_requests TO publish_watcher;
GRANT INSERT                ON audit_log         TO publish_watcher;
-- intervention-svc: HQ approves/rejects new book requests (FR-A1.4 / A11.1)
GRANT INSERT, UPDATE        ON new_book_requests TO intervention_svc;

-- pos-ingestor (Lambda): sales_realtime + inventory adjustment via reservations
GRANT INSERT                ON sales_realtime    TO pos_ingestor;
GRANT UPDATE                ON inventory         TO pos_ingestor;
GRANT INSERT, UPDATE        ON reservations      TO pos_ingestor;
GRANT INSERT                ON audit_log         TO pos_ingestor;

-- spike-detect (Lambda): spike_events + may trigger pending_orders
GRANT INSERT, UPDATE        ON spike_events      TO spike_detect;
GRANT INSERT                ON pending_orders    TO spike_detect;
GRANT INSERT                ON audit_log         TO spike_detect;

-- aladin-sync (Lambda): books + authors + publishers UPSERT
GRANT INSERT, UPDATE        ON books             TO aladin_sync;
GRANT INSERT, UPDATE        ON authors           TO aladin_sync;
GRANT INSERT, UPDATE        ON publishers        TO aladin_sync;
GRANT USAGE, SELECT         ON SEQUENCE authors_author_id_seq      TO aladin_sync;
GRANT USAGE, SELECT         ON SEQUENCE publishers_publisher_id_seq TO aladin_sync;
GRANT INSERT                ON audit_log         TO aladin_sync;

-- =========================================================================
-- Snapshot/KPI sync (CronJob roles) - reuse forecast_svc role for now
-- (Daily snapshot ingest is forecast_svc/decision_svc area · expanded later)
-- =========================================================================
GRANT INSERT, UPDATE        ON inventory_snapshot_daily TO forecast_svc;
GRANT INSERT, UPDATE        ON kpi_daily                TO forecast_svc;
