import json
import os
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException


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

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/app/model_artifact"))
MODEL = joblib.load(MODEL_DIR / "model.joblib")
METADATA = json.loads((MODEL_DIR / "metrics.json").read_text(encoding="utf-8"))
MODEL_VERSION = os.environ.get("MODEL_VERSION") or METADATA.get("model_version") or "unknown"
POLICY = METADATA.get("policy") or {}

app = FastAPI()


def _model_predict(model: Any, frame: pd.DataFrame) -> np.ndarray:
    if isinstance(model, dict) and model.get("type") == "weighted_ensemble":
        predictions = []
        for name, member in model["models"].items():
            predictions.append(float(model["weights"][name]) * np.asarray(member.predict(frame), dtype=float))
        return np.sum(predictions, axis=0)
    return np.asarray(model.predict(frame), dtype=float)


def _predict_policy(frame: pd.DataFrame) -> tuple[np.ndarray, int]:
    segment = frame["demand_segment"].fillna("low")
    model_segments = set(POLICY.get("model_segments") or METADATA.get("model_segments") or ["high"])
    medium_policy = POLICY.get("medium") or METADATA.get("medium_policy") or "ma7"
    low_policy = POLICY.get("low") or METADATA.get("low_policy") or "ma28"
    multipliers = POLICY.get("segment_multipliers") or METADATA.get("segment_multipliers") or {}
    model_mask = segment.isin(model_segments).to_numpy()
    pred = np.zeros(len(frame), dtype=float)
    start = time.perf_counter()
    if model_mask.any():
        pred[model_mask] = np.clip(_model_predict(MODEL, frame.loc[model_mask, FEATURE_COLUMNS]), 0, None)
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
    return np.clip(pred, 0, None), elapsed_ms


def _confidence_bounds(pred: np.ndarray, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    policy = METADATA.get("policy") or {}
    multipliers = policy.get("segment_multipliers") or METADATA.get("segment_multipliers") or {}
    segment = frame["demand_segment"].fillna("low")
    low = np.asarray(pred, dtype=float) * 0.75
    high = np.asarray(pred, dtype=float) * 1.25
    if METADATA.get("calibrate_for_order") and multipliers:
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


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "gate_passed": bool(METADATA.get("gate_passed", False)),
    }


@app.post("/predict")
def predict(payload: dict[str, Any]) -> dict[str, Any]:
    instances = payload.get("instances")
    if not isinstance(instances, list) or not instances:
        raise HTTPException(status_code=400, detail="payload.instances must be a non-empty list")
    frame = pd.DataFrame(instances)
    missing = sorted(set(FEATURE_COLUMNS) - set(frame.columns))
    if missing:
        raise HTTPException(status_code=400, detail=f"missing feature columns: {missing}")
    pred, inference_ms = _predict_policy(frame)
    confidence_low, confidence_high = _confidence_bounds(pred, frame)
    predictions = [
        {
            "predicted_demand": round(float(value), 4),
            "confidence_low": round(float(confidence_low[index]), 4),
            "confidence_high": round(float(confidence_high[index]), 4),
            "model_version": MODEL_VERSION,
            "inference_ms": inference_ms,
        }
        for index, value in enumerate(pred)
    ]
    return {"predictions": predictions}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
