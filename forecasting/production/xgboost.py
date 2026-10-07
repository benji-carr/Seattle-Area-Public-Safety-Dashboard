from __future__ import annotations

import hashlib
import json
import logging
import platform
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import joblib
import numpy as np
import pandas as pd
import sklearn
import xgboost
from pandera.errors import SchemaError, SchemaErrors

from forecasting.production.table_schemas import training_columns_schema, training_schema

from forecasting.backtests.xgboost import (
    build_xgboost_pipeline,
    resolve_numeric_features,
)
from forecasting.features.xgboost import (
    CALENDAR_XGB_FEATURES,
    TARGET_COLUMN,
    TARGET_HISTORY_FEATURES,
    prepare_target_panel,
    validate_xgboost_feature_panel,
)


MODEL_NAME = "spd_neighborhood_xgboost"
MODEL_VERSION = "v1"
MODEL_CONFIG_ID = "be7924a5110a"
FEATURE_SET_NAME = "lags_rolling_calendar"
SCHEMA_VERSION = "1"

PRODUCTION_XGB_PARAMS = MappingProxyType(
    {
        "n_estimators": 300,
        "learning_rate": 0.03,
        "max_depth": 6,
        "min_child_weight": 12,
        "subsample": 0.95,
        "colsample_bytree": 0.75,
        "reg_lambda": 0.25,
        "reg_alpha": 0.50,
        "gamma": 0.01,
        "objective": "reg:squarederror",
        "tree_method": "hist",
        "random_state": 42,
        "n_jobs": 1,
    }
)

REFERENCE_EVALUATION_METRICS = {
    "development": {"mean_fold_mase": 0.757269},
    "final_holdout": {
        "mean_mase": 0.777653,
        "median_mase": 0.776374,
        "mean_mae": 3.490099,
        "mean_rmse": 4.465492,
        "mean_smape": 37.056085,
        "mean_bias": -0.018347,
    },
    "ranking": {
        "top10_accuracy_pct": 86.136986,
        "mean_correct_top10": 8.613699,
        "top10_volume_capture_pct": 97.653299,
        "rank_correlation": 0.932087,
    },
}

LOGGER = logging.getLogger(__name__)


def write_json(data: dict, path: Path) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataframe_fingerprint(training_frame: pd.DataFrame) -> str:
    """Hash canonical rows with pandas' stable content hashing, not file bytes."""
    canonical = training_frame.copy()
    canonical["target_date"] = pd.to_datetime(
        canonical["target_date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    canonical["neighborhood"] = canonical["neighborhood"].astype(str)
    canonical = canonical.sort_values(
        ["target_date", "neighborhood"]
    ).reset_index(drop=True)
    digest = hashlib.sha256()
    digest.update("|".join(canonical.columns).encode("utf-8"))
    digest.update(
        pd.util.hash_pandas_object(canonical, index=False)
        .to_numpy(dtype="uint64")
        .tobytes()
    )
    return digest.hexdigest()


def validate_training_data(
    target_panel: pd.DataFrame,
    feature_panel: pd.DataFrame,
    numeric_features: list[str],
) -> pd.DataFrame:
    prepare_target_panel(target_panel)
    validate_xgboost_feature_panel(feature_panel)

    try:
        training_columns_schema(numeric_features).validate(feature_panel)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Feature panel is missing required columns: {error}") from error

    training = feature_panel[
        ["target_date", "neighborhood", TARGET_COLUMN, *numeric_features]
    ].copy()
    training["target_date"] = pd.to_datetime(
        training["target_date"], errors="raise"
    ).dt.normalize()
    try:
        training_schema(numeric_features).validate(training)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Training rows contain duplicate keys, missing values, or non-finite numeric model inputs: {error}") from error
    if training["neighborhood"].nunique() < 2:
        raise ValueError("Production training requires at least two neighborhoods.")
    if training["target_date"].max() != feature_panel["target_date"].max():
        raise ValueError("Latest eligible feature-panel date was excluded from training.")
    return training.sort_values(["target_date", "neighborhood"]).reset_index(drop=True)


def git_metadata() -> tuple[dict, str | None]:
    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args], check=True, capture_output=True, text=True
            ).stdout.strip() or None
        except (FileNotFoundError, subprocess.CalledProcessError):
            return None

    commit = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    status = git("status", "--porcelain")
    warning = None if commit is not None else "Git metadata unavailable."
    return {"commit_sha": commit, "branch": branch, "dirty": bool(status) if status is not None else None}, warning


