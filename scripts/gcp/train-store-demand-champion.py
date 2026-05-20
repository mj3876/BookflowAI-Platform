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


def _parse_segments(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def model_predict(model: object, frame: pd.DataFrame) -> np.ndarray:
    if isinstance(model, dict) and model.get("type") == "weighted_ensemble":
        predictions = []
        for name, member in model["models"].items():
            predictions.append(float(model["weights"][name]) * np.asarray(member.predict(frame), dtype=float))
        return np.sum(predictions, axis=0)
    return np.asarray(model.predict(frame), dtype=float)


def predict_policy(
    model: object,
    frame: pd.DataFrame,
    model_segments: set[str],
    medium_policy: str,
    low_policy: str,
    segment_multipliers: dict[str, float] | None = None,
    group_multipliers: list[dict] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    segment = frame["demand_segment"].fillna("low")
    model_mask = segment.isin(model_segments).to_numpy()
    pred = np.zeros(len(frame), dtype=float)
    if model_mask.any():
        pred[model_mask] = np.clip(model_predict(model, frame.loc[model_mask, FEATURE_COLUMNS]), 0, None)

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

    pred = np.clip(pred, 0, None)
    if segment_multipliers:
        for demand_segment, multiplier in segment_multipliers.items():
            mask = segment.eq(demand_segment).to_numpy()
            if mask.any():
                pred[mask] *= float(multiplier)
    if group_multipliers:
        for rule in group_multipliers:
            column = rule["column"]
            if column not in frame.columns:
                continue
            mask = segment.eq(rule["segment"]).to_numpy()
            mask &= frame[column].astype(str).eq(str(rule["value"])).to_numpy()
            if mask.any():
                pred[mask] *= float(rule["multiplier"])
    return np.clip(pred, 0, None), model_mask


def metric_rows(model_name: str, actual: pd.Series, pred: np.ndarray, segments: pd.Series) -> list[dict]:
    rows = []
    for segment in ["all"] + sorted(segments.dropna().unique()):
        mask = np.ones(len(segments), dtype=bool) if segment == "all" else segments.eq(segment).to_numpy()
        y = actual.to_numpy(dtype=float)[mask]
        p = pred[mask]
        abs_error = np.abs(y - p)
        under = np.maximum(y - p, 0)
        over = np.maximum(p - y, 0)
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
                "p95_abs_error": float(np.quantile(abs_error, 0.95)),
                "underforecast_rate": float(np.mean(p < y)),
                "underforecast_units": float(under.sum()),
                "overforecast_units": float(over.sum()),
                "fill_rate_proxy": float(1 - (under.sum() / denom)) if denom else None,
            }
        )
    return rows


def order_cost_rows(metrics: list[dict], underforecast_cost: float, overforecast_cost: float) -> list[dict]:
    rows = []
    for row in metrics:
        actual_sum = row["actual_sum"] or 0
        weighted_cost = (
            row["underforecast_units"] * underforecast_cost
            + row["overforecast_units"] * overforecast_cost
        )
        next_row = dict(row)
        next_row["underforecast_cost"] = underforecast_cost
        next_row["overforecast_cost"] = overforecast_cost
        next_row["weighted_order_cost"] = float(weighted_cost)
        next_row["weighted_order_cost_rate"] = float(weighted_cost / actual_sum) if actual_sum else None
        rows.append(next_row)
    return rows


def segment_multipliers_from_predictions(
    actual: pd.Series,
    pred: np.ndarray,
    segments: pd.Series,
    service_level: float,
    max_multiplier: float,
) -> dict[str, float]:
    multipliers: dict[str, float] = {}
    safe_pred = np.maximum(np.asarray(pred, dtype=float), 0.05)
    actual_values = actual.to_numpy(dtype=float)
    for segment in sorted(segments.dropna().unique()):
        mask = segments.eq(segment).to_numpy()
        if not mask.any():
            continue
        ratios = actual_values[mask] / safe_pred[mask]
        multiplier = float(np.quantile(ratios, service_level))
        multipliers[str(segment)] = float(np.clip(multiplier, 1.0, max_multiplier))
    return multipliers


