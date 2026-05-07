-- Migration 002 · 2026-05-06
-- A4 (FR-A6.8): 본사 마스터가 반품 거부 시 rejected_at + reject_reason 채움
-- Idempotent: ADD COLUMN IF NOT EXISTS

ALTER TABLE returns ADD COLUMN IF NOT EXISTS rejected_at   TIMESTAMPTZ;
ALTER TABLE returns ADD COLUMN IF NOT EXISTS reject_reason VARCHAR(200);

-- intervention-svc 가 reject 시 status='REJECTED' + 두 컬럼 UPDATE
GRANT UPDATE (status, rejected_at, reject_reason) ON returns TO intervention_svc;

-- Mark version
INSERT INTO schema_versions (version, description, applied_at)
VALUES ('002', 'returns reject columns + intervention_svc UPDATE grant', NOW())
ON CONFLICT (version) DO NOTHING;
