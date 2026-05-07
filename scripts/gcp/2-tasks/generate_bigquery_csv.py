from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from faker import Faker


PROJECT_ID = "project-8ab6bf05-54d2-4f5d-b8d"
DATASET_ID = "bookflow_dw"
DEFAULT_WORKBOOK = Path(r"C:\Users\1\Downloads\V3_BOOKFLOW_Data_Schema.xlsx")
DEFAULT_DDL = Path(__file__).resolve().parents[3] / "infra" / "gcp" / "00-foundation" / "bookflow_bigquery_ddl.sql"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

TYPE_MAP = {
    "INT64": "int",
    "STRING": "str",
    "DATE": "date",
    "NUMERIC": "float",
    "BOOL": "bool",
    "BOOLEAN": "bool",
    "TIMESTAMP": "timestamp",
    "JSON": "json",
}

TABLE_ORDER = [
    "books_static",
    "locations_static",
    "authors",
    "publishers",
    "warehouses",
    "books",
    "locations",
    "sales_fact",
    "inventory_daily",
    "features",
    "forecast_results",
    "inventory",
    "reservations",
    "pending_orders",
    "order_approvals",
    "returns",
    "audit_log",
    "users",
    "forecast_cache",
    "new_book_requests",
    "spike_events",
    "notifications_log",
    "sales_realtime",
    "inventory_snapshot_daily",
    "kpi_daily",
]

CATEGORIES = [
    (101, "Literature/Fiction"),
    (102, "Business/Economics"),
    (103, "Humanities/Society"),
    (104, "Science/Technology"),
    (105, "Children"),
    (106, "Self Development"),
]
REGIONS = ["Metro", "Gangwon", "Chungcheong", "Honam", "Yeongnam", "Jeju"]
SEASONS = ["SPRING", "SUMMER", "FALL", "WINTER"]
ORDER_TYPES = ["PURCHASE", "REDISTRIBUTION", "RETURN"]
ORDER_STATUS = ["PENDING", "APPROVED", "REJECTED", "EXECUTED"]


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    bq_type: str
    py_type: str
    description: str = ""


@dataclass
class TableSpec:
    name: str
    columns: list[ColumnSpec]
    row_count: int
    description: str = ""


