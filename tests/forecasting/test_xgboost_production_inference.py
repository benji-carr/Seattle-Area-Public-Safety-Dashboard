import shutil
import uuid
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from forecasting.features.xgboost import build_xgboost_feature_panel, prepare_target_panel
from forecasting.production import inference
from forecasting.production.inference import build_future_features, generate_forecast
from forecasting.production.xgboost import train_production_model
from forecasting.production.xgboost import (
    MODEL_NAME, MODEL_VERSION, MODEL_CONFIG_ID, FEATURE_SET_NAME, file_sha256,
)


@pytest.fixture
def legacy_artifact(tmp_path):
    """The existing reader accepts optional provenance and absent schema_version."""
    payloads = {
        "metadata.json": {
            "model_name": MODEL_NAME, "model_version": MODEL_VERSION,
            "model_config_id": MODEL_CONFIG_ID, "feature_set_name": FEATURE_SET_NAME,
            "artifact_run_id": "legacy", "training_data_sha256": "fixture",
            "created_at_utc": "unchanged legacy date", "git_warning": None,
        },
        "feature_schema.json": {
            "feature_set_name": FEATURE_SET_NAME, "raw_training_columns": ["neighborhood", "calls_lag_1"],
            "numeric_features": ["calls_lag_1"], "fitted_neighborhood_categories": ["A", "B"],
            "extra": "kept",
        },
        "monitoring_baseline.json": {"expected_neighborhoods": ["A", "B"]},
        "training_summary.json": {},
    }
    for name, payload in payloads.items():
        (tmp_path / name).write_text(json.dumps(payload))
    (tmp_path / "pipeline.joblib").write_bytes(b"mock pipeline; never deserialized")
    return tmp_path, payloads


def seal_legacy_artifact(directory):
    files = {name: file_sha256(directory / name) for name in inference.ARTIFACT_FILES if name != "checksums.json"}
    (directory / "checksums.json").write_text(json.dumps({"files": files}))


def test_legacy_artifact_optional_metadata_is_returned_unmodified(legacy_artifact, monkeypatch):
    directory, payloads = legacy_artifact
    pipeline = object()
    monkeypatch.setattr(inference.joblib, "load", lambda _: pipeline)
    for version in (None, "1"):
        schema = payloads["feature_schema.json"].copy()
        if version is not None:
            schema["schema_version"] = version
        (directory / "feature_schema.json").write_text(json.dumps(schema))
        seal_legacy_artifact(directory)
        result = inference.load_verified_artifact(directory)
        assert result["metadata"] == payloads["metadata.json"]
        assert result["schema"] == schema
        assert result["pipeline"] is pipeline


@pytest.mark.parametrize("field", ["model_name", "model_version", "model_config_id", "feature_set_name"])
def test_artifact_missing_identity_fails_before_deserialization(legacy_artifact, monkeypatch, field):
    directory, payloads = legacy_artifact
    metadata = payloads["metadata.json"].copy()
    metadata.pop(field)
    (directory / "metadata.json").write_text(json.dumps(metadata))
    seal_legacy_artifact(directory)
    monkeypatch.setattr(inference.joblib, "load", lambda _: pytest.fail("must validate before loading"))
    with pytest.raises(ValueError, match=field):
        inference.load_verified_artifact(directory)


def test_artifact_checksum_failure_precedes_metadata_validation(legacy_artifact, monkeypatch):
    directory, _ = legacy_artifact
    seal_legacy_artifact(directory)
    (directory / "metadata.json").write_text("{}")
    monkeypatch.setattr(inference.joblib, "load", lambda _: pytest.fail("must verify before loading"))
    with pytest.raises(ValueError, match="checksum"):
        inference.load_verified_artifact(directory)


def make_panels(n_days=80):
    rows = []
    for day, date in enumerate(pd.date_range("2024-01-01", periods=n_days, freq="D")):
        for index, neighborhood in enumerate(("A", "B")):
            rows.append({"target_date": date, "neighborhood": neighborhood, "calls": float(index + day % 9)})
    target = prepare_target_panel(pd.DataFrame(rows))
    return target, build_xgboost_feature_panel(target)


