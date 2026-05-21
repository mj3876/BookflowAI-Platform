-- 011_new_book_requests_requester_email.sql
-- 시나리오 2 (신간 정상플로우): 출판사 신청 시 결과 통보용 이메일 + 신생 출판사 등록 지원.
--   1) new_book_requests.requester_email — 승인 시 출판사에게 발주명세 메일(1-7)을 보낼 수신처.
--      publisher-api(POST) / publisher-watcher(sync) 가 채우고, intervention-svc 가 승인 시 읽어
--      notification-svc → Logic App 으로 출판사 발주명세 메일 발송.
--   2) publishers INSERT/UPDATE 권한을 publisher_api 에 부여 — 신생 출판사(코드 미보유)는
--      publisher-api 가 publisher_name 으로 publishers 를 ON CONFLICT (name) upsert 하여 id 확보.
-- idempotent: ADD COLUMN IF NOT EXISTS · GRANT 는 반복 안전.

BEGIN;

-- 1. 결과 통보용 출판사 담당자 이메일
ALTER TABLE new_book_requests
    ADD COLUMN IF NOT EXISTS requester_email VARCHAR(200);

COMMENT ON COLUMN new_book_requests.requester_email
    IS '출판사 담당자 이메일 (결과 통보용). 승인 시 발주명세 메일 수신처 (시나리오 2 · 1-7).';

-- 2. 신생 출판사 upsert 권한 (publisher-api POST 경로)
--    publishers.name UNIQUE → ON CONFLICT (name) DO UPDATE 에 INSERT + UPDATE 필요.
GRANT INSERT, UPDATE ON publishers TO publisher_api;
-- publishers.publisher_id SERIAL 시퀀스 (007 에서 ALL SEQUENCES 부여됐으나 명시 보강)
GRANT USAGE, SELECT ON SEQUENCE publishers_publisher_id_seq TO publisher_api;

-- 3. schema_versions 마커
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('011', NOW(), 'new_book_requests.requester_email + publishers upsert grant (신간 출판사 이메일)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