class SyntheticBookflowGenerator:
    def __init__(self, ddl_path: Path, workbook_path: Path, output_dir: Path, seed: int) -> None:
        self.ddl_path = ddl_path
        self.workbook_path = workbook_path
        self.output_dir = output_dir
        self.fake = Faker("ko_KR")
        Faker.seed(seed)
        random.seed(seed)
        self.today = date.today()
        self.schemas = parse_ddl(ddl_path)
        self.excel_meta = read_excel_metadata(workbook_path, set(self.schemas))
        self.tables = self._build_table_specs()
        self.cache: dict[str, pd.DataFrame] = {}

    def run(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for table_name in ordered_tables(self.tables):
            df = self.generate_table(table_name)
            self.cache[table_name] = df
            df.to_csv(self.output_dir / f"{table_name}.csv", index=False, encoding="utf-8-sig")
            print(f"wrote {len(df):>6} rows -> {self.output_dir / f'{table_name}.csv'}")

    def _build_table_specs(self) -> dict[str, TableSpec]:
        specs: dict[str, TableSpec] = {}
        for table_name, columns in self.schemas.items():
            meta = self.excel_meta.get(table_name, {})
            row_count = int(meta.get("row_count") or default_row_count(table_name))
            descriptions = meta.get("columns", {})
            enriched = [
                ColumnSpec(
                    name=col.name,
                    bq_type=col.bq_type,
                    py_type=col.py_type,
                    description=descriptions.get(col.name, col.description),
                )
                for col in columns
            ]
            specs[table_name] = TableSpec(
                name=table_name,
                columns=enriched,
                row_count=row_count,
                description=str(meta.get("description") or ""),
            )
        return specs

    def generate_table(self, table_name: str) -> pd.DataFrame:
        specialized = getattr(self, f"_generate_{table_name}", None)
        if specialized:
            return coerce_frame(specialized(), self.tables[table_name])
        return coerce_frame(self._generate_generic(table_name), self.tables[table_name])

    def _generate_books_static(self) -> pd.DataFrame:
        rows = []
        used: set[str] = set()
        for _ in range(self.tables["books_static"].row_count):
            isbn = make_isbn13(used)
            category_id, category_name = random.choice(CATEGORIES)
            price_standard = random.randrange(9000, 38000, 500)
            discount_rate = random.uniform(0.05, 0.18)
            price_sales = int(round(price_standard * (1 - discount_rate), -2))
            debut_year = random.randint(1980, self.today.year - 1)
            sales_point = random.randint(50, 100000)
            rows.append(
                {
                    "isbn13": isbn,
                    "category_id": category_id,
                    "category_name": category_name,
                    "publisher": self.fake.company(),
                    "author": self.fake.name(),
                    "price_standard": price_standard,
                    "price_sales": price_sales,
                    "price_tier": price_tier(price_standard),
                    "sales_point": sales_point,
                    "item_page": random.randint(90, 850),
                    "is_bestseller_flag": sales_point >= 70000,
                    "author_past_books_count": random.randint(0, 35),
                    "author_debut_year": debut_year,
                    "author_experience_years": self.today.year - debut_year,
                }
            )
        return pd.DataFrame(rows)

    def _generate_locations_static(self) -> pd.DataFrame:
        target = max(self.tables["locations_static"].row_count, 14)
        rows = [
            {"location_id": 1, "location_type": "WH", "wh_id": 1, "size": "L", "is_virtual": False},
            {"location_id": 2, "location_type": "WH", "wh_id": 2, "size": "L", "is_virtual": False},
        ]
        for location_id in range(3, target + 1):
            is_online = location_id in {target - 1, target}
            rows.append(
                {
                    "location_id": location_id,
                    "location_type": "STORE_ONLINE" if is_online else "STORE_OFFLINE",
                    "wh_id": 1 if location_id % 2 else 2,
                    "size": random.choice(["S", "M", "L"]),
                    "is_virtual": is_online,
                }
            )
        return pd.DataFrame(rows[:target])

    def _generate_sales_fact(self) -> pd.DataFrame:
        books = self._require("books_static")
        locations = self._store_locations()
        rows = []
        for _ in range(self.tables["sales_fact"].row_count):
            book = books.sample(1).iloc[0]
            loc = locations.sample(1).iloc[0]
            qty = weighted_int(1, 35, bias_low=True)
            avg_price = float(book["price_sales"])
            rows.append(
                {
                    "sale_date": random_recent_date(self.today, 90),
                    "isbn13": book["isbn13"],
                    "store_id": int(loc["location_id"]),
                    "wh_id": int(loc["wh_id"]),
                    "channel": "online" if loc["location_type"] == "STORE_ONLINE" else "offline",
                    "qty_sold": qty,
                    "revenue": float(qty * avg_price),
                    "avg_price": avg_price,
                    "tx_count": random.randint(1, max(1, qty)),
                }
            )
        return pd.DataFrame(rows)

    def _generate_inventory_daily(self) -> pd.DataFrame:
        books = self._require("books_static")
        locations = self._require("locations_static")
        physical = locations[locations["is_virtual"] == False]  # noqa: E712
        rows = []
        for _ in range(self.tables["inventory_daily"].row_count):
            book = books.sample(1).iloc[0]
            loc = physical.sample(1).iloc[0]
            safety_stock = random.randint(5, 50)
            on_hand = random.randint(0, 500)
            rows.append(
                {
                    "snapshot_date": random_recent_date(self.today, 90),
                    "isbn13": book["isbn13"],
                    "location_id": int(loc["location_id"]),
                    "on_hand": on_hand,
                    "reserved_qty": random.randint(0, min(on_hand, 80)),
                    "safety_stock": safety_stock,
                }
            )
        return pd.DataFrame(rows)

    def _generate_features(self) -> pd.DataFrame:
        books = self._require("books_static")
        rows = []
        for _ in range(self.tables["features"].row_count):
            d = random_recent_date(self.today, 90)
            is_holiday = random.random() < 0.05
            rows.append(
                {
                    "feature_date": d,
                    "isbn13": books.sample(1).iloc[0]["isbn13"],
                    "is_holiday": is_holiday,
                    "holiday_name": random.choice(["Seollal", "Chuseok", "Children Day", "Liberation Day"]) if is_holiday else "",
                    "season": season_for_month(d.month),
                    "day_of_week": d.isoweekday(),
                    "is_weekend": d.weekday() >= 5,
                    "month": d.month,
                    "event_nearby_days": random.randint(0, 30),
                    "sns_mentions_1d": weighted_int(0, 5000, bias_low=True),
                    "sns_mentions_7d": weighted_int(0, 30000, bias_low=True),
                    "book_age_days": random.randint(0, 3650),
                    "is_bestseller_flag": random.random() < 0.12,
                    "on_hand_total": random.randint(0, 5000),
                    "days_since_last_stockout": random.randint(0, 365),
                }
            )
        return pd.DataFrame(rows)

    def _generate_forecast_results(self) -> pd.DataFrame:
        books = self._require("books_static")
        stores = self._store_locations()
        rows = []
        for _ in range(self.tables["forecast_results"].row_count):
            prediction_date = random_recent_date(self.today, 90)
            predicted = round(random.uniform(1, 120), 2)
            rows.append(
                {
                    "prediction_date": prediction_date,
                    "target_date": prediction_date + timedelta(days=random.randint(1, 5)),
                    "isbn13": books.sample(1).iloc[0]["isbn13"],
                    "store_id": int(stores.sample(1).iloc[0]["location_id"]),
                    "predicted_demand": predicted,
                    "confidence_low": round(max(0, predicted * random.uniform(0.65, 0.9)), 2),
                    "confidence_high": round(predicted * random.uniform(1.1, 1.45), 2),
                    "model_version": f"v{random.randint(1, 8)}",
                    "inference_ms": random.randint(20, 2500),
                }
            )
        return pd.DataFrame(rows)

    def _generate_books(self) -> pd.DataFrame:
        static = self._require("books_static")
        rows = []
        for idx, book in static.head(self.tables["books"].row_count).iterrows():
            created = random_recent_datetime(self.today, 90)
            rows.append(
                {
                    "isbn13": book["isbn13"],
                    "isbn10": str(random.randint(1000000000, 9999999999)),
                    "aladin_item_id": 100000 + idx,
                    "title": self.book_title(),
                    "author": book["author"],
                    "publisher": book["publisher"],
                    "pub_date": self.today - timedelta(days=random.randint(0, 3650)),
                    "category_id": int(book["category_id"]),
                    "category_name": book["category_name"],
                    "price_standard": int(book["price_standard"]),
                    "price_sales": int(book["price_sales"]),
                    "cover_url": f"https://example.com/covers/{book['isbn13']}.jpg",
                    "description": self.fake.sentence(nb_words=12),
                    "active": random.random() > 0.03,
                    "discontinue_mode": "",
                    "discontinue_reason": "",
                    "discontinue_at": None,
                    "discontinue_by": "",
                    "reactivated_at": None,
                    "expected_soldout_at": self.today + timedelta(days=random.randint(1, 60)),
                    "source": "aladin",
                    "created_at": created,
                    "updated_at": created + timedelta(hours=random.randint(1, 96)),
                }
            )
        return pd.DataFrame(rows)

    def _generate_locations(self) -> pd.DataFrame:
        static = self._require("locations_static")
        rows = []
        for _, loc in static.iterrows():
            rows.append(
                {
                    "location_id": int(loc["location_id"]),
                    "location_type": loc["location_type"],
                    "wh_id": int(loc["wh_id"]),
                    "name": f"{self.fake.city()} {loc['location_type']}",
                    "size": loc["size"],
                    "region": random.choice(REGIONS),
                    "is_virtual": bool(loc["is_virtual"]),
                    "active": True,
                }
            )
        return pd.DataFrame(rows).head(self.tables["locations"].row_count)

    def _generate_authors(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "author_id": range(1, self.tables["authors"].row_count + 1),
                "name": [self.fake.name() for _ in range(self.tables["authors"].row_count)],
                "debut_year": [random.randint(1970, self.today.year - 1) for _ in range(self.tables["authors"].row_count)],
                "past_books_count": [random.randint(0, 50) for _ in range(self.tables["authors"].row_count)],
            }
        )

    def _generate_publishers(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "publisher_id": range(1, self.tables["publishers"].row_count + 1),
                "name": [self.fake.company() for _ in range(self.tables["publishers"].row_count)],
                "contact_email": [self.fake.company_email() for _ in range(self.tables["publishers"].row_count)],
            }
        )

    def _generate_warehouses(self) -> pd.DataFrame:
        count = max(2, self.tables["warehouses"].row_count)
        return pd.DataFrame(
            {
                "wh_id": range(1, count + 1),
                "name": [f"BOOKFLOW {region} Logistics Center" for region in REGIONS[:count]],
                "region": REGIONS[:count],
                "capacity": [random.randint(80000, 300000) for _ in range(count)],
            }
        )

    def _generate_inventory(self) -> pd.DataFrame:
        daily = self._require("inventory_daily")
        latest = daily.drop_duplicates(["isbn13", "location_id"]).head(self.tables["inventory"].row_count)
        rows = []
        for _, row in latest.iterrows():
            rows.append(
                {
                    "isbn13": row["isbn13"],
                    "location_id": int(row["location_id"]),
                    "on_hand": int(row["on_hand"]),
                    "reserved_qty": int(row["reserved_qty"]),
                    "safety_stock": int(row["safety_stock"]),
                    "updated_at": random_recent_datetime(self.today, 7),
                    "updated_by": "inventory-svc",
                }
            )
        return pd.DataFrame(rows)

    def _generate_sales_realtime(self) -> pd.DataFrame:
        books = self._require("books_static")
        stores = self._store_locations()
        rows = []
        for i in range(1, self.tables["sales_realtime"].row_count + 1):
            book = books.sample(1).iloc[0]
            loc = stores.sample(1).iloc[0]
            qty = random.randint(1, 8)
            unit_price = int(book["price_sales"])
            discount = random.choice([0, 0, 0, 500, 1000, 1500])
            rows.append(
                {
                    "txn_id": f"TXN-{self.today:%Y%m%d}-{i:08d}",
                    "event_ts": random_recent_datetime(self.today, 90),
                    "store_id": int(loc["location_id"]),
                    "wh_id": int(loc["wh_id"]),
                    "channel": "online" if loc["location_type"] == "STORE_ONLINE" else "offline",
                    "isbn13": book["isbn13"],
                    "qty": qty,
                    "unit_price": unit_price,
                    "discount": discount,
                    "revenue": max(0, qty * (unit_price - discount)),
                    "payment_method": random.choice(["CARD", "CASH", "POINT", "TRANSFER"]),
                    "created_at": random_recent_datetime(self.today, 90),
                }
            )
        return pd.DataFrame(rows)

    def _generate_inventory_snapshot_daily(self) -> pd.DataFrame:
        inventory = self._require("inventory_daily")
        rows = []
        for i, row in inventory.head(self.tables["inventory_snapshot_daily"].row_count).iterrows():
            on_hand = int(row["on_hand"])
            reserved = int(row["reserved_qty"])
            rows.append(
                {
                    "snapshot_date": row["snapshot_date"],
                    "isbn13": row["isbn13"],
                    "location_id": int(row["location_id"]),
                    "on_hand": on_hand,
                    "reserved_qty": reserved,
                    "available": max(0, on_hand - reserved),
                    "safety_stock": int(row["safety_stock"]),
                    "snapshot_taken_at": datetime.combine(row["snapshot_date"], datetime.min.time()),
                }
            )
        return pd.DataFrame(rows)

    def _generate_kpi_daily(self) -> pd.DataFrame:
        stores = self._store_locations()
        books = self._require("books_static")
        rows = []
        for _ in range(self.tables["kpi_daily"].row_count):
            store = stores.sample(1).iloc[0]
            category_id = int(random.choice(CATEGORIES)[0])
            qty = random.randint(10, 600)
            rows.append(
                {
                    "kpi_date": random_recent_date(self.today, 90),
                    "store_id": int(store["location_id"]),
                    "category_id": category_id,
                    "channel": "online" if store["location_type"] == "STORE_ONLINE" else "offline",
                    "qty_sold": qty,
                    "revenue": qty * random.randint(9000, 25000),
                    "tx_count": random.randint(1, qty),
                    "avg_price": random.randint(9000, 25000),
                    "unique_isbn_count": random.randint(1, min(200, len(books))),
                    "top_isbn": books.sample(1).iloc[0]["isbn13"],
                    "synced_from_bq_at": random_recent_datetime(self.today, 7),
                }
            )
        return pd.DataFrame(rows)

    def _generate_generic(self, table_name: str) -> pd.DataFrame:
        spec = self.tables[table_name]
        return pd.DataFrame([{col.name: self.generic_value(table_name, col, i) for col in spec.columns} for i in range(spec.row_count)])

    def generic_value(self, table_name: str, col: ColumnSpec, i: int) -> Any:
        name = col.name.lower()
        desc = col.description.lower()
        if name in {"isbn13", "top_isbn"} or "isbn-13" in desc:
            return self._require("books_static").sample(1).iloc[0]["isbn13"]
        if name.endswith("_id") or name == "id":
            if name in {"store_id", "scope_store_id"}:
                return int(self._store_locations().sample(1).iloc[0]["location_id"])
            if name in {"location_id", "source_location_id", "target_location_id"}:
                return int(self._require("locations_static").sample(1).iloc[0]["location_id"])
            if name == "wh_id" or "warehouse" in desc:
                return random.randint(1, 2)
            return i + 1
        if col.py_type == "date":
            if "target" in name:
                return self.today + timedelta(days=random.randint(1, 5))
            return random_recent_date(self.today, 90)
        if col.py_type == "timestamp":
            return random_recent_datetime(self.today, 90)
        if col.py_type == "int":
            return int_from_description(desc)
        if col.py_type == "float":
            return float_from_description(desc)
        if col.py_type == "bool":
            return random.random() < 0.8 if "active" in name else random.random() < 0.2
        if col.py_type == "json":
            return json.dumps({"sample": True, "score": round(random.random(), 3)}, ensure_ascii=False)
        return self.string_value(name)

    def string_value(self, name: str) -> str:
        if "email" in name:
            return self.fake.email()
        if "name" in name:
            return self.fake.company() if "publisher" in name else self.fake.name()
        if "title" in name:
            return self.book_title()
        if "author" in name:
            return self.fake.name()
        if "publisher" in name:
            return self.fake.company()
        if "status" in name:
            return random.choice(ORDER_STATUS)
        if "role" in name:
            return random.choice(["HQ_MANAGER", "WH_MANAGER", "STORE_MANAGER", "AUDITOR"])
        if "channel" in name:
            return random.choice(["online", "offline"])
        if "region" in name:
            return random.choice(REGIONS)
        return self.fake.word()

    def book_title(self) -> str:
        words = self.fake.words(nb=random.randint(2, 5))
        return " ".join(words)

    def _require(self, table_name: str) -> pd.DataFrame:
        if table_name not in self.cache:
            self.cache[table_name] = self.generate_table(table_name)
        return self.cache[table_name]

    def _store_locations(self) -> pd.DataFrame:
        locations = self._require("locations_static")
        stores = locations[locations["location_type"].isin(["STORE_OFFLINE", "STORE_ONLINE"])]
        return stores if not stores.empty else locations