def test_future_features_match_historical_backtest_features():
    target, historical_features = make_panels()
    target_date = historical_features["target_date"].iloc[-5]
    expected = sorted(target["neighborhood"].unique())
    future, origin, produced_date = build_future_features(target, expected, target_date - pd.Timedelta(days=1))
    actual = historical_features.loc[historical_features["target_date"] == target_date].sort_values("neighborhood")
    assert produced_date == target_date
    assert origin == target_date - pd.Timedelta(days=1)
    model_columns = [column for column in future if column != "target_date"]
    pd.testing.assert_frame_equal(
        future[model_columns].reset_index(drop=True),
        actual[model_columns].reset_index(drop=True),
        check_dtype=False,
    )


def test_forecast_snapshot_is_idempotent_and_rejects_entity_change():
    target, features = make_panels()
    root = Path("tests") / "_tmp" / f"inference_{uuid.uuid4().hex}"
    try:
        artifact = train_production_model(target_panel=target, feature_panel=features, target_panel_path=Path("target"), feature_panel_path=Path("features"), output_root=root)["artifact_dir"]
        output = root / "forecasts"
        first = generate_forecast(artifact_dir=artifact, target_panel=target, output_root=output)
        second = generate_forecast(artifact_dir=artifact, target_panel=target, output_root=output)
        assert first["forecast"]["predicted_rank"].tolist() == [1, 2]
        assert second["idempotent"] is True
        changed = target.copy()
        changed.loc[changed.index[0], "neighborhood"] = "unexpected"
        with pytest.raises(ValueError, match="neighborhood set"):
            generate_forecast(artifact_dir=artifact, target_panel=changed, output_root=output)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_forecast_source_age_uses_seattle_calendar_date_and_is_idempotent(monkeypatch):
    target, features = make_panels(n_days=80)
    target["target_date"] += pd.Timedelta(days=(pd.Timestamp("2026-08-31") - target["target_date"].max()).days)
    features["target_date"] += pd.Timedelta(days=(pd.Timestamp("2026-08-31") - features["target_date"].max()).days)
    root = Path("tests") / "_tmp" / f"inference_age_{uuid.uuid4().hex}"
    try:
        artifact = train_production_model(
            target_panel=target,
            feature_panel=features,
            target_panel_path=Path("target"),
            feature_panel_path=Path("features"),
            output_root=root,
        )["artifact_dir"]
        args = {
            "artifact_dir": artifact,
            "target_panel": target,
            "forecast_origin": "2026-08-31",
            "output_root": root / "forecasts",
            "as_of_date": "2026-09-03",
        }
        with pytest.raises(ValueError, match="Source data age 3 exceeds max_data_age_days=2"):
            generate_forecast(**args, max_data_age_days=2)
        first = generate_forecast(**args, max_data_age_days=3)
        second = generate_forecast(**args, max_data_age_days=3)
        monkeypatch.setattr(inference, "seattle_today", lambda: pd.Timestamp("2026-09-03"))
        default_as_of_args = {key: value for key, value in args.items() if key != "as_of_date"}
        default_as_of_args["output_root"] = root / "default_as_of_forecasts"
        default_as_of = generate_forecast(**default_as_of_args, max_data_age_days=3)
        persisted_diagnostics = __import__("json").loads(
            (first["snapshot"] / "inference_diagnostics.json").read_text(encoding="utf-8")
        )

        expected_id = hashlib.sha256(
            f"spd_neighborhood_xgboost|v1|{first['diagnostics']['artifact_run_id']}|2026-08-31|2026-09-01".encode("utf-8")
        ).hexdigest()[:20]
        assert first["diagnostics"]["source_data_age_days"] == 3
        assert persisted_diagnostics["source_data_age_days"] == 3
        assert first["diagnostics"]["target_date"] == "2026-09-01"
        assert first["diagnostics"]["forecast_id"] == expected_id
        assert second["idempotent"] is True
        assert second["diagnostics"]["forecast_id"] == expected_id
        assert default_as_of["diagnostics"]["source_data_age_days"] == 3
    finally:
        shutil.rmtree(root, ignore_errors=True)
