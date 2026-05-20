import argparse
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


def stable_unit(*parts: object) -> float:
    key = "#".join(str(part) for part in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:12], 16) / float(16**12 - 1)


def stable_noise(scale: float, *parts: object) -> float:
    return (stable_unit(*parts) - 0.5) * 2 * scale


def category_multiplier(category_id: pd.Series) -> pd.Series:
    normalized = category_id.fillna(0).astype(int) % 10
    return normalized.map(
        {
            0: 0.92,
            1: 1.18,
            2: 1.08,
            3: 0.96,
            4: 1.14,
            5: 1.05,
            6: 1.22,
            7: 0.88,
            8: 1.12,
            9: 1.00,
        }
    ).fillna(1.0)


def build_target(frame: pd.DataFrame) -> pd.Series:
    qty_lag_1 = frame["qty_lag_1"].fillna(0).astype(float)
    qty_lag_7 = frame["qty_lag_7"].fillna(0).astype(float)
    ma7 = frame["qty_rolling_7d"].fillna(0).astype(float)
    ma28 = frame["qty_rolling_28d"].fillna(0).astype(float)

    segment_base = frame["demand_segment"].map({"high": 2.8, "medium": 0.9, "low": 0.25}).fillna(0.4)
    history_anchor = (0.42 * ma7) + (0.32 * ma28) + (0.18 * qty_lag_7) + (0.08 * qty_lag_1)
    history_anchor = np.maximum(history_anchor, segment_base)

    store_size = frame["store_size"].map({"S": 0.88, "M": 1.0, "L": 1.22}).fillna(1.0)
    channel = frame["channel"].map({"online": 1.18, "offline": 1.0}).fillna(1.0)
    price_tier = frame["price_tier"].map({"LOW": 1.12, "MID": 1.0, "HIGH": 0.86}).fillna(1.0)
    bestseller = 1.0 + (frame["bestseller_flag"].fillna(0).astype(float) * 0.22)
    sales_point = 1.0 + np.minimum(np.log1p(frame["sales_point"].fillna(0).astype(float)) / 45, 0.28)
    author = 1.0 + np.minimum(frame["author_experience_years"].fillna(0).astype(float) / 160, 0.18)
    category = category_multiplier(frame["category_id"])

    weekend = 1.0 + (frame["weekend_flag"].fillna(0).astype(float) * 0.10)
    holiday = 1.0 + (frame["holiday_flag"].fillna(0).astype(float) * 0.16)
    event_days = frame["event_nearby_days"].fillna(30).astype(float)
    event = 1.0 + np.maximum(0, 7 - event_days) * 0.035
    sns = 1.0 + np.minimum(np.log1p(frame["sns_mentions_7d"].fillna(0).astype(float)) / 38, 0.24)

    age_days = frame["book_age_days"].fillna(365).astype(float)
    launch_decay = 0.82 + (0.38 * np.exp(-age_days / 180))
    stockout_rebound = 1.0 + np.maximum(0, 21 - frame["days_since_last_stockout"].fillna(365).astype(float)) * 0.008

    deterministic_noise = np.array(
        [
            stable_noise(0.08, row.isbn13, row.store_id, row.feature_date)
            for row in frame[["isbn13", "store_id", "feature_date"]].itertuples(index=False)
        ]
    )

    latent_demand = (
        history_anchor
        * store_size
        * channel
        * price_tier
        * bestseller
        * sales_point
        * author
        * category
        * weekend
        * holiday
        * event
        * sns
        * launch_decay
        * stockout_rebound
        * (1.0 + deterministic_noise)
    )

    available = np.maximum(
        frame["on_hand"].fillna(0).astype(float) - frame["reserved_qty"].fillna(0).astype(float),
        0,
    )
    daily_capacity = np.maximum(available * 0.18, frame["safety_stock"].fillna(0).astype(float) * 0.35)
    observed_sales = np.minimum(latent_demand, np.maximum(daily_capacity, segment_base * 1.2))
    return np.maximum(np.round(observed_sales), 0).astype(int)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a schema-compatible BOOKFLOW auto-order training CSV.")
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-csv", required=True)
    args = parser.parse_args()

    frame = pd.read_csv(args.input_csv)
    required = {
        "feature_date",
        "isbn13",
        "store_id",
        "qty_sold",
        "qty_lag_1",
        "qty_lag_7",
        "qty_rolling_7d",
        "qty_rolling_28d",
        "demand_segment",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Input CSV missing required columns: {missing}")

    output = frame.copy()
    output["qty_sold"] = build_target(output)
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False)

    summary = {
        "rows": int(len(output)),
        "input_csv": str(Path(args.input_csv).resolve()),
        "output_csv": str(output_path.resolve()),
        "qty_sum": int(output["qty_sold"].sum()),
        "qty_mean": float(output["qty_sold"].mean()),
        "zero_rate": float((output["qty_sold"] == 0).mean()),
    }
    print(summary)


if __name__ == "__main__":
    main()