def parse_ddl(path: Path) -> dict[str, list[ColumnSpec]]:
    text = path.read_text(encoding="utf-8")
    schemas: dict[str, list[ColumnSpec]] = {}
    table_pattern = re.compile(r"CREATE TABLE IF NOT EXISTS `[^`]+\.([A-Za-z0-9_]+)`\s*\((.*?)\)\s*(?:PARTITION|CLUSTER|OPTIONS|;)", re.S)
    for table_name, body in table_pattern.findall(text):
        columns = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith("PRIMARY KEY"):
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s+(INT64|STRING|DATE|NUMERIC|BOOL|BOOLEAN|TIMESTAMP|JSON)\b(.*)", line, re.I)
            if not match:
                continue
            name, bq_type, rest = match.groups()
            desc_match = re.search(r'OPTIONS\s*\(\s*description\s*=\s*"([^"]*)"', rest)
            bq_type = bq_type.upper()
            columns.append(ColumnSpec(name=name, bq_type=bq_type, py_type=TYPE_MAP[bq_type], description=desc_match.group(1) if desc_match else ""))
        schemas[table_name] = columns
    return schemas


def read_excel_metadata(path: Path, table_names: set[str]) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")

    workbook = pd.read_excel(path, sheet_name=None, dtype=object)
    metadata: dict[str, dict[str, Any]] = {}

    for sheet_name, df in workbook.items():
        if df.empty:
            continue
        df = df.dropna(how="all").copy()
        df.columns = [str(c).strip() for c in df.columns]
        normalized = {normalize(c): c for c in df.columns}

        table_col = find_table_column(normalized)
        if table_col:
            for table_value, group in df.groupby(table_col, dropna=True):
                table_name = match_table_name(table_value, table_names)
                if table_name:
                    merge_table_metadata(metadata, table_name, group, normalized)
            continue

        table_name = infer_table_name(sheet_name, df, table_names, normalized)
        if not table_name:
            continue
        merge_table_metadata(metadata, table_name, df, normalized)

    return metadata


