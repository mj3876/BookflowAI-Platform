import argparse
import os
from dataclasses import dataclass

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "2")

import numpy as np
import pandas as pd
from google.cloud import bigquery
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

try:
    from xgboost import XGBRegressor
except Exception:  # pragma: no cover - optional dependency
    XGBRegressor = None

try:
    from lightgbm import LGBMRegressor
except Exception:  # pragma: no cover - optional dependency
    LGBMRegressor = None

try:
    from catboost import CatBoostRegressor
except Exception:  # pragma: no cover - optional dependency
    CatBoostRegressor = None


@dataclass(frozen=True)
class Metric:
    model_name: str
    demand_segment: str
    row_count: int
    actual_sum: float
    predicted_sum: float
    mae: float
    rmse: float
    wape: float
    bias: float
    p90_abs_error: float


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


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def build_query(project_id: str, dataset_id: str, table_id: str, train_days: int, holdout_days: int, train_sample_pct: int) -> str:
    feature_select = ",\n      ".join(FEATURE_COLUMNS)
    return f"""
    DECLARE max_feature_date DATE DEFAULT (
      SELECT MAX(feature_date)
      FROM `{project_id}.{dataset_id}.{table_id}`
    );

    WITH base AS (
      SELECT
        feature_date,
        isbn13,
        qty_sold,
        {feature_select}
      FROM `{project_id}.{dataset_id}.{table_id}`
      WHERE feature_date > DATE_SUB(max_feature_date, INTERVAL {train_days + holdout_days} DAY)
        AND feature_date <= max_feature_date
        AND demand_segment IN ('high', 'medium')
        AND qty_sold IS NOT NULL
    ),
    split AS (
      SELECT
        *,
        IF(feature_date > DATE_SUB(max_feature_date, INTERVAL {holdout_days} DAY), 'holdout', 'train') AS split_name
      FROM base
    )
    SELECT *
    FROM split
    WHERE split_name = 'holdout'
       OR MOD(ABS(FARM_FINGERPRINT(CONCAT(isbn13, '#', CAST(store_id AS STRING), '#', CAST(feature_date AS STRING)))), 100) < {train_sample_pct}
    """


def fetch_frame(args: argparse.Namespace) -> pd.DataFrame:
    client = bigquery.Client(project=args.project_id, location=args.bq_location)
    query = build_query(
        args.project_id,
        args.dataset_id,
        args.training_table,
        args.train_days,
        args.holdout_days,
        args.train_sample_pct,
    )
    rows = client.query(query).result(page_size=10000)
    return pd.DataFrame([dict(row.items()) for row in rows])


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


def candidate_models(random_state: int, mode: str) -> dict[str, object]:
    models: dict[str, object] = {
        "hist_gradient_boosting_poisson": HistGradientBoostingRegressor(
            loss="poisson",
            max_iter=120 if mode == "fast" else 180,
            learning_rate=0.06,
            max_leaf_nodes=31,
            l2_regularization=0.05,
            random_state=random_state,
        ),
        "hist_gradient_boosting_squared": HistGradientBoostingRegressor(
            loss="squared_error",
            max_iter=100 if mode == "fast" else 160,
            learning_rate=0.06,
            max_leaf_nodes=31,
            l2_regularization=0.05,
            random_state=random_state,
        ),
    }
    if mode != "fast":
        models["extra_trees"] = ExtraTreesRegressor(
            n_estimators=120,
            max_depth=18,
            min_samples_leaf=8,
            n_jobs=-1,
            random_state=random_state,
        )
    if XGBRegressor is not None:
        models["xgboost_poisson"] = XGBRegressor(
            objective="count:poisson",
            n_estimators=80 if mode == "fast" else 350,
            max_depth=6,
            learning_rate=0.045,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=4.0,
            tree_method="hist",
            n_jobs=-1,
            random_state=random_state,
        )
        if mode != "fast":
            models["xgboost_squared"] = XGBRegressor(
                objective="reg:squarederror",
                n_estimators=300,
                max_depth=6,
                learning_rate=0.045,
                subsample=0.9,
                colsample_bytree=0.9,
                reg_lambda=4.0,
                tree_method="hist",
                n_jobs=-1,
                random_state=random_state,
            )
    if LGBMRegressor is not None:
        models["lightgbm_poisson"] = LGBMRegressor(
            objective="poisson",
            n_estimators=160 if mode == "fast" else 500,
            learning_rate=0.045,
            num_leaves=31,
            min_child_samples=40,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=4.0,
            n_jobs=-1,
            random_state=random_state,
            verbosity=-1,
        )
        if mode != "fast":
            models["lightgbm_tweedie"] = LGBMRegressor(
                objective="tweedie",
                tweedie_variance_power=1.2,
                n_estimators=500,
                learning_rate=0.045,
                num_leaves=31,
                min_child_samples=40,
                subsample=0.9,
                colsample_bytree=0.9,
                reg_lambda=4.0,
                n_jobs=-1,
                random_state=random_state,
                verbosity=-1,
            )
    if CatBoostRegressor is not None:
        models["catboost_poisson"] = CatBoostRegressor(
            loss_function="Poisson",
            iterations=180 if mode == "fast" else 500,
            learning_rate=0.045,
            depth=6,
            l2_leaf_reg=6.0,
            random_seed=random_state,
            verbose=False,
            allow_writing_files=False,
            thread_count=-1,
        )
    return models