def group_multipliers_from_predictions(
    actual: pd.Series,
    pred: np.ndarray,
    frame: pd.DataFrame,
    columns: list[str],
    service_level: float,
    max_multiplier: float,
    min_rows: int,
    max_rules: int,
) -> list[dict]:
    rules = []
    safe_pred = np.maximum(np.asarray(pred, dtype=float), 0.05)
    actual_values = actual.to_numpy(dtype=float)
    base_ratio = actual_values / safe_pred
    for column in columns:
        if column not in frame.columns:
            continue
        grouped = frame[[column, "demand_segment"]].copy()
        grouped["_ratio"] = base_ratio
        for (value, segment), group in grouped.groupby([column, "demand_segment"], dropna=False):
            if len(group) < min_rows:
                continue
            multiplier = float(np.quantile(group["_ratio"].to_numpy(dtype=float), service_level))
            multiplier = float(np.clip(multiplier, 1.0, max_multiplier))
            if multiplier <= 1.001:
                continue
            rules.append(
                {
                    "column": column,
                    "value": str(value),
                    "segment": str(segment),
                    "multiplier": multiplier,
                    "train_rows": int(len(group)),
                }
            )
    rules.sort(key=lambda row: (row["multiplier"], row["train_rows"]), reverse=True)
    return rules[:max_rules]


def pass_gate(
    metrics: list[dict],
    max_all_wape: float,
    max_high_wape: float,
    max_medium_wape: float,
    min_all_fill_rate: float,
    min_high_fill_rate: float,
    max_all_underforecast_rate: float,
    max_high_underforecast_rate: float,
) -> tuple[bool, list[str]]:
    by_segment = {row["demand_segment"]: row for row in metrics}
    reasons = []
    if by_segment.get("all", {}).get("wape", 999) > max_all_wape:
        reasons.append("all_wape_above_threshold")
    if by_segment.get("high", {}).get("wape", 999) > max_high_wape:
        reasons.append("high_wape_above_threshold")
    if by_segment.get("medium", {}).get("wape", 999) > max_medium_wape:
        reasons.append("medium_wape_above_threshold")
    if by_segment.get("all", {}).get("fill_rate_proxy", 0) < min_all_fill_rate:
        reasons.append("all_fill_rate_below_threshold")
    if by_segment.get("high", {}).get("fill_rate_proxy", 0) < min_high_fill_rate:
        reasons.append("high_fill_rate_below_threshold")
    if by_segment.get("all", {}).get("underforecast_rate", 1) > max_all_underforecast_rate:
        reasons.append("all_underforecast_rate_above_threshold")
    if by_segment.get("high", {}).get("underforecast_rate", 1) > max_high_underforecast_rate:
        reasons.append("high_underforecast_rate_above_threshold")
    return not reasons, reasons


def make_regressor(args: argparse.Namespace):
    model_family = args.model_family
    if model_family == "hgb":
        return HistGradientBoostingRegressor(
            loss=args.hgb_loss,
            max_iter=args.n_estimators,
            learning_rate=args.learning_rate,
            max_leaf_nodes=args.max_leaf_nodes,
            l2_regularization=args.reg_lambda,
            random_state=42,
        )
    if model_family == "lightgbm":
        if LGBMRegressor is None:
            raise RuntimeError("lightgbm is not installed.")
        return LGBMRegressor(
            objective=args.objective,
            n_estimators=args.n_estimators,
            learning_rate=args.learning_rate,
            num_leaves=args.num_leaves,
            min_child_samples=args.min_child_samples,
            subsample=args.subsample,
            colsample_bytree=args.colsample_bytree,
            reg_alpha=args.reg_alpha,
            reg_lambda=args.reg_lambda,
            n_jobs=-1,
            random_state=42,
            verbosity=-1,
        )
    if model_family == "xgboost":
        if XGBRegressor is None:
            raise RuntimeError("xgboost is not installed.")
        return XGBRegressor(
            objective="count:poisson" if args.objective == "poisson" else "reg:squarederror",
            n_estimators=args.n_estimators,
            max_depth=args.max_depth,
            learning_rate=args.learning_rate,
            subsample=args.subsample,
            colsample_bytree=args.colsample_bytree,
            reg_alpha=args.reg_alpha,
            reg_lambda=args.reg_lambda,
            tree_method="hist",
            n_jobs=-1,
            random_state=42,
        )
    if model_family == "catboost":
        if CatBoostRegressor is None:
            raise RuntimeError("catboost is not installed.")
        return CatBoostRegressor(
            loss_function="Poisson" if args.objective == "poisson" else "RMSE",
            iterations=args.n_estimators,
            learning_rate=args.learning_rate,
            depth=args.max_depth,
            l2_leaf_reg=args.reg_lambda,
            random_seed=42,
            verbose=False,
            allow_writing_files=False,
            thread_count=-1,
        )
    raise ValueError(f"Unsupported model family: {model_family}")


def make_pipeline(regressor: object) -> Pipeline:
    return Pipeline(
        steps=[
            ("preprocess", make_preprocessor()),
            ("model", regressor),
        ]
    )


