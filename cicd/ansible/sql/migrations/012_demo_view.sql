-- 012_demo_view.sql · CI/CD 시연용 view (매장별 재고 요약)
-- 영상 시연: PR 머지 → GHA → SSM → Ansible CN → psql 자동 적용 흐름 시각화
CREATE OR REPLACE VIEW vw_branch_inventory_summary AS
SELECT
    l.location_id,
    l.location_name,
    COUNT(DISTINCT i.book_id) AS distinct_books,
    SUM(i.qty)                AS total_qty
FROM locations l
LEFT JOIN inventory i ON i.location_id = l.location_id
GROUP BY l.location_id, l.location_name;

COMMENT ON VIEW vw_branch_inventory_summary IS 'CI/CD 시연용 view — 매장별 재고 요약';
