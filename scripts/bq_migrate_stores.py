"""
BQ store 정합 마이그레이션 스크립트
- locations_static: 16행 (RDS 정합, name/region 추가)
- store_location_map: 14행 (store_id=location_id)
- sales_fact: store_id 재매핑 + 신규 지점 2개(광화문 L, 대구동성 L) 추가
- inventory_daily: location_id 재매핑 + 신규 지점 추가

OLD BQ → NEW RDS 매핑:
  store 1(강남L)    → 1(강남L)       그대로
  store 2(홍대M)    → 4(홍대M)
  store 3(잠실M)    → 3(잠실M)       그대로
  store 4(신촌S)    → 5(신촌S)
  store 5(수원S)    → 6(용산S)
  store 6(WH1온라인)→ 13(수도권온라인)
  store 7(부산L)    → 7(부산서면L)   그대로
  store 8(대구M)    → 9(울산삼산M)
  store 9(광주M)    → 10(대구교대M)
  store 10(대전S)   → 11(부산센텀S)
  store 11(울산S)   → 12(포항양덕S)
  store 12(WH2온라인)→ 14(영남온라인)
  NEW store 2(광화문L, wh1): old store 1(강남L) 데이터 복제
  NEW store 8(대구동성L, wh2): old store 7(부산L) 데이터 복제
"""
from google.cloud import bigquery

proj = "project-8ab6bf05-54d2-4f5d-b8d"
ds   = "bookflow_dw"
client = bigquery.Client(project=proj, location="asia-northeast1")

def run(sql, desc):
    print(f"  → {desc} ...")
    client.query(sql, location="asia-northeast1").result()
    print(f"    완료")

def replace_clustered_table(table, build_sql, desc):
    """클러스터링된 테이블은 tmp→drop→copy→drop_tmp 방식으로 교체"""
    tmp = f"{proj}.{ds}.{table}_mig_tmp"
    dst = f"{proj}.{ds}.{table}"
    print(f"  → {desc} ...")
    client.query(f"CREATE OR REPLACE TABLE `{tmp}` AS {build_sql}", location="asia-northeast1").result()
    client.query(f"DROP TABLE IF EXISTS `{dst}`", location="asia-northeast1").result()
    client.copy_table(tmp, dst).result()
    client.query(f"DROP TABLE IF EXISTS `{tmp}`", location="asia-northeast1").result()
    print(f"    완료")

# ── 1. locations_static 교체 ──────────────────────────────────────────────────
print("\n[1/4] locations_static 교체")
run(f"DROP TABLE IF EXISTS `{proj}.{ds}.locations_static`", "기존 테이블 삭제")
run(f"""
CREATE TABLE `{proj}.{ds}.locations_static` AS
SELECT * FROM UNNEST([
  STRUCT(1  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'강남점'        AS name,'L'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(2  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'광화문점'      AS name,'L'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(3  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'잠실점'        AS name,'M'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(4  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'홍대점'        AS name,'M'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(5  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'신촌점'        AS name,'S'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(6  AS location_id,'STORE_OFFLINE' AS location_type,1 AS wh_id,'용산점'        AS name,'S'  AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(7  AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'부산 서면점'   AS name,'L'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(8  AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'대구 동성점'   AS name,'L'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(9  AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'울산 삼산점'   AS name,'M'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(10 AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'대구 교대점'   AS name,'M'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(11 AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'부산 센텀점'   AS name,'S'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(12 AS location_id,'STORE_OFFLINE' AS location_type,2 AS wh_id,'포항 양덕점'   AS name,'S'  AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(13 AS location_id,'STORE_ONLINE'  AS location_type,1 AS wh_id,'수도권 온라인' AS name,'L'  AS size,'수도권' AS region,TRUE  AS is_virtual,TRUE AS active),
  STRUCT(14 AS location_id,'STORE_ONLINE'  AS location_type,2 AS wh_id,'영남 온라인'   AS name,'L'  AS size,'영남'   AS region,TRUE  AS is_virtual,TRUE AS active),
  STRUCT(15 AS location_id,'WH'            AS location_type,1 AS wh_id,'수도권 거점창고' AS name,'XL' AS size,'수도권' AS region,FALSE AS is_virtual,TRUE AS active),
  STRUCT(16 AS location_id,'WH'            AS location_type,2 AS wh_id,'영남 거점창고'   AS name,'XL' AS size,'영남'   AS region,FALSE AS is_virtual,TRUE AS active)
])
""", "locations_static 16행 삽입")

