from google.cloud import bigquery

proj = "project-8ab6bf05-54d2-4f5d-b8d"
ds   = "bookflow_dw"
isbn = "9791607337545"
lead = 30

client = bigquery.Client(project=proj, location="asia-northeast1")

def p(t):
    return f"`{proj}.{ds}.{t}`"

print(f"=== new-book-inference 테스트: isbn13={isbn} ===\n")

# 책 메타데이터 확인
meta = list(client.query(
    f"SELECT isbn13, author, category_id, price_tier, sales_point, item_page FROM {p('books_static')} WHERE isbn13 = '{isbn}'"
).result())
if not meta:
    print("ERROR: books_static에 해당 ISBN 없음")
    exit(1)
m = meta[0]
print(f"도서 메타: author={m.author}  category={m.category_id}  price_tier={m.price_tier}  sales_point={m.sales_point}")
print()

# ML.PREDICT 실행 (CF 내부 로직 동일)
sql = f"""
SELECT
  slm.store_id,
  ls.wh_id,
  GREATEST(pred.predicted_label, 0.0)                              AS predicted_daily_demand,
  CAST(ROUND(GREATEST(pred.predicted_label, 0.0) * {lead}) AS INT64) AS predicted_30d_qty
FROM ML.PREDICT(
  MODEL {p('bookflow_new_books_forecast')},
  (
    SELECT
      b.category_id,
      b.price_tier,
      COALESCE(b.sales_point, 0)                              AS sales_point,
      CAST(COALESCE(b.is_bestseller_flag, FALSE) AS INT64)   AS is_bestseller_flag,
      COALESCE(b.author_experience_years, 0)                  AS author_experience_years,
      COALESCE(b.author_past_books_count, 0)                  AS author_past_books_count,
      COALESCE(b.item_page, 0)                                AS item_page,
      slm.store_id
    FROM {p('books_static')} b
    CROSS JOIN {p('store_location_map')} slm
    WHERE b.isbn13 = '{isbn}'
  )
) AS pred
JOIN {p('store_location_map')} slm ON slm.store_id = pred.store_id
JOIN {p('locations_static')} ls    ON ls.location_id = slm.inventory_location_id
ORDER BY slm.store_id
"""

rows = list(client.query(sql).result())
print("매장별 예측:")
wh = {}
for r in rows:
    print(f"  store_id={r.store_id}  wh_id={r.wh_id}  daily={r.predicted_daily_demand:.3f}  30d={r.predicted_30d_qty}권")
    wh[r.wh_id] = wh.get(r.wh_id, 0) + r.predicted_30d_qty

print()
print("=== CF 응답 예상값 ===")
print(f'  isbn13:   {isbn}')
print(f'  wh1_qty:  {wh.get(1, 0)} 권')
print(f'  wh2_qty:  {wh.get(2, 0)} 권')
print(f'  lead_days: {lead}')
print(f'  source:   new_book_model')
