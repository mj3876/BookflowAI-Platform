-- 008_new_book_requests_publisher.sql
-- publisher-api(별도 VPC · CodeDeploy EC2)가 new_book_requests 에 INSERT 할 때 필요한 스키마.
--   publisher/backend/main.py 의 POST /api/v1/new-book-requests 가
--     · attachment_s3_key 컬럼 (첨부파일 S3 키)
--     · ON CONFLICT (isbn13) DO NOTHING  → isbn13 UNIQUE 제약
--   두 요소를 모두 사용 → 부재 시 INSERT 가 500 으로 실패.
-- 원본: BookFlowAI-Apps publisher/backend/migration.sql — ansible migration 체인에
--   미편입돼 rds-seed 가 적용한 적이 없던 것을 정식 migration 으로 승격.
-- idempotent: ADD COLUMN IF NOT EXISTS · 제약은 pg_constraint 존재 검사 후 추가.

BEGIN;

-- 1. 첨부파일 S3 키 컬럼 (publisher-api 경유 제출 시만 채워짐 · seed CSV 엔 없음 → default NULL)
ALTER TABLE new_book_requests
    ADD COLUMN IF NOT EXISTS attachment_s3_key TEXT;

COMMENT ON COLUMN new_book_requests.attachment_s3_key
    IS 'S3 키 (예: publisher-attachments/9791234567890/marketing.pdf). publisher-api 경유 제출 시만 존재.';

-- 2. ON CONFLICT (isbn13) DO NOTHING 을 위한 UNIQUE 제약
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'new_book_requests'::regclass
          AND contype = 'u'
          AND conname = 'new_book_requests_isbn13_key'
    ) THEN
        ALTER TABLE new_book_requests
            ADD CONSTRAINT new_book_requests_isbn13_key UNIQUE (isbn13);
    END IF;
END$$;

-- 3. schema_versions 마커
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v8', NOW(), 'new_book_requests attachment_s3_key 컬럼 + isbn13 UNIQUE (publisher-api INSERT 정합)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