def with_family(args: argparse.Namespace, model_family: str, **overrides: object) -> argparse.Namespace:
    values = vars(args).copy()
    values["model_family"] = model_family
    values.update(overrides)
    return argparse.Namespace(**values)


def make_ensemble_members(args: argparse.Namespace) -> list[tuple[str, Pipeline]]:
    members = []
    if XGBRegressor is not None:
        members.append(
            (
                "xgboost_poisson",
                make_pipeline(
                    make_regressor(
                        with_family(
                            args,
                            "xgboost",
                            objective="poisson",
                            n_estimators=max(args.n_estimators, 300),
                            learning_rate=min(args.learning_rate, 0.025),
                            max_depth=min(args.max_depth, 4),
                            subsample=min(args.subsample, 0.9),
                            colsample_bytree=min(args.colsample_bytree, 0.85),
                            reg_alpha=max(args.reg_alpha, 0.1),
                            reg_lambda=max(args.reg_lambda, 8.0),
                        )
                    )
                ),
            )
        )
    if LGBMRegressor is not None:
        members.append(
            (
                "lightgbm_poisson",
                make_pipeline(
                    make_regressor(
                        with_family(
                            args,
                            "lightgbm",
                            objective="poisson",
                            n_estimators=max(args.n_estimators, 500),
                            learning_rate=min(args.learning_rate, 0.035),
                            min_child_samples=max(args.min_child_samples, 60),
                            reg_lambda=max(args.reg_lambda, 6.0),
                        )
                    )
                ),
            )
        )
    if CatBoostRegressor is not None:
        members.append(
            (
                "catboost_poisson",
                make_pipeline(
                    make_regressor(
                        with_family(
                            args,
                            "catboost",
                            objective="poisson",
                            n_estimators=max(args.n_estimators, 350),
                            learning_rate=min(args.learning_rate, 0.035),
                            max_depth=min(args.max_depth, 5),
                            reg_lambda=max(args.reg_lambda, 8.0),
                        )
                    )
                ),
            )
        )
    if len(members) < 2:
        raise RuntimeError("Ensemble requires at least two optional model libraries.")
    return members


def candidate_weight_grid(names: list[str]) -> list[dict[str, float]]:
    if len(names) != 3:
        equal = 1 / len(names)
        return [{name: equal for name in names}]
    grid = []
    for first in range(0, 11):
        for second in range(0, 11 - first):
            third = 10 - first - second
            grid.append(dict(zip(names, [first / 10, second / 10, third / 10], strict=True)))
    return grid


def choose_ensemble_weights(
    names: list[str],
    member_predictions: dict[str, np.ndarray],
    actual: pd.Series,
    segments: pd.Series,
    min_fill_rate: float,
    min_high_fill_rate: float,
    underforecast_cost: float,
    overforecast_cost: float,
) -> tuple[dict[str, float], list[dict]]:
    candidates = []
    for weights in candidate_weight_grid(names):
        pred = np.sum([weights[name] * member_predictions[name] for name in names], axis=0)
        rows = order_cost_rows(metric_rows("ensemble_candidate", actual, pred, segments), underforecast_cost, overforecast_cost)
        by_segment = {row["demand_segment"]: row for row in rows}
        all_row = by_segment["all"]
        high_row = by_segment.get("high", all_row)
        penalty = 0.0
        if all_row["fill_rate_proxy"] < min_fill_rate:
            penalty += (min_fill_rate - all_row["fill_rate_proxy"]) * 10
        if high_row["fill_rate_proxy"] < min_high_fill_rate:
            penalty += (min_high_fill_rate - high_row["fill_rate_proxy"]) * 10
        candidates.append(
            {
                "weights": weights,
                "weighted_order_cost_rate": all_row["weighted_order_cost_rate"],
                "wape": all_row["wape"],
                "fill_rate_proxy": all_row["fill_rate_proxy"],
                "underforecast_rate": all_row["underforecast_rate"],
                "score": all_row["weighted_order_cost_rate"] + penalty,
            }
        )
    candidates.sort(key=lambda row: (row["score"], row["weighted_order_cost_rate"], row["wape"]))
    return candidates[0]["weights"], candidates[:10]


