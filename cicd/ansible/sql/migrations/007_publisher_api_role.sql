-- 007_publisher_api_role.sql
-- publisher 채널(별도 VPC · CodeDeploy EC2) 전용 RDS login role.
--   publisher/backend/main.py 가 이 role 로 RDS 접속 — 출판사 신간 신청 수신.
--   POST /api/v1/new-book-requests → books · new_book_requests · audit_log INSERT.
--   SELECT 는 bookflow_app umbrella role 상속 (003_grants.sql).
--
-- Secrets Manager `bookflow/publisher-api` 의 RDS_USER=publisher_api · RDS_PASSWORD 가
-- 아래 비밀번호와 일치해야 함 (학습 환경 placeholder · 운영 시 rotation).
-- idempotent: CREATE ROLE IF NOT EXISTS 패턴.

BEGIN;

-- 1. publisher_api login role (bookflow_app 상속 → 전 테이블 SELECT)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'publisher_api') THEN
        CREATE ROLE publisher_api LOGIN PASSWORD 'Bookflow-Pub-2026' IN ROLE bookflow_app;
    END IF;
END $$;

-- 2. write 권한 — publisher 가 INSERT 하는 3 테이블만 (최소 권한)
GRANT INSERT ON books             TO publisher_api;
GRANT INSERT ON new_book_requests TO publisher_api;
GRANT INSERT ON audit_log         TO publisher_api;

-- 3. SERIAL 시퀀스 (new_book_requests.id · audit_log.log_id INSERT 용)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO publisher_api;

-- 4. schema_versions 마커
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v7', NOW(), 'publisher_api RDS login role + INSERT grants (publisher 채널)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
