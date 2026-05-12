from google.cloud import bigquery

proj = "project-8ab6bf05-54d2-4f5d-b8d"
ds   = "bookflow_dw"
client = bigquery.Client(project=proj)

for tbl, col in [("locations_static", "location_id"), ("store_location_map", "store_id")]:
    cnt = list(client.query(
        f"SELECT COUNT(*) AS n FROM `{proj}.{ds}.{tbl}`",
        location="asia-northeast1"
    ).result())[0][0]
    print(f"{tbl}: {cnt}행")

for tbl, col in [("sales_fact", "store_id"), ("inventory_daily", "location_id")]:
    rows = list(client.query(
        f"SELECT {col}, COUNT(*) AS n FROM `{proj}.{ds}.{tbl}` GROUP BY {col} ORDER BY {col}",
        location="asia-northeast1"
    ).result())
    ids   = [r[0] for r in rows]
    total = sum(r[1] for r in rows)
    print(f"{tbl} {col}s: {ids}  total={total:,}")

# locations_static 내용 확인
print("\n=== locations_static ===")
rows = list(client.query(
    f"SELECT location_id, name, wh_id, size, region FROM `{proj}.{ds}.locations_static` ORDER BY location_id",
    location="asia-northeast1"
).result())
for r in rows:
    print(f"  {r.location_id:2d} {r.name:<12} wh={r.wh_id} size={r.size} region={r.region}")
