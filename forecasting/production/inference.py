from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from forecasting.production.artifact_models import ArtifactMetadata, ArtifactFeatureSchema

from forecasting.features.xgboost import build_xgboost_feature_panel, prepare_target_panel, validate_daily_panel
from forecasting.paths import FORECASTS_DIR
from forecasting.production.data_refresh import seattle_today
from forecasting.production.xgboost import FEATURE_SET_NAME, MODEL_CONFIG_ID, MODEL_NAME, MODEL_VERSION, file_sha256, write_json


LOGGER = logging.getLogger(__name__)
ARTIFACT_FILES = ["pipeline.joblib", "metadata.json", "feature_schema.json", "training_summary.json", "monitoring_baseline.json", "checksums.json"]


def load_verified_artifact(artifact_dir: str | Path) -> dict:
    directory = Path(artifact_dir)
    missing = [name for name in ARTIFACT_FILES if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Artifact is incomplete: missing {missing}")
    checksums = json.loads((directory / "checksums.json").read_text(encoding="utf-8"))
    for name, expected in checksums.get("files", {}).items():
        if not (directory / name).is_file() or file_sha256(directory / name) != expected:
            raise ValueError(f"Artifact checksum validation failed for {name}.")
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    schema = json.loads((directory / "feature_schema.json").read_text(encoding="utf-8"))
    baseline = json.loads((directory / "monitoring_baseline.json").read_text(encoding="utf-8"))
    identity = dict(model_name=MODEL_NAME, model_version=MODEL_VERSION,
                    model_config_id=MODEL_CONFIG_ID, feature_set_name=FEATURE_SET_NAME)
    try:
        ArtifactMetadata.model_validate(metadata, context=identity)
        ArtifactFeatureSchema.model_validate(schema, context=identity)
    except ValueError as error:
        raise ValueError(f"Artifact metadata or feature schema is incompatible: {error}") from error
    if baseline.get("expected_neighborhoods") != schema.get("fitted_neighborhood_categories"):
        raise ValueError("Artifact baseline neighborhood set is incompatible with fitted pipeline schema.")
    return {"directory": directory, "pipeline": joblib.load(directory / "pipeline.joblib"), "metadata": metadata, "schema": schema, "baseline": baseline, "checksums": checksums}


def build_future_features(target_panel: pd.DataFrame, expected_neighborhoods: list[str], forecast_origin: str | pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    observed = prepare_target_panel(target_panel)
    origin = pd.Timestamp(forecast_origin).normalize() if forecast_origin is not None else observed["target_date"].max()
    history = observed.loc[observed["target_date"] <= origin].copy()
    if origin not in set(history["target_date"]):
        raise ValueError("Forecast origin is not present in observed target history.")
    actual = set(history["neighborhood"].astype(str))
    expected = set(expected_neighborhoods)
    if actual != expected:
        raise ValueError(f"Observed neighborhood set differs from artifact; missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}")
    validate_daily_panel(history)
    target_date = origin + pd.Timedelta(days=1)
    # The synthetic zero target cannot influence this row: all target-history
    # features are shifted before use, matching the backtest feature builder.
    future = pd.DataFrame({"target_date": target_date, "neighborhood": sorted(expected), "calls": 0.0})
    panel = build_xgboost_feature_panel(pd.concat([history, future], ignore_index=True))
    features = panel.loc[panel["target_date"] == target_date].drop(columns="calls").sort_values("neighborhood").reset_index(drop=True)
    if len(features) != len(expected):
        raise ValueError("Insufficient history to construct all future feature rows.")
    return features, origin, target_date


def make_forecast_id(artifact_run_id: str, origin: pd.Timestamp, target_date: pd.Timestamp) -> str:
    identity = f"{MODEL_NAME}|{MODEL_VERSION}|{artifact_run_id}|{origin.date()}|{target_date.date()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _write_forecast_snapshot(snapshot: Path, forecast: pd.DataFrame, features: pd.DataFrame, diagnostics: dict) -> bool:
    if snapshot.exists():
        checksums_path = snapshot / "checksums.json"
        if not checksums_path.is_file():
            raise FileExistsError(f"Existing snapshot is incomplete: {snapshot}")
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        if all(file_sha256(snapshot / name) == value for name, value in checksums["files"].items()):
            existing_forecast = pd.read_parquet(snapshot / "forecast.parquet")
            existing_features = pd.read_parquet(snapshot / "inference_features.parquet")
            comparable_forecast = forecast.drop(columns="generated_at_utc")
            existing_comparable = existing_forecast.drop(columns="generated_at_utc")
            try:
                pd.testing.assert_frame_equal(
                    existing_comparable.reset_index(drop=True),
                    comparable_forecast.reset_index(drop=True),
                    check_exact=False,
                    rtol=1e-12,
                    atol=1e-12,
                )
                pd.testing.assert_frame_equal(
                    existing_features.reset_index(drop=True),
                    features.reset_index(drop=True),
                    check_exact=False,
                    rtol=1e-12,
                    atol=1e-12,
                )
            except AssertionError as exc:
                raise ValueError(
                    f"Existing snapshot differs from newly computed forecast: {snapshot}"
                ) from exc
            return True
        raise ValueError(f"Existing snapshot differs or fails checksum validation: {snapshot}")
    staging = snapshot.with_name(f".{snapshot.name}.staging")
    try:
        staging.mkdir(parents=True, exist_ok=False)
        forecast.to_parquet(staging / "forecast.parquet", index=False)
        features.to_parquet(staging / "inference_features.parquet", index=False)
        write_json(diagnostics, staging / "inference_diagnostics.json")
        files = ["forecast.parquet", "inference_features.parquet", "inference_diagnostics.json"]
        write_json({"algorithm": "sha256", "files": {name: file_sha256(staging / name) for name in files}}, staging / "checksums.json")
        staging.rename(snapshot)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return False


def generate_forecast(*, artifact_dir: str | Path, target_panel: pd.DataFrame, forecast_origin: str | None = None, output_root: str | Path = FORECASTS_DIR, max_data_age_days: int | None = None, as_of_date: str | pd.Timestamp | None = None) -> dict:
    artifact = load_verified_artifact(artifact_dir)
    features, origin, target_date = build_future_features(target_panel, artifact["baseline"]["expected_neighborhoods"], forecast_origin)
    today = (seattle_today() if as_of_date is None else pd.Timestamp(as_of_date)).normalize()
    age_days = int((today - origin).days)
    if max_data_age_days is not None and age_days > max_data_age_days:
        raise ValueError(f"Source data age {age_days} exceeds max_data_age_days={max_data_age_days}.")
    if age_days > 0:
        LOGGER.warning("Source data are %s days old.", age_days)
    raw_columns = artifact["schema"]["raw_training_columns"]
    X = features[raw_columns]
    predictions = artifact["pipeline"].predict(X)
    if len(predictions) != len(features) or not np.isfinite(predictions).all():
        raise ValueError("Forecast predictions are incomplete or non-finite.")
    generated = datetime.now(timezone.utc).isoformat()
    forecast_id = make_forecast_id(artifact["metadata"]["artifact_run_id"], origin, target_date)
    forecast = features[["neighborhood"]].copy()
    forecast["predicted_calls"] = predictions
    forecast = forecast.sort_values(["predicted_calls", "neighborhood"], ascending=[False, True]).reset_index(drop=True)
    forecast["predicted_rank"] = np.arange(1, len(forecast) + 1)
    for key, value in {"forecast_id": forecast_id, "generated_at_utc": generated, "forecast_origin": origin, "target_date": target_date, "model_name": MODEL_NAME, "model_version": MODEL_VERSION, "model_config_id": MODEL_CONFIG_ID, "artifact_run_id": artifact["metadata"]["artifact_run_id"], "feature_set_name": FEATURE_SET_NAME, "training_data_sha256": artifact["metadata"]["training_data_sha256"], "pipeline_sha256": artifact["checksums"]["files"]["pipeline.joblib"]}.items():
        forecast[key] = value
    feature_output = features.copy()
    feature_output["forecast_id"] = forecast_id
    feature_output["forecast_origin"] = origin
    feature_output["target_date"] = target_date
    feature_output = feature_output[
        ["forecast_id", "forecast_origin", "target_date", *raw_columns]
    ]
    feature_summaries = {}
    for name in artifact["schema"]["numeric_features"]:
        values = features[name]
        reference = artifact["baseline"]["numeric_features"][name]
        feature_summaries[name] = {"count": int(len(values)), "mean": float(values.mean()), "std": float(values.std(ddof=0)), "min": float(values.min()), "max": float(values.max()), "p10": float(values.quantile(.1)), "p50": float(values.quantile(.5)), "p90": float(values.quantile(.9)), "below_training_min_count": int((values < reference["min"]).sum()), "above_training_max_count": int((values > reference["max"]).sum())}
    diagnostics = {"forecast_id": forecast_id, "artifact_run_id": artifact["metadata"]["artifact_run_id"], "generated_at_utc": generated, "latest_observed_target_date": origin.date().isoformat(), "forecast_origin": origin.date().isoformat(), "target_date": target_date.date().isoformat(), "source_data_age_days": age_days, "input_row_count": len(features), "expected_neighborhood_count": len(artifact["baseline"]["expected_neighborhoods"]), "actual_neighborhood_count": len(features), "missing_feature_counts": {name: int(features[name].isna().sum()) for name in artifact["schema"]["raw_training_columns"]}, "unseen_neighborhood_count": 0, "missing_neighborhood_count": 0, "prediction_count": len(predictions), "prediction_mean": float(np.mean(predictions)), "prediction_std": float(np.std(predictions)), "prediction_min": float(np.min(predictions)), "prediction_max": float(np.max(predictions)), "prediction_p10": float(np.quantile(predictions, .1)), "prediction_p50": float(np.quantile(predictions, .5)), "prediction_p90": float(np.quantile(predictions, .9)), "negative_prediction_count": int((predictions < 0).sum()), "nonfinite_prediction_count": 0, "rank_count": len(forecast), "numeric_feature_summaries": feature_summaries}
    root = Path(output_root) / MODEL_NAME / MODEL_VERSION
    snapshot = root / "snapshots" / f"target_date={target_date.date()}" / f"artifact_run_id={artifact['metadata']['artifact_run_id']}"
    idempotent = _write_forecast_snapshot(snapshot, forecast, feature_output, diagnostics)
    if not idempotent:
        latest_temp = root / ".latest.parquet.tmp"
        forecast.to_parquet(latest_temp, index=False)
        latest_temp.replace(root / "latest.parquet")
        write_json({"forecast_id": forecast_id, "target_date": target_date.date().isoformat(), "forecast_origin": origin.date().isoformat(), "generated_at_utc": generated, "artifact_run_id": artifact["metadata"]["artifact_run_id"], "immutable_snapshot_path": str(snapshot), "forecast_checksum": file_sha256(snapshot / "forecast.parquet")}, root / "latest_manifest.json")
    return {"forecast": forecast, "features": feature_output, "diagnostics": diagnostics, "snapshot": snapshot, "idempotent": idempotent}