def evaluate(model_name: str, y_true: pd.Series, y_pred: np.ndarray, segments: pd.Series) -> list[Metric]:
    predictions = np.clip(np.asarray(y_pred, dtype=float), 0, None)
    rows: list[Metric] = []
    segment_values = ["all"] + sorted(segments.dropna().unique())
    for segment in segment_values:
        mask = pd.Series(True, index=segments.index) if segment == "all" else segments == segment
        actual = y_true[mask].astype(float).to_numpy()
        predicted = predictions[mask.to_numpy()]
        abs_error = np.abs(actual - predicted)
        actual_sum = float(np.sum(np.abs(actual)))
        rows.append(
            Metric(
                model_name=model_name,
                demand_segment=str(segment),
                row_count=int(mask.sum()),
                actual_sum=float(actual.sum()),
                predicted_sum=float(predicted.sum()),
                mae=float(mean_absolute_error(actual, predicted)),
                rmse=float(mean_squared_error(actual, predicted) ** 0.5),
                wape=float(abs_error.sum() / actual_sum) if actual_sum else np.nan,
                bias=float(np.mean(predicted - actual)),
                p90_abs_error=float(np.quantile(abs_error, 0.9)),
            )
        )
    return rows


def baseline_predictions(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    return {
        "baseline_ma_7d": frame["qty_rolling_7d"].fillna(0).to_numpy(),
        "baseline_ma_28d": frame["qty_rolling_28d"].fillna(0).to_numpy(),
        "baseline_lag_7": frame["qty_lag_7"].fillna(0).to_numpy(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Low-cost local ML backtest for BOOKFLOW store demand.")
    parser.add_argument("--project-id", default=os.getenv("BOOKFLOW_GCP_PROJECT_ID"))
    parser.add_argument("--dataset-id", default=os.getenv("BOOKFLOW_BQ_DATASET", "bookflow_dw"))
    parser.add_argument("--bq-location", default=os.getenv("BOOKFLOW_BQ_LOCATION", "asia-northeast1"))
    parser.add_argument("--training-table", default=os.getenv("BOOKFLOW_TRAINING_TABLE", "training_dataset_store"))
    parser.add_argument("--train-days", type=int, default=int(os.getenv("BOOKFLOW_LOCAL_TRAIN_DAYS", "120")))
    parser.add_argument("--holdout-days", type=int, default=int(os.getenv("BOOKFLOW_LOCAL_HOLDOUT_DAYS", "14")))
    parser.add_argument("--train-sample-pct", type=int, default=int(os.getenv("BOOKFLOW_LOCAL_TRAIN_SAMPLE_PCT", "25")))
    parser.add_argument("--mode", choices=["fast", "full"], default=os.getenv("BOOKFLOW_LOCAL_BACKTEST_MODE", "fast"))
    parser.add_argument("--random-state", type=int, default=int(os.getenv("BOOKFLOW_RANDOM_STATE", "42")))
    parser.add_argument("--input-csv", default=os.getenv("BOOKFLOW_LOCAL_BACKTEST_INPUT", ""))
    parser.add_argument("--output-csv", default=os.getenv("BOOKFLOW_LOCAL_BACKTEST_OUTPUT", ""))
    args = parser.parse_args()

    if not args.project_id and not args.input_csv:
        args.project_id = require_env("BOOKFLOW_GCP_PROJECT_ID")

    if args.input_csv:
        frame = pd.read_csv(args.input_csv)
    else:
        frame = fetch_frame(args)
    train = frame[frame["split_name"] == "train"].copy()
    holdout = frame[frame["split_name"] == "holdout"].copy()
    if train.empty or holdout.empty:
        raise RuntimeError("Train or holdout data is empty.")

    print(f"loaded_rows={len(frame)} train_rows={len(train)} holdout_rows={len(holdout)}", flush=True)
    print(f"train_dates={train['feature_date'].min()}..{train['feature_date'].max()}", flush=True)
    print(f"holdout_dates={holdout['feature_date'].min()}..{holdout['feature_date'].max()}", flush=True)

    metrics: list[Metric] = []
    y_holdout = holdout["qty_sold"].astype(float)
    for name, pred in baseline_predictions(holdout).items():
        metrics.extend(evaluate(name, y_holdout, pred, holdout["demand_segment"]))

    x_train = train[FEATURE_COLUMNS]
    y_train = train["qty_sold"].astype(float)
    x_holdout = holdout[FEATURE_COLUMNS]

    for name, regressor in candidate_models(args.random_state, args.mode).items():
        pipeline = Pipeline(
            steps=[
                ("preprocess", make_preprocessor()),
                ("model", regressor),
            ]
        )
        print(f"training_model={name}", flush=True)
        pipeline.fit(x_train, y_train)
        metrics.extend(evaluate(name, y_holdout, pipeline.predict(x_holdout), holdout["demand_segment"]))

    result = pd.DataFrame([m.__dict__ for m in metrics])
    result = result.sort_values(["demand_segment", "wape", "model_name"])
    print(result.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    best = result.sort_values("wape").groupby("demand_segment", as_index=False).first()
    print("\nBest by segment:")
    print(best.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    policy = best[best["demand_segment"].isin(["high", "medium"])].copy()
    print("\nRecommended low-cost policy:")
    print(policy[["demand_segment", "model_name", "wape", "mae", "rmse", "bias", "p90_abs_error"]].to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    if args.output_csv:
        result.to_csv(args.output_csv, index=False)
        print(f"wrote_metrics={args.output_csv}")


if __name__ == "__main__":
    main()
