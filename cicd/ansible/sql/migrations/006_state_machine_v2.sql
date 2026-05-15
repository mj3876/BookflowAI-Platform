-- 006_state_machine_v2.sql
-- 4-step state machine 정합: PENDING → APPROVED → IN_TRANSIT → EXECUTED + REJECTED(rejection_stage)
--   - 계획 승인 (양측 ✓) 과 실제 입출고 (source 발송 · target 수령) 분리
--   - Option B (APPROVED 시 source -qty 자동) 폐기 → /orders/{id}/dispatch endpoint 로 명시적 변동
--   - inventory writer 단일화 (inventory-svc /adjust)
--   - cross-user 정합성: 모든 status 전환이 atomic + race-safe (ON CONFLICT + COUNT subquery)
--
-- 신규 컬럼 5개:
--   dispatched_at        — source 발송 시점 (status IN_TRANSIT 진입)
--   dispatched_by        — 발송자 (user_id)
--   executed_by          — 수령자 (user_id) · executed_at 은 이미 존재
--   rejection_stage      — REJECTED 시 어느 status 에서 거부됐는지 ('PENDING'|'APPROVED'|'IN_TRANSIT')
--   expected_arrival_at  — forecast_rationale.expected_arrival_date 컬럼 승격 (캘린더 cell count 인덱스)

BEGIN;

-- =========================================================================
-- 1. 컬럼 추가 (idempotent)
-- =========================================================================
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS dispatched_at       TIMESTAMPTZ;
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS dispatched_by       VARCHAR(50);
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS executed_by         VARCHAR(50);
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS rejection_stage     VARCHAR(20);
ALTER TABLE pending_orders ADD COLUMN IF NOT EXISTS expected_arrival_at DATE;

-- =========================================================================
-- 2. expected_arrival_at backfill (forecast_rationale JSONB → 컬럼 승격)
--    기존 row 정합: JSONB 의 expected_arrival_date 가 있으면 그대로, 없으면 created_at + LEAD_DAYS
-- =========================================================================
UPDATE pending_orders
   SET expected_arrival_at = (forecast_rationale->>'expected_arrival_date')::DATE
 WHERE expected_arrival_at IS NULL
   AND forecast_rationale ? 'expected_arrival_date';

-- forecast_rationale 에 expected_arrival_date 없는 row: order_type 별 LEAD_DAYS 로 추정
UPDATE pending_orders
   SET expected_arrival_at = (created_at::DATE + CASE order_type
       WHEN 'REBALANCE'       THEN 1
       WHEN 'WH_TO_STORE'     THEN 1
       WHEN 'WH_TRANSFER'     THEN 2
       WHEN 'PUBLISHER_ORDER' THEN 4
       ELSE 1
     END)
 WHERE expected_arrival_at IS NULL;

-- =========================================================================
-- 3. 기존 EXECUTED row 의 dispatched_at backfill (CHECK 충족 위해)
--    EXECUTED 면 IN_TRANSIT 거쳤다고 가정 — dispatched_at 추정 (executed_at - 1h, 없으면 approved_at)
--    AUTO_EXECUTED 도 동일 패턴 (urgent/critical 자동 발송)
-- =========================================================================
UPDATE pending_orders
   SET dispatched_at = COALESCE(executed_at - INTERVAL '1 hour', approved_at, created_at)
 WHERE dispatched_at IS NULL
   AND status IN ('EXECUTED','AUTO_EXECUTED');

-- =========================================================================
-- 4. 기존 REJECTED row 의 rejection_stage backfill
--    approved_at 이 있으면 'APPROVED' (출고 전 거부) · 아니면 'PENDING' (협의 단계 거부)
-- =========================================================================
UPDATE pending_orders
   SET rejection_stage = CASE
     WHEN approved_at IS NOT NULL THEN 'APPROVED'
     ELSE 'PENDING'
   END
 WHERE rejection_stage IS NULL
   AND status = 'REJECTED';

-- =========================================================================
-- 5. 불변식 CHECK (race-safe state machine 보장)
--    drop-then-add 패턴 — idempotent 매일 재실행
-- =========================================================================
ALTER TABLE pending_orders DROP CONSTRAINT IF EXISTS chk_dispatched_when;
ALTER TABLE pending_orders ADD  CONSTRAINT chk_dispatched_when
  CHECK (
    (status IN ('IN_TRANSIT','EXECUTED','AUTO_EXECUTED')) = (dispatched_at IS NOT NULL)
    OR (status = 'REJECTED' AND rejection_stage = 'IN_TRANSIT' AND dispatched_at IS NOT NULL)
    OR (status = 'REJECTED' AND rejection_stage IN ('PENDING','APPROVED'))
  );

ALTER TABLE pending_orders DROP CONSTRAINT IF EXISTS chk_executed_when;
ALTER TABLE pending_orders ADD  CONSTRAINT chk_executed_when
  CHECK ((status IN ('EXECUTED','AUTO_EXECUTED')) = (executed_at IS NOT NULL));

ALTER TABLE pending_orders DROP CONSTRAINT IF EXISTS chk_rejection_stage_when;
ALTER TABLE pending_orders ADD  CONSTRAINT chk_rejection_stage_when
  CHECK ((status = 'REJECTED') = (rejection_stage IS NOT NULL));

ALTER TABLE pending_orders DROP CONSTRAINT IF EXISTS chk_rejection_stage_values;
ALTER TABLE pending_orders ADD  CONSTRAINT chk_rejection_stage_values
  CHECK (rejection_stage IS NULL OR rejection_stage IN ('PENDING','APPROVED','IN_TRANSIT'));

-- =========================================================================
-- 6. Index — 캘린더 cell count 최적화 (expected_arrival_at 기반 group by)
-- =========================================================================
CREATE INDEX IF NOT EXISTS idx_pending_orders_arrival
  ON pending_orders (expected_arrival_at, status);

CREATE INDEX IF NOT EXISTS idx_pending_orders_dispatched_at
  ON pending_orders (dispatched_at) WHERE dispatched_at IS NOT NULL;

-- =========================================================================
-- 7. intervention_svc 신규 컬럼 UPDATE 권한
-- =========================================================================
GRANT UPDATE (dispatched_at, dispatched_by, executed_by, rejection_stage, expected_arrival_at)
   ON pending_orders TO intervention_svc;

-- =========================================================================
-- 8. schema_versions 마커
-- =========================================================================
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v6', NOW(), 'state machine v2 — IN_TRANSIT separation (dispatched_at/executed_by/rejection_stage/expected_arrival_at) + CHECK invariants')
ON CONFLICT (version) DO NOTHING;

COMMIT;
