-- 010_publisher_books_backfill.sql
-- 2026-05-20 16시 KST POS 멈춤 사고 후속:
--   PUBLISHER_REQUEST 워크플로로 생성됐는데 가격 미입력으로 NULL price_sales 인 책 정리.
--   sim catalog 필터 (price > 0) 에서 TypeError 유발하여 crash loop 의 직접 원인.
-- 신규 가격 누락은 009 migration + Apps PR (`fix/publisher-price-required`) 로 차단.
-- 이 migration 은 기존 NULL row 1건 (예: `9791141604769` "테스트 도서") 의 정리.
-- 멱등: WHERE active=true AND price_sales IS NULL → 한 번 실행 후 row 없음.

BEGIN;

UPDATE books
   SET active             = false,
       discontinue_mode   = COALESCE(discontinue_mode, 'NONE'),
       discontinue_reason = 'AUTO_DEACTIVATE_NULL_PRICE_2026_05_20',
       discontinue_at     = NOW(),
       updated_at         = NOW()
 WHERE source       = 'PUBLISHER_REQUEST'
   AND price_sales IS NULL
   AND active       = true;

INSERT INTO schema_versions (version, applied_at, description)
VALUES ('v10', NOW(), 'PUBLISHER_REQUEST NULL price books deactivate (16시 사고 정리)')
ON CONFLICT (version) DO NOTHING;

COMMIT;