# ── 2. store_location_map 교체 ────────────────────────────────────────────────
print("\n[2/4] store_location_map 교체")
run(f"DROP TABLE IF EXISTS `{proj}.{ds}.store_location_map`", "기존 테이블 삭제")
run(f"""
CREATE TABLE `{proj}.{ds}.store_location_map` AS
SELECT * FROM UNNEST([
  STRUCT(1  AS store_id,1  AS location_id,1  AS inventory_location_id,'1->1->1'    AS mapping_rule),
  STRUCT(2  AS store_id,2  AS location_id,2  AS inventory_location_id,'2->2->2'    AS mapping_rule),
  STRUCT(3  AS store_id,3  AS location_id,3  AS inventory_location_id,'3->3->3'    AS mapping_rule),
  STRUCT(4  AS store_id,4  AS location_id,4  AS inventory_location_id,'4->4->4'    AS mapping_rule),
  STRUCT(5  AS store_id,5  AS location_id,5  AS inventory_location_id,'5->5->5'    AS mapping_rule),
  STRUCT(6  AS store_id,6  AS location_id,6  AS inventory_location_id,'6->6->6'    AS mapping_rule),
  STRUCT(7  AS store_id,7  AS location_id,7  AS inventory_location_id,'7->7->7'    AS mapping_rule),
  STRUCT(8  AS store_id,8  AS location_id,8  AS inventory_location_id,'8->8->8'    AS mapping_rule),
  STRUCT(9  AS store_id,9  AS location_id,9  AS inventory_location_id,'9->9->9'    AS mapping_rule),
  STRUCT(10 AS store_id,10 AS location_id,10 AS inventory_location_id,'10->10->10' AS mapping_rule),
  STRUCT(11 AS store_id,11 AS location_id,11 AS inventory_location_id,'11->11->11' AS mapping_rule),
  STRUCT(12 AS store_id,12 AS location_id,12 AS inventory_location_id,'12->12->12' AS mapping_rule),
  STRUCT(13 AS store_id,13 AS location_id,15 AS inventory_location_id,'13->13->15' AS mapping_rule),
  STRUCT(14 AS store_id,14 AS location_id,16 AS inventory_location_id,'14->14->16' AS mapping_rule)
])
""", "store_location_map 14행 삽입")

# ── 3. sales_fact store_id 재매핑 ─────────────────────────────────────────────
print("\n[3/4] sales_fact store_id 재매핑")
STORE_MAP = {1:1, 2:4, 3:3, 4:5, 5:6, 6:13, 7:7, 8:9, 9:10, 10:11, 11:12, 12:14}
WH_MAP    = {1:1, 2:1, 3:1, 4:1, 5:1, 6:1, 7:2, 8:2, 9:2, 10:2, 11:2, 12:2, 13:1, 14:2}

store_case = " ".join(f"WHEN {o} THEN {n}" for o, n in STORE_MAP.items())
wh_case    = " ".join(f"WHEN {o} THEN {n}" for o, n in WH_MAP.items())

replace_clustered_table("sales_fact", f"""
SELECT
  sale_date, isbn13,
  CASE store_id {store_case} ELSE store_id END AS store_id,
  CASE store_id {wh_case}    ELSE wh_id   END AS wh_id,
  channel, qty_sold, revenue, avg_price, tx_count
FROM `{proj}.{ds}.sales_fact`
UNION ALL
SELECT sale_date, isbn13, 2  AS store_id, 1 AS wh_id, channel, qty_sold, revenue, avg_price, tx_count
FROM `{proj}.{ds}.sales_fact` WHERE store_id = 1
UNION ALL
SELECT sale_date, isbn13, 8  AS store_id, 2 AS wh_id, channel, qty_sold, revenue, avg_price, tx_count
FROM `{proj}.{ds}.sales_fact` WHERE store_id = 7
""", "sales_fact 재매핑 + 신규 지점(광화문L, 대구동성L) 추가")

# ── 4. inventory_daily location_id 재매핑 ─────────────────────────────────────
print("\n[4/4] inventory_daily location_id 재매핑")
LOC_MAP  = {3:1, 4:4, 5:3, 6:5, 7:6, 9:7, 10:9, 11:10, 12:11, 13:12}
loc_case = " ".join(f"WHEN {o} THEN {n}" for o, n in LOC_MAP.items())
loc_in   = ",".join(str(k) for k in LOC_MAP)

replace_clustered_table("inventory_daily", f"""
SELECT
  snapshot_date, isbn13,
  CASE location_id {loc_case} ELSE location_id END AS location_id,
  on_hand, reserved_qty, safety_stock
FROM `{proj}.{ds}.inventory_daily`
WHERE location_id IN ({loc_in})
UNION ALL
SELECT snapshot_date, isbn13, 2 AS location_id, on_hand, reserved_qty, safety_stock
FROM `{proj}.{ds}.inventory_daily` WHERE location_id = 3
UNION ALL
SELECT snapshot_date, isbn13, 8 AS location_id, on_hand, reserved_qty, safety_stock
FROM `{proj}.{ds}.inventory_daily` WHERE location_id = 9
""", "inventory_daily 재매핑 + 신규 지점(광화문L, 대구동성L) 추가")

print("\n✅ BQ 마이그레이션 완료")

# ── 결과 검증 ──────────────────────────────────────────────────────────────────
print("\n=== 검증 ===")
for tbl, col in [("locations_static","location_id"), ("store_location_map","store_id")]:
    cnt = list(client.query(f"SELECT COUNT(*) AS n FROM `{proj}.{ds}.{tbl}`").result())[0][0]
    print(f"  {tbl}: {cnt}행")

for tbl, col in [("sales_fact","store_id"), ("inventory_daily","location_id")]:
    rows = list(client.query(
        f"SELECT {col}, COUNT(*) AS n FROM `{proj}.{ds}.{tbl}` GROUP BY {col} ORDER BY {col}"
    ).result())
    ids = [r[0] for r in rows]
    print(f"  {tbl} {col}s: {ids}")
