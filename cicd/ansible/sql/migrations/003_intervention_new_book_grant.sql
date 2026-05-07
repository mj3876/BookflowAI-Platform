-- Migration 003 · 2026-05-06
-- intervention-svc 가 new_book_requests 의 status / fetched_at / approved_at 을 UPDATE 해야 하는데
-- 003_grants.sql baseline 에서 publish_watcher 만 명시되어 있었음.
-- HQ 신간 편입 결정 (approve / reject) 503 InsufficientPrivilege 원인.

GRANT INSERT, UPDATE ON new_book_requests TO intervention_svc;

INSERT INTO schema_versions (version, description, applied_at)
VALUES ('003', 'intervention_svc UPDATE on new_book_requests', NOW())
ON CONFLICT (version) DO NOTHING;