def find_table_column(normalized: dict[str, str]) -> str | None:
    for key in ["table", "tablename", "tableid", "entity", "entityname"]:
        if key in normalized:
            return normalized[key]
    return None


def match_table_name(value: Any, table_names: set[str]) -> str | None:
    normalized_value = normalize(value)
    for table in table_names:
        if normalized_value == normalize(table):
            return table
    return None


def merge_table_metadata(metadata: dict[str, dict[str, Any]], table_name: str, df: pd.DataFrame, normalized: dict[str, str]) -> None:
    row_count = find_row_count(df, normalized)
    table_desc = find_table_description(df, normalized)
    column_descriptions = find_column_descriptions(df, normalized)
    metadata.setdefault(table_name, {})
    if row_count:
        metadata[table_name]["row_count"] = row_count
    if table_desc:
        metadata[table_name]["description"] = table_desc
    if column_descriptions:
        metadata[table_name].setdefault("columns", {}).update(column_descriptions)


def infer_table_name(sheet_name: str, df: pd.DataFrame, table_names: set[str], normalized: dict[str, str]) -> str | None:
    sheet_key = normalize(sheet_name)
    for table in table_names:
        if normalize(table) == sheet_key or normalize(table) in sheet_key:
            return table

    col = find_table_column(normalized)
    if col:
        values = [str(v).strip() for v in df[col].dropna().tolist()]
        for value in values:
            table = match_table_name(value, table_names)
            if table:
                return table

    sample = " ".join(str(v) for v in df.head(20).fillna("").to_numpy().ravel())
    for table in table_names:
        if table in sample:
            return table
    return None