def build_feature_schema(pipeline, numeric_features: list[str], training: pd.DataFrame) -> dict:
    encoder = pipeline.named_steps["preprocessor"].named_transformers_["neighborhood"]
    transformed_count = int(pipeline.named_steps["preprocessor"].transform(training[["neighborhood", *numeric_features]].head(1)).shape[1])
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_set_name": FEATURE_SET_NAME,
        "categorical_features": ["neighborhood"],
        "numeric_features": numeric_features,
        "target_column": TARGET_COLUMN,
        "entity_column": "neighborhood",
        "date_column": "target_date",
        "target_history_features": TARGET_HISTORY_FEATURES,
        "calendar_features": CALENDAR_XGB_FEATURES,
        "raw_training_columns": ["neighborhood", *numeric_features],
        "number_of_transformed_features": transformed_count,
        "fitted_neighborhood_categories": [str(value) for value in encoder.categories_[0]],
    }


def build_monitoring_baseline(
    training: pd.DataFrame,
    target_panel: pd.DataFrame,
    numeric_features: list[str],
    run_id: str,
    training_sha: str,
) -> dict:
    numeric = {}
    for feature in numeric_features:
        values = training[feature].to_numpy(dtype=float)
        unique = np.unique(values)
        statistics = {
            "count": int(len(values)), "missing_count": int(np.isnan(values).sum()),
            "mean": float(np.mean(values)), "std": float(np.std(values)),
            "min": float(np.min(values)), "max": float(np.max(values)),
            **{f"p{int(q * 100):02d}": float(np.quantile(values, q)) for q in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]},
        }
        if len(unique) <= 5:
            statistics["value_frequencies"] = {str(value): float((values == value).mean()) for value in unique}
        else:
            edges = np.unique(np.quantile(values, np.linspace(0, 1, 11)))
            interior = edges[1:-1]
            bins = np.concatenate(([-np.inf], interior, [np.inf]))
            statistics["histogram"] = {
                "interior_bin_edges": [float(value) for value in interior],
                "expected_proportions": [float(value) for value in np.histogram(values, bins=bins)[0] / len(values)],
                "underflow_overflow": True,
            }
        numeric[feature] = statistics

    history = target_panel.sort_values(["neighborhood", "target_date"]).copy()
    history["lag_7"] = history.groupby("neighborhood")[TARGET_COLUMN].shift(7)
    neighborhoods = []
    for neighborhood, group in history.groupby("neighborhood", sort=True):
        target = group[TARGET_COLUMN]
        valid = group.dropna(subset=["lag_7"])
        neighborhoods.append({
            "neighborhood": str(neighborhood), "training_target_mean": float(target.mean()),
            "training_target_std": float(target.std()), "training_target_median": float(target.median()),
            "training_target_min": float(target.min()), "training_target_max": float(target.max()),
            "lag_7_seasonal_naive_mae_denominator": float((valid[TARGET_COLUMN] - valid["lag_7"]).abs().mean()),
        })
    return {
        "schema_version": "1", "model_name": MODEL_NAME, "model_version": MODEL_VERSION,
        "model_config_id": MODEL_CONFIG_ID, "artifact_run_id": run_id,
        "training_data_sha256": training_sha, "feature_set_name": FEATURE_SET_NAME,
        "expected_neighborhoods": sorted(training["neighborhood"].astype(str).unique().tolist()),
        "numeric_features": numeric, "per_neighborhood": neighborhoods,
        "mase_denominator_note": "Mean absolute difference between each observed target and its value seven calendar days earlier, calculated only from the fixed training target history.",
    }


