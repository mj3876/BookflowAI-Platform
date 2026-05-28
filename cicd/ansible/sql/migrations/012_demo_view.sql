-- 012_demo_view.sql · CI/CD 시연용 view (매장별 재고 요약)
-- 영상 시연: PR 머지 → GHA → SSM → Ansible CN → psql 자동 적용 흐름 시각화
CREATE OR REPLACE VIEW vw_branch_inventory_summary AS
SELECT
    l.location_id,
    l.name      AS location_name,
    l.region,
    l.location_type,
    COUNT(DISTINCT i.isbn13) AS distinct_books,
    SUM(i.on_hand)            AS total_qty
FROM locations l
LEFT JOIN inventory i ON i.location_id = l.location_id
GROUP BY l.location_id, l.name, l.region, l.location_type;

COMMENT ON VIEW vw_branch_inventory_summary IS 'CI/CD 시연용 view — 매장별 재고 요약 (locations.name / inventory.on_hand 기준)';