def find_row_count(df: pd.DataFrame, normalized: dict[str, str]) -> int | None:
    for key in ["rowcount", "rows", "samplerows", "samplesize", "count"]:
        col = normalized.get(key)
        if col:
            for value in df[col].dropna().tolist():
                parsed = parse_int(value)
                if parsed and parsed > 0:
                    return parsed

    for _, row in df.iterrows():
        cells = [str(v).strip() for v in row.tolist() if pd.notna(v)]
        for idx, cell in enumerate(cells[:-1]):
            if normalize(cell) == "rowcount":
                parsed = parse_int(cells[idx + 1])
                if parsed and parsed > 0:
                    return parsed
    return None


def find_table_description(df: pd.DataFrame, normalized: dict[str, str]) -> str | None:
    for key in ["description", "desc", "businesslogic", "note"]:
        col = normalized.get(key)
        if col:
            values = [str(v).strip() for v in df[col].dropna().tolist() if str(v).strip()]
            if values:
                return values[0]
    return None


def find_column_descriptions(df: pd.DataFrame, normalized: dict[str, str]) -> dict[str, str]:
    col_name_col = None
    for key in ["column", "columnname", "field", "fieldname", "name"]:
        if key in normalized:
            col_name_col = normalized[key]
            break
    desc_col = None
    for key in ["description", "desc", "businesslogic", "note"]:
        if key in normalized:
            desc_col = normalized[key]
            break
    if not col_name_col or not desc_col:
        return {}
    result = {}
    for _, row in df[[col_name_col, desc_col]].dropna(how="any").iterrows():
        result[str(row[col_name_col]).strip()] = str(row[desc_col]).strip()
    return result


