-- 005_wh_to_store_order_type.sql
-- 4-stage cascade 정합:
--   Stage 0 (신규): WH_TO_STORE — 자기 wh 본체 → 자기 권역 매장 보충
--   Stage 1       : REBALANCE   — 같은 wh 매장 ↔ 매장
--   Stage 2       : WH_TRANSFER — 다른 권역 WH 본체 ↔ WH 본체
--   Stage 3       : PUBLISHER_ORDER — 외부 발주 → wh
--
-- pending_orders.order_type 은 VARCHAR(20) (CHECK 제약 없음) 이므로 신규 값 도입에 DDL 불필요.
-- 본 migration 은 schema_versions 마커 + 정합 안내 코멘트만 기록 (DB no-op).
-- 향후 ENUM 으로 전환 시 ALTER TYPE 추가 위치는 여기.

BEGIN;

-- 1. order_type 정합 명시 (코멘트만 · DDL no-op)
COMMENT ON COLUMN pending_orders.order_type IS
  'WH_TO_STORE (Stage 0) | REBALANCE (Stage 1) | WH_TRANSFER (Stage 2) | PUBLISHER_ORDER (Stage 3)';

-- 2. schema_versions 마커
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v5', NOW(), 'pending_orders.order_type WH_TO_STORE (Stage 0) 정합 코멘트')
ON CONFLICT (version) DO NOTHING;

COMMIT;
