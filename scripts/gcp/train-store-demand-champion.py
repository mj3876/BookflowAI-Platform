import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

try:
    from lightgbm import LGBMRegressor
except Exception:  # pragma: no cover - optional dependency
    LGBMRegressor = None

try:
    from xgboost import XGBRegressor
except Exception:  # pragma: no cover - optional dependency
    XGBRegressor = None

try:
    from catboost import CatBoostRegressor
except Exception:  # pragma: no cover - optional dependency
    CatBoostRegressor = None


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

CATEGORICAL_COLUMNS = [
    "channel",
    "location_type",
    "store_size",
    "region",
    "price_tier",
    "demand_segment",
]


def make_preprocessor() -> ColumnTransformer:
    numeric_columns = [col for col in FEATURE_COLUMNS if col not in CATEGORICAL_COLUMNS]
    numeric_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value=0)),
        ]
    )
    categorical_pipe = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="missing")),
            ("encoder", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)),
        ]
    )
    return ColumnTransformer(
        transformers=[
            ("num", numeric_pipe, numeric_columns),
            ("cat", categorical_pipe, CATEGORICAL_COLUMNS),
        ],
        remainder="drop",
    )


def load_frame(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = set(FEATURE_COLUMNS + ["feature_date", "isbn13", "qty_sold", "split_name"])
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Input CSV missing required columns: {missing}")
    return frame


def predict_policy(model: Pipeline, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    segment = frame["demand_segment"].fillna("low")
    high_mask = segment.eq("high").to_numpy()
    medium_mask = segment.eq("medium").to_numpy()
    pred = np.zeros(len(frame), dtype=float)
    if high_mask.any():
        pred[high_mask] = np.clip(model.predict(frame.loc[high_mask, FEATURE_COLUMNS]), 0, None)
    if medium_mask.any():
        pred[medium_mask] = frame.loc[medium_mask, "qty_rolling_7d"].fillna(0).to_numpy(dtype=float)
    low_mask = ~(high_mask | medium_mask)
    if low_mask.any():
        pred[low_mask] = frame.loc[low_mask, "qty_rolling_28d"].fillna(0).to_numpy(dtype=float)
    return np.clip(pred, 0, None), high_mask


def metric_rows(model_name: str, actual: pd.Series, pred: np.ndarray, segments: pd.Series) -> list[dict]:
    rows = []
    for segment in ["all"] + sorted(segments.dropna().unique()):
        mask = np.ones(len(segments), dtype=bool) if segment == "all" else segments.eq(segment).to_numpy()
        y = actual.to_numpy(dtype=float)[mask]
        p = pred[mask]
        abs_error = np.abs(y - p)
        denom = np.abs(y).sum()
        rows.append(
            {
                "model_name": model_name,
                "demand_segment": str(segment),
                "row_count": int(mask.sum()),
                "actual_sum": float(y.sum()),
                "predicted_sum": float(p.sum()),
                "mae": float(mean_absolute_error(y, p)),
                "rmse": float(mean_squared_error(y, p) ** 0.5),
                "wape": float(abs_error.sum() / denom) if denom else None,
                "bias": float(np.mean(p - y)),
                "p90_abs_error": float(np.quantile(abs_error, 0.9)),
            }
        )
    return rows


def pass_gate(metrics: list[dict], max_all_wape: float, max_high_wape: float, max_medium_wape: float) -> tuple[bool, list[str]]:
    by_segment = {row["demand_segment"]: row for row in metrics}
    reasons = []
    if by_segment.get("all", {}).get("wape", 999) > max_all_wape:
        reasons.append("all_wape_above_threshold")
    if by_segment.get("high", {}).get("wape", 999) > max_high_wape:
        reasons.append("high_wape_above_threshold")
    if by_segment.get("medium", {}).get("wape", 999) > max_medium_wape:
        reasons.append("medium_wape_above_threshold")
    return not reasons, reasons


def make_regressor(model_family: str, hgb_loss: str):
    if model_family == "hgb":
        return HistGradientBoostingRegressor(
            loss=hgb_loss,
            max_iter=120 if hgb_loss == "poisson" else 100,
            learning_rate=0.06,
            max_leaf_nodes=31,
            l2_regularization=0.05,
            random_state=42,
        )
    if model_family == "lightgbm":
        if LGBMRegressor is None:
            raise RuntimeError("lightgbm is not installed.")
        return LGBMRegressor(
            objective="poisson",
            n_estimators=160,
            learning_rate=0.045,
            num_leaves=31,
            min_child_samples=40,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=4.0,
            n_jobs=-1,
            random_state=42,
            verbosity=-1,
        )
    if model_family == "xgboost":
        if XGBRegressor is None:
            raise RuntimeError("xgboost is not installed.")
        return XGBRegressor(
            objective="count:poisson",
            n_estimators=80,
            max_depth=6,
            learning_rate=0.045,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=4.0,
            tree_method="hist",
            n_jobs=-1,
            random_state=42,
        )
    if model_family == "catboost":
        if CatBoostRegressor is None:
            raise RuntimeError("catboost is not installed.")
        return CatBoostRegressor(
            loss_function="Poisson",
            iterations=180,
            learning_rate=0.045,
            depth=6,
            l2_leaf_reg=6.0,
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
            thread_count=-1,
        )
    raise ValueError(f"Unsupported model family: {model_family}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train low-cost BOOKFLOW store demand champion artifact.")
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-version", default="")
    parser.add_argument("--model-family", choices=["hgb", "lightgbm", "xgboost", "catboost"], default="lightgbm")
    parser.add_argument("--hgb-loss", choices=["poisson", "squared_error"], default="poisson")
    parser.add_argument("--max-all-wape", type=float, default=0.526)
    parser.add_argument("--max-high-wape", type=float, default=0.522)
    parser.add_argument("--max-medium-wape", type=float, default=1.005)
    args = parser.parse_args()

    frame = load_frame(args.input_csv)
    train = frame[frame["split_name"].eq("train") & frame["demand_segment"].eq("high")].copy()
    holdout = frame[frame["split_name"].eq("holdout")].copy()
    if train.empty or holdout.empty:
        raise RuntimeError("Train or holdout frame is empty.")

    model = Pipeline(
        steps=[
            ("preprocess", make_preprocessor()),
            (
                "model",
                make_regressor(args.model_family, args.hgb_loss),
            ),
        ]
    )
    model.fit(train[FEATURE_COLUMNS], train["qty_sold"].astype(float))

    champion_pred, _ = predict_policy(model, holdout)
    baseline_pred = holdout["qty_rolling_28d"].fillna(0).to_numpy(dtype=float)
    family_label = args.model_family if args.model_family != "hgb" else f"hgb_{args.hgb_loss}"
    model_name = f"store_demand_champion_{family_label}_high_ma7_medium"
    metrics = metric_rows(model_name, holdout["qty_sold"], champion_pred, holdout["demand_segment"])
    baseline_metrics = metric_rows("baseline_ma28", holdout["qty_sold"], baseline_pred, holdout["demand_segment"])
    passed, reject_reasons = pass_gate(metrics, args.max_all_wape, args.max_high_wape, args.max_medium_wape)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_version = args.model_version or f"store-demand-champion-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    joblib.dump(model, output_dir / "model.joblib")
    pd.DataFrame(metrics + baseline_metrics).to_csv(output_dir / "metrics.csv", index=False)
    (output_dir / "feature_schema.json").write_text(
        json.dumps(
            {
                "feature_columns": FEATURE_COLUMNS,
                "categorical_columns": CATEGORICAL_COLUMNS,
                "target_column": "qty_sold",
                "policy": {
                    "high": family_label,
                    "medium": "qty_rolling_7d",
                    "low": "qty_rolling_28d",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    metadata = {
        "model_version": model_version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "training_mode": "local_cpu_low_cost",
        "model_family": args.model_family,
        "hgb_loss": args.hgb_loss,
        "input_csv": str(Path(args.input_csv).resolve()),
        "train_rows_high": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "holdout_min_date": str(holdout["feature_date"].min()),
        "holdout_max_date": str(holdout["feature_date"].max()),
        "gate_passed": passed,
        "reject_reasons": reject_reasons,
        "thresholds": {
            "max_all_wape": args.max_all_wape,
            "max_high_wape": args.max_high_wape,
            "max_medium_wape": args.max_medium_wape,
        },
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# BOOKFLOW Store Demand Champion\n\n"
        "Low-cost local CPU artifact. Uses HistGradientBoosting for high-demand series, "
        "ma7 for medium-demand series, and ma28 for low-demand fallback.\n",
        encoding="utf-8",
    )

    print(json.dumps(metadata, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