def ordered_tables(tables: dict[str, TableSpec]) -> list[str]:
    seen = set()
    ordered = []
    for name in TABLE_ORDER:
        if name in tables:
            ordered.append(name)
            seen.add(name)
    ordered.extend(sorted(set(tables) - seen))
    return ordered


def coerce_frame(df: pd.DataFrame, spec: TableSpec) -> pd.DataFrame:
    for col in spec.columns:
        if col.name not in df.columns:
            df[col.name] = None
        if col.py_type == "int":
            df[col.name] = df[col.name].fillna(0).astype(int)
        elif col.py_type == "float":
            df[col.name] = df[col.name].fillna(0).astype(float)
        elif col.py_type == "bool":
            df[col.name] = df[col.name].fillna(False).astype(bool)
        elif col.py_type == "date":
            df[col.name] = pd.to_datetime(df[col.name]).dt.date
        elif col.py_type == "timestamp":
            df[col.name] = pd.to_datetime(df[col.name], errors="coerce")
        elif col.py_type in {"str", "json"}:
            df[col.name] = df[col.name].fillna("").astype(str)
    return df[[col.name for col in spec.columns]]


def normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def parse_int(value: Any) -> int | None:
    if pd.isna(value):
        return None
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group(0).replace(",", "")) if match else None


