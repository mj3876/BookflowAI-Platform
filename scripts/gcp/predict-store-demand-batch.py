import argparse
import json
import time
from datetime import date
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


def load_model(model_dir: Path):
    model_path = model_dir / "model.joblib"
    metrics_path = model_dir / "metrics.json"
    if not model_path.exists():
        raise FileNotFoundError(model_path)
    model = joblib.load(model_path)
    metadata = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    if metrics_path.exists() and not metadata.get("gate_passed", False):
        raise RuntimeError(f"Model gate did not pass: {metadata.get('reject_reasons')}")
    return model, metadata


def next_dates(start_date: str, horizon: int) -> list[pd.Timestamp]:
    start = pd.to_datetime(start_date).date()
    return [pd.Timestamp(start + pd.Timedelta(days=offset)) for offset in range(1, horizon + 1)]


def build_future_frame(latest: pd.DataFrame, prediction_date: str, horizon: int) -> pd.DataFrame:
    latest = latest.copy()
    frames = []
    for offset, target_date in enumerate(next_dates(prediction_date, horizon), start=1):
        future = latest.copy()
        future["target_date"] = target_date.date().isoformat()
        future["feature_date"] = target_date.date().isoformat()
        future["day_of_week"] = ((target_date.dayofweek + 1) % 7) + 1
        future["month"] = target_date.month
        future["weekend_flag"] = int(future["day_of_week"].iloc[0] in (1, 7))
        future["holiday_flag"] = 0
        future["event_nearby_days"] = np.maximum(future["event_nearby_days"].fillna(30).astype(float) - offset, 0)
        future["book_age_days"] = future["book_age_days"].fillna(0).astype(float) + offset
        future["days_since_last_stockout"] = future["days_since_last_stockout"].fillna(0).astype(float) + offset
        frames.append(future)
    return pd.concat(frames, ignore_index=True)


def predict_policy(model, metadata: dict, frame: pd.DataFrame) -> np.ndarray:
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
    elapsed_ms = int((time.perf_counter() - start) * 1000)

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
    return np.clip(pred, 0, None), elapsed_ms


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
    parser = argparse.ArgumentParser(description="Create BOOKFLOW D+1..D+N store demand batch forecasts.")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--latest-features-csv", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--prediction-date", default=date.today().isoformat())
    args = parser.parse_args()

    model, metadata = load_model(Path(args.model_dir))
    latest = pd.read_csv(args.latest_features_csv)
    missing = sorted(set(["feature_date", "isbn13"] + FEATURE_COLUMNS) - set(latest.columns))
    if missing:
        raise ValueError(f"Latest features CSV missing required columns: {missing}")

    latest = latest.sort_values(["isbn13", "store_id", "feature_date"]).drop_duplicates(["isbn13", "store_id"], keep="last")
    future = build_future_frame(latest, args.prediction_date, args.horizon)
    pred, inference_ms = predict_policy(model, metadata, future)
    confidence_low, confidence_high = confidence_bounds(pred, metadata, future)

    model_version = metadata.get("model_version", Path(args.model_dir).name)
    output = pd.DataFrame(
        {
            "prediction_date": args.prediction_date,
            "target_date": future["target_date"],
            "isbn13": future["isbn13"],
            "store_id": future["store_id"].astype(int),
            "predicted_demand": np.round(pred, 4),
            "confidence_low": np.round(confidence_low, 4),
            "confidence_high": np.round(confidence_high, 4),
            "model_version": model_version,
            "inference_ms": inference_ms,
        }
    )
    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False)
    summary = {
        "prediction_date": args.prediction_date,
        "horizon": args.horizon,
        "rows": int(len(output)),
        "series_count": int(output[["isbn13", "store_id"]].drop_duplicates().shape[0]),
        "min_target_date": str(output["target_date"].min()),
        "max_target_date": str(output["target_date"].max()),
        "predicted_sum": float(output["predicted_demand"].sum()),
        "negative_predictions": int((output["predicted_demand"] < 0).sum()),
        "model_version": model_version,
        "output_csv": str(output_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