def main() -> None:
    parser = argparse.ArgumentParser(description="Train low-cost BOOKFLOW store demand champion artifact.")
    parser.add_argument("--input-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-version", default="")
    parser.add_argument("--model-family", choices=["hgb", "lightgbm", "xgboost", "catboost", "ensemble"], default="lightgbm")
    parser.add_argument("--hgb-loss", choices=["poisson", "squared_error"], default="poisson")
    parser.add_argument("--objective", choices=["poisson", "squared_error"], default="poisson")
    parser.add_argument("--n-estimators", type=int, default=160)
    parser.add_argument("--learning-rate", type=float, default=0.045)
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--num-leaves", type=int, default=31)
    parser.add_argument("--min-child-samples", type=int, default=40)
    parser.add_argument("--subsample", type=float, default=0.9)
    parser.add_argument("--colsample-bytree", type=float, default=0.9)
    parser.add_argument("--reg-alpha", type=float, default=0.0)
    parser.add_argument("--reg-lambda", type=float, default=4.0)
    parser.add_argument("--model-segments", default="high,medium")
    parser.add_argument("--medium-policy", choices=["model", "ma7", "ma28"], default="model")
    parser.add_argument("--low-policy", choices=["model", "ma7", "ma28"], default="ma28")
    parser.add_argument("--calibrate-for-order", action="store_true")
    parser.add_argument("--target-service-level", type=float, default=0.9)
    parser.add_argument("--max-calibration-multiplier", type=float, default=1.35)
    parser.add_argument("--group-calibration-columns", default="")
    parser.add_argument("--group-target-service-level", type=float, default=0.9)
    parser.add_argument("--max-group-calibration-multiplier", type=float, default=1.15)
    parser.add_argument("--min-group-calibration-rows", type=int, default=500)
    parser.add_argument("--max-group-calibration-rules", type=int, default=24)
    parser.add_argument("--underforecast-cost", type=float, default=3.0)
    parser.add_argument("--overforecast-cost", type=float, default=1.0)
    parser.add_argument("--max-all-wape", type=float, default=0.526)
    parser.add_argument("--max-high-wape", type=float, default=0.522)
    parser.add_argument("--max-medium-wape", type=float, default=1.005)
    parser.add_argument("--min-all-fill-rate", type=float, default=0.82)
    parser.add_argument("--min-high-fill-rate", type=float, default=0.82)
    parser.add_argument("--max-all-underforecast-rate", type=float, default=0.58)
    parser.add_argument("--max-high-underforecast-rate", type=float, default=0.58)
    args = parser.parse_args()

    frame = load_frame(args.input_csv)
    model_segments = _parse_segments(args.model_segments)
    if args.medium_policy == "model":
        model_segments.add("medium")
    if args.low_policy == "model":
        model_segments.add("low")
    train = frame[frame["split_name"].eq("train") & frame["demand_segment"].isin(model_segments)].copy()
    holdout = frame[frame["split_name"].eq("holdout")].copy()
    if train.empty or holdout.empty:
        raise RuntimeError("Train or holdout frame is empty.")

    ensemble_search = []
    if args.model_family == "ensemble":
        fitted_members = {}
        member_predictions = {}
        for name, member in make_ensemble_members(args):
            member.fit(train[FEATURE_COLUMNS], train["qty_sold"].astype(float))
            fitted_members[name] = member
            member_predictions[name] = np.clip(model_predict(member, holdout[FEATURE_COLUMNS]), 0, None)
        weights, ensemble_search = choose_ensemble_weights(
            list(fitted_members),
            member_predictions,
            holdout["qty_sold"],
            holdout["demand_segment"],
            args.min_all_fill_rate,
            args.min_high_fill_rate,
            args.underforecast_cost,
            args.overforecast_cost,
        )
        model = {"type": "weighted_ensemble", "models": fitted_members, "weights": weights}
    else:
        model = make_pipeline(make_regressor(args))
        model.fit(train[FEATURE_COLUMNS], train["qty_sold"].astype(float))

    raw_train_pred, _ = predict_policy(model, train, model_segments, args.medium_policy, args.low_policy)
    segment_multipliers = (
        segment_multipliers_from_predictions(
            train["qty_sold"],
            raw_train_pred,
            train["demand_segment"],
            args.target_service_level,
            args.max_calibration_multiplier,
        )
        if args.calibrate_for_order
        else {}
    )
    segment_train_pred, _ = predict_policy(
        model,
        train,
        model_segments,
        args.medium_policy,
        args.low_policy,
        segment_multipliers,
    )
    group_columns = [item.strip() for item in args.group_calibration_columns.split(",") if item.strip()]
    group_multipliers = (
        group_multipliers_from_predictions(
            train["qty_sold"],
            segment_train_pred,
            train,
            group_columns,
            args.group_target_service_level,
            args.max_group_calibration_multiplier,
            args.min_group_calibration_rows,
            args.max_group_calibration_rules,
        )
        if args.calibrate_for_order and group_columns
        else []
    )
    champion_pred, _ = predict_policy(
        model,
        holdout,
        model_segments,
        args.medium_policy,
        args.low_policy,
        segment_multipliers,
        group_multipliers,
    )
    baseline_pred = holdout["qty_rolling_28d"].fillna(0).to_numpy(dtype=float)
    family_label = args.model_family if args.model_family != "hgb" else f"hgb_{args.hgb_loss}"
    suffix = "order_calibrated" if args.calibrate_for_order else "demand"
    model_name = f"store_demand_champion_{family_label}_{suffix}"
    metrics = order_cost_rows(
        metric_rows(model_name, holdout["qty_sold"], champion_pred, holdout["demand_segment"]),
        args.underforecast_cost,
        args.overforecast_cost,
    )
    baseline_metrics = order_cost_rows(
        metric_rows("baseline_ma28", holdout["qty_sold"], baseline_pred, holdout["demand_segment"]),
        args.underforecast_cost,
        args.overforecast_cost,
    )
    passed, reject_reasons = pass_gate(
        metrics,
        args.max_all_wape,
        args.max_high_wape,
        args.max_medium_wape,
        args.min_all_fill_rate,
        args.min_high_fill_rate,
        args.max_all_underforecast_rate,
        args.max_high_underforecast_rate,
    )

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
                    "model_segments": sorted(model_segments),
                    "medium": args.medium_policy,
                    "low": args.low_policy,
                    "segment_multipliers": segment_multipliers,
                    "group_multipliers": group_multipliers,
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
        "objective": args.objective,
        "hyperparameters": {
            "n_estimators": args.n_estimators,
            "learning_rate": args.learning_rate,
            "max_depth": args.max_depth,
            "max_leaf_nodes": args.max_leaf_nodes,
            "num_leaves": args.num_leaves,
            "min_child_samples": args.min_child_samples,
            "subsample": args.subsample,
            "colsample_bytree": args.colsample_bytree,
            "reg_alpha": args.reg_alpha,
            "reg_lambda": args.reg_lambda,
        },
        "ensemble_search": ensemble_search,
        "input_csv": str(Path(args.input_csv).resolve()),
        "model_segments": sorted(model_segments),
        "medium_policy": args.medium_policy,
        "low_policy": args.low_policy,
        "calibrate_for_order": args.calibrate_for_order,
        "target_service_level": args.target_service_level,
        "max_calibration_multiplier": args.max_calibration_multiplier,
        "segment_multipliers": segment_multipliers,
        "group_calibration_columns": group_columns,
        "group_target_service_level": args.group_target_service_level,
        "max_group_calibration_multiplier": args.max_group_calibration_multiplier,
        "group_multipliers": group_multipliers,
        "auto_order_profile": {
            "decision_owner": "aws-decision-svc",
            "schema_contract": "forecast_results.v1",
            "predicted_demand_semantics": "calibrated_auto_order_demand",
            "confidence_low_semantics": "uncalibrated_base_demand_proxy",
            "confidence_high_semantics": "upper_order_risk_bound",
            "requires_inventory_write_gateway": "inventory-svc",
            "auto_order_ready": passed,
            "recommended_decision_rule": (
                "decision-svc may auto-execute only when model gate_passed=true, "
                "forecast row model_version matches the approved champion, "
                "confidence_high does not breach configured overstock cap, "
                "and inventory/reorder constraints pass in AWS."
            ),
        },
        "train_rows_model_segments": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "holdout_min_date": str(holdout["feature_date"].min()),
        "holdout_max_date": str(holdout["feature_date"].max()),
        "gate_passed": passed,
        "reject_reasons": reject_reasons,
        "thresholds": {
            "max_all_wape": args.max_all_wape,
            "max_high_wape": args.max_high_wape,
            "max_medium_wape": args.max_medium_wape,
            "min_all_fill_rate": args.min_all_fill_rate,
            "min_high_fill_rate": args.min_high_fill_rate,
            "max_all_underforecast_rate": args.max_all_underforecast_rate,
            "max_high_underforecast_rate": args.max_high_underforecast_rate,
            "underforecast_cost": args.underforecast_cost,
            "overforecast_cost": args.overforecast_cost,
        },
        "metrics": metrics,
        "baseline_metrics": baseline_metrics,
    }
    (output_dir / "metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# BOOKFLOW Store Demand Champion\n\n"
        "Low-cost local CPU artifact. Stores demand policy metadata, order-risk metrics, "
        "and optional segment calibration multipliers for approval or automatic-order trials.\n",
        encoding="utf-8",
    )

    print(json.dumps(metadata, indent=2))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