def default_row_count(table_name: str) -> int:
    if table_name in {"books_static", "books"}:
        return 1000
    if table_name in {"locations_static", "locations"}:
        return 14
    if table_name in {"warehouses"}:
        return 2
    if table_name in {"authors", "publishers"}:
        return 200
    if "daily" in table_name or table_name in {"sales_fact", "features", "sales_realtime"}:
        return 5000
    return 1000


def random_recent_date(today: date, days: int) -> date:
    return today - timedelta(days=random.randint(0, days - 1))


def random_recent_datetime(today: date, days: int) -> datetime:
    d = random_recent_date(today, days)
    return datetime(d.year, d.month, d.day, random.randint(0, 23), random.randint(0, 59), random.randint(0, 59))


def make_isbn13(used: set[str]) -> str:
    while True:
        prefix = "979" + "".join(str(random.randint(0, 9)) for _ in range(9))
        check = isbn13_check_digit(prefix)
        isbn = prefix + str(check)
        if isbn not in used:
            used.add(isbn)
            return isbn


def isbn13_check_digit(first_12: str) -> int:
    total = sum((1 if idx % 2 == 0 else 3) * int(ch) for idx, ch in enumerate(first_12))
    return (10 - (total % 10)) % 10


def price_tier(price: int) -> str:
    if price < 15000:
        return "LOW"
    if price < 26000:
        return "MID"
    return "HIGH"


def season_for_month(month: int) -> str:
    if month in {3, 4, 5}:
        return "SPRING"
    if month in {6, 7, 8}:
        return "SUMMER"
    if month in {9, 10, 11}:
        return "FALL"
    return "WINTER"


def weighted_int(low: int, high: int, bias_low: bool = False) -> int:
    if not bias_low:
        return random.randint(low, high)
    return int(low + (high - low) * (random.random() ** 2))


def int_from_description(description: str) -> int:
    range_match = re.search(r"(\d+)\s*[-~]\s*(\d+)", description)
    if range_match:
        return random.randint(int(range_match.group(1)), int(range_match.group(2)))
    if "percent" in description or "ratio" in description:
        return random.randint(0, 100)
    if "qty" in description or "quantity" in description or "stock" in description:
        return random.randint(0, 500)
    if "price" in description or "cost" in description or "revenue" in description:
        return random.randint(1000, 500000)
    if "latency" in description or "milliseconds" in description:
        return random.randint(20, 2500)
    return random.randint(1, 1000)


def float_from_description(description: str) -> float:
    if "confidence" in description:
        return round(random.uniform(0.0, 1.0), 4)
    if "demand" in description or "quantity" in description:
        return round(random.uniform(0, 150), 2)
    if "price" in description or "cost" in description or "revenue" in description:
        return round(random.uniform(1000, 500000), 2)
    return round(random.uniform(0, 1000), 2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate synthetic BOOKFLOW BigQuery CSV data.")
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK, help="Path to V3_BOOKFLOW_Data_Schema.xlsx.")
    parser.add_argument("--ddl", type=Path, default=DEFAULT_DDL, help="Path to BigQuery DDL SQL.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for generated CSV files.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for deterministic output.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generator = SyntheticBookflowGenerator(args.ddl, args.workbook, args.output_dir, args.seed)
    generator.run()


if __name__ == "__main__":
    main()
