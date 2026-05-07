-- Migration 001 · baseline marker
-- 001_tables.sql + 002_indexes.sql + 003_grants.sql 의 기존 baseline 을 기록.
-- 새로운 schema 변경은 migrations/00N_*.sql 에 ALTER 로 추가.
-- Idempotent re-run 가능.

CREATE TABLE IF NOT EXISTS schema_versions (
    version       VARCHAR(10) PRIMARY KEY,
    description   TEXT,
    applied_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO schema_versions (version, description, applied_at)
VALUES ('001', 'initial baseline · 001_tables + 002_indexes + 003_grants', NOW())
ON CONFLICT (version) DO NOTHING;
