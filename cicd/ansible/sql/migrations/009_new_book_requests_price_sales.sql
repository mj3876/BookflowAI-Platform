-- 009_new_book_requests_price_sales.sql
-- publisher 신간요청 시 price_sales 필수화. 2026-05-20 16:00 KST 사고 근본 fix:
--   기존 NULL price_sales 책 (`9791141604769` "테스트 도서") 이 ecs-sim catalog 필터 의
--   `price_sales > 0` 비교에서 TypeError 유발 → sim crash loop → POS Kinesis 멈춤.
--   Apps 레포 `fix/publisher-price-required` 브랜치 (publisher/backend/migration.sql) 와 정합 미러.
-- 원본: BookFlowAI-Apps publisher/backend/migration.sql — ansible migration 체인 정식 승격.
-- idempotent: ADD COLUMN IF NOT EXISTS · backfill 후 information_schema 검사 시 NOT NULL 승격.

BEGIN;

-- 1. price_sales 컬럼 추가 (일단 NULL 허용 — backfill 위해)
ALTER TABLE new_book_requests
    ADD COLUMN IF NOT EXISTS price_sales INTEGER;

COMMENT ON COLUMN new_book_requests.price_sales
    IS '신간 도서 정가 (원). publisher 포털 제출 시 필수 (gt=0). books.price_sales 로 전파됨.';

-- 2. 기존 NULL row backfill (1 = placeholder · 운영자 정상가 보정 권장)
UPDATE new_book_requests
   SET price_sales = 1
 WHERE price_sales IS NULL;

-- 3. NOT NULL 승격 (현재 nullable 인 경우만 — 멱등)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'new_book_requests'
          AND column_name = 'price_sales'
          AND is_nullable = 'YES'
    ) THEN
        ALTER TABLE new_book_requests ALTER COLUMN price_sales SET NOT NULL;
    END IF;
END$$;

-- 4. schema_versions 마커
INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v9', NOW(), 'new_book_requests price_sales NOT NULL (publisher 16시 사고 근본 fix)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