def train_production_model(
    *,
    target_panel: pd.DataFrame,
    feature_panel: pd.DataFrame,
    target_panel_path: Path,
    feature_panel_path: Path,
    output_root: Path,
    model_version: str = MODEL_VERSION,
) -> dict:
    started = time.perf_counter()
    target_panel = prepare_target_panel(target_panel)
    numeric_features = resolve_numeric_features(feature_set_name=FEATURE_SET_NAME)
    training = validate_training_data(target_panel, feature_panel, numeric_features)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = output_root / MODEL_NAME / model_version / run_id
    staging_dir = final_dir.with_name(f".{run_id}.staging")
    if final_dir.exists() or staging_dir.exists():
        raise FileExistsError(f"Production artifact directory already exists: {final_dir}")

    LOGGER.info("Building pipeline")
    pipeline = build_xgboost_pipeline(
        numeric_features=numeric_features,
        model_params=dict(PRODUCTION_XGB_PARAMS),
    )
    model_params = pipeline.named_steps["model"].get_params()
    for key, value in PRODUCTION_XGB_PARAMS.items():
        if model_params[key] != value:
            raise AssertionError(f"Locked parameter mismatch for {key}.")

    X_train = training[["neighborhood", *numeric_features]]
    y_train = training[TARGET_COLUMN]
    LOGGER.info("Fitting model")
    fit_started = time.perf_counter()
    pipeline.fit(X_train, y_train)
    fit_elapsed = time.perf_counter() - fit_started
    schema = build_feature_schema(pipeline, numeric_features, training)
    training_sha = dataframe_fingerprint(training)

    try:
        staging_dir.mkdir(parents=True, exist_ok=False)
        serialization_started = time.perf_counter()
        pipeline_path = staging_dir / "pipeline.joblib"
        booster_path = staging_dir / "booster.ubj"
        joblib.dump(pipeline, pipeline_path)
        pipeline.named_steps["model"].get_booster().save_model(booster_path)

        LOGGER.info("Validating serialized pipeline")
        reloaded = joblib.load(pipeline_path)
        sample = X_train.head(min(10, len(X_train)))
        original_predictions = pipeline.predict(sample)
        reloaded_predictions = reloaded.predict(sample)
        np.testing.assert_allclose(original_predictions, reloaded_predictions, rtol=1e-12, atol=1e-12)
        if not np.isfinite(reloaded_predictions).all() or len(reloaded_predictions) != len(sample):
            raise ValueError("Serialized pipeline prediction validation failed.")
        serialization_elapsed = time.perf_counter() - serialization_started

        schema_path = staging_dir / "feature_schema.json"
        write_json(schema, schema_path)
        schema_sha = file_sha256(schema_path)
        write_json(
            build_monitoring_baseline(training, target_panel, numeric_features, run_id, training_sha),
            staging_dir / "monitoring_baseline.json",
        )
        git_info, git_warning = git_metadata()
        summary = {
            "training_start_date": training["target_date"].min().date().isoformat(),
            "training_end_date": training["target_date"].max().date().isoformat(),
            "n_training_rows": len(training),
            "n_training_dates": int(training["target_date"].nunique()),
            "n_neighborhoods": int(training["neighborhood"].nunique()),
            "target_statistics": {key: float(value) for key, value in training[TARGET_COLUMN].agg(["mean", "std", "min", "max"]).items()},
            "input_feature_count": len(numeric_features) + 1,
            "transformed_feature_count": schema["number_of_transformed_features"],
            "model_tree_count": int(pipeline.named_steps["model"].get_booster().num_boosted_rounds()),
            "fit_elapsed_seconds": fit_elapsed,
            "artifact_serialization_elapsed_seconds": serialization_elapsed,
            "total_elapsed_seconds": time.perf_counter() - started,
            "training_data_sha256": training_sha,
            "feature_schema_sha256": schema_sha,
            "round_trip_validation_passed": True,
        }
        write_json(summary, staging_dir / "training_summary.json")
        metadata = {
            "model_name": MODEL_NAME, "model_version": model_version,
            "model_config_id": MODEL_CONFIG_ID, "artifact_run_id": run_id,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "model_family": "XGBRegressor", "feature_set_name": FEATURE_SET_NAME,
            "frozen_hyperparameters": dict(PRODUCTION_XGB_PARAMS),
            "target_definition": "unique SPD CAD events per neighborhood per day",
            "target_column": TARGET_COLUMN, "date_column": "target_date", "entity_column": "neighborhood",
            "training_start_date": summary["training_start_date"], "training_end_date": summary["training_end_date"],
            "n_training_rows": summary["n_training_rows"], "n_training_dates": summary["n_training_dates"], "n_neighborhoods": summary["n_neighborhoods"],
            "resolved_target_panel_path": str(target_panel_path), "resolved_feature_panel_path": str(feature_panel_path),
            "python_version": platform.python_version(), "pandas_version": pd.__version__, "numpy_version": np.__version__,
            "scikit_learn_version": sklearn.__version__, "xgboost_version": xgboost.__version__, "joblib_version": joblib.__version__,
            "git": git_info, "git_warning": git_warning,
            "reference_evaluation_metrics": REFERENCE_EVALUATION_METRICS,
            "reference_metrics_note": "Frozen pre-production evaluation metrics; not computed from this all-history fit.",
            "training_data_sha256": training_sha, "feature_schema_sha256": schema_sha,
            "data_fingerprint_method": "SHA-256 of sorted relevant rows using pandas.hash_pandas_object with normalized dates and fixed column order.",
        }
        write_json(metadata, staging_dir / "metadata.json")
        checksum_files = ["pipeline.joblib", "booster.ubj", "metadata.json", "feature_schema.json", "training_summary.json", "monitoring_baseline.json"]
        write_json({"algorithm": "sha256", "files": {name: file_sha256(staging_dir / name) for name in checksum_files}}, staging_dir / "checksums.json")
        staging_dir.rename(final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    LOGGER.info("Production training complete: %s", final_dir)
    return {"artifact_dir": final_dir, "summary": summary, "metadata": metadata, "training": training}
