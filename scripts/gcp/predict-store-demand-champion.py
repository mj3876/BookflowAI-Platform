import argparse
import json
import os
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "store_id",
    "wh_id",
    "channel",
    "location_type",
    "store_size",
    "region",
    "on_hand",
    "reserved_qty",
    "safety_stock",
    "holiday_flag",
    "day_of_week",
    "month",
    "weekend_flag",
    "event_nearby_days",
    "sns_mentions_1d",
    "sns_mentions_7d",
    "book_age_days",
    "days_since_last_stockout",
    "category_id",
    "price_tier",
    "sales_point",
    "bestseller_flag",
    "author_experience_years",
    "qty_lag_1",
    "qty_lag_7",
    "qty_rolling_7d",
    "qty_rolling_28d",
    "demand_segment",
]


def predict_policy(model, frame: pd.DataFrame) -> tuple[np.ndarray, int]:
    segment = frame["demand_segment"].fillna("low")
    high_mask = segment.eq("high").to_numpy()
    medium_mask = segment.eq("medium").to_numpy()
    pred = np.zeros(len(frame), dtype=float)
    start = time.perf_counter()
    if high_mask.any():
        pred[high_mask] = np.clip(model.predict(frame.loc[high_mask, FEATURE_COLUMNS]), 0, None)
    inference_ms = int((time.perf_counter() - start) * 1000)
    if medium_mask.any():
        pred[medium_mask] = frame.loc[medium_mask, "qty_rolling_7d"].fillna(0).to_numpy(dtype=float)
    low_mask = ~(high_mask | medium_mask)
    if low_mask.any():
        pred[low_mask] = frame.loc[low_mask, "qty_rolling_28d"].fillna(0).to_numpy(dtype=float)
    return np.clip(pred, 0, None), inference_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="Predict BOOKFLOW store demand with a champion artifact.")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--model-version", default="")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    model = joblib.load(model_dir / "model.joblib")
    metadata = json.loads((model_dir / "metrics.json").read_text(encoding="utf-8"))
    model_version = args.model_version or metadata.get("model_version") or model_dir.name

    frame = pd.read_csv(args.input_csv)
    required = set(["prediction_date", "target_date", "isbn13", "store_id"] + FEATURE_COLUMNS)
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Input CSV missing required columns: {missing}")

    pred, inference_ms = predict_policy(model, frame)
    out = pd.DataFrame(
        {
            "prediction_date": frame["prediction_date"],
            "target_date": frame["target_date"],
            "isbn13": frame["isbn13"].astype(str),
            "store_id": frame["store_id"].astype(int),
            "predicted_demand": np.round(pred, 6),
            "confidence_low": np.round(np.maximum(pred * 0.75, 0), 6),
            "confidence_high": np.round(pred * 1.25, 6),
            "model_version": model_version,
            "inference_ms": inference_ms,
        }
    )
    Path(args.output_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.output_csv, index=False)
    print(
        json.dumps(
            {
                "rows": int(len(out)),
                "model_version": model_version,
                "prediction_date": str(out["prediction_date"].min()),
                "min_target_date": str(out["target_date"].min()),
                "max_target_date": str(out["target_date"].max()),
                "output_csv": str(Path(args.output_csv).resolve()),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
