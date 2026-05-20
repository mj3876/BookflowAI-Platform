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


def model_predict(model, frame: pd.DataFrame) -> np.ndarray:
    if isinstance(model, dict) and model.get("type") == "weighted_ensemble":
        predictions = []
        for name, member in model["models"].items():
            predictions.append(float(model["weights"][name]) * np.asarray(member.predict(frame), dtype=float))
        return np.sum(predictions, axis=0)
    return np.asarray(model.predict(frame), dtype=float)


def predict_policy(model, metadata: dict, frame: pd.DataFrame) -> tuple[np.ndarray, int]:
    segment = frame["demand_segment"].fillna("low")
    policy = metadata.get("policy") or {}
    model_segments = set(policy.get("model_segments") or metadata.get("model_segments") or ["high"])
    medium_policy = policy.get("medium") or metadata.get("medium_policy") or "ma7"
    low_policy = policy.get("low") or metadata.get("low_policy") or "ma28"
    multipliers = policy.get("segment_multipliers") or metadata.get("segment_multipliers") or {}
    group_multipliers = policy.get("group_multipliers") or metadata.get("group_multipliers") or []
    model_mask = segment.isin(model_segments).to_numpy()
    pred = np.zeros(len(frame), dtype=float)
    start = time.perf_counter()
    if model_mask.any():
        pred[model_mask] = np.clip(model_predict(model, frame.loc[model_mask, FEATURE_COLUMNS]), 0, None)
    inference_ms = int((time.perf_counter() - start) * 1000)

    medium_mask = segment.eq("medium").to_numpy() & ~model_mask
    if medium_mask.any():
        source = "qty_rolling_7d" if medium_policy == "ma7" else "qty_rolling_28d"
        pred[medium_mask] = frame.loc[medium_mask, source].fillna(0).to_numpy(dtype=float)

    low_mask = segment.eq("low").to_numpy() & ~model_mask
    if low_mask.any():
        source = "qty_rolling_7d" if low_policy == "ma7" else "qty_rolling_28d"
        pred[low_mask] = frame.loc[low_mask, source].fillna(0).to_numpy(dtype=float)

    other_mask = ~(model_mask | medium_mask | low_mask)
    if other_mask.any():
        pred[other_mask] = frame.loc[other_mask, "qty_rolling_28d"].fillna(0).to_numpy(dtype=float)

    for demand_segment, multiplier in multipliers.items():
        mask = segment.eq(demand_segment).to_numpy()
        if mask.any():
            pred[mask] *= float(multiplier)
    for rule in group_multipliers:
        column = rule.get("column")
        if not column or column not in frame.columns:
            continue
        mask = segment.eq(str(rule.get("segment"))).to_numpy()
        mask &= frame[column].astype(str).eq(str(rule.get("value"))).to_numpy()
        if mask.any():
            pred[mask] *= float(rule.get("multiplier", 1.0))
    return np.clip(pred, 0, None), inference_ms


def confidence_bounds(pred: np.ndarray, metadata: dict, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    policy = metadata.get("policy") or {}
    multipliers = policy.get("segment_multipliers") or metadata.get("segment_multipliers") or {}
    segment = frame["demand_segment"].fillna("low")
    low = np.asarray(pred, dtype=float) * 0.75
    high = np.asarray(pred, dtype=float) * 1.25
    if metadata.get("calibrate_for_order") and multipliers:
        low = np.asarray(pred, dtype=float).copy()
        high = np.asarray(pred, dtype=float).copy()
        for demand_segment, multiplier in multipliers.items():
            mask = segment.eq(demand_segment).to_numpy()
            if mask.any():
                safe_multiplier = max(float(multiplier), 1.0)
                low[mask] = pred[mask] / safe_multiplier
                high[mask] = pred[mask] * min(1.18, 1.0 + ((safe_multiplier - 1.0) / 3.0))
        other_mask = ~segment.isin(multipliers.keys()).to_numpy()
        if other_mask.any():
            low[other_mask] = pred[other_mask] * 0.75
            high[other_mask] = pred[other_mask] * 1.25
    return np.maximum(low, 0), np.maximum(high, 0)


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

    pred, inference_ms = predict_policy(model, metadata, frame)
    confidence_low, confidence_high = confidence_bounds(pred, metadata, frame)
    out = pd.DataFrame(
        {
            "prediction_date": frame["prediction_date"],
            "target_date": frame["target_date"],
            "isbn13": frame["isbn13"].astype(str),
            "store_id": frame["store_id"].astype(int),
            "predicted_demand": np.round(pred, 6),
            "confidence_low": np.round(confidence_low, 6),
            "confidence_high": np.round(confidence_high, 6),
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
