"""Compatibility checks for the Pydantic/Pandera pipeline boundaries."""

import json
from decimal import Decimal

import pandas as pd
import pytest
import requests
from pandera.errors import SchemaError

from dashboard import crime_snapshot, spd_snapshot
from dashboard.crime_call_support_data import validate_call_metric_source
from dashboard.call_metric_schemas import SOURCE_COLUMNS, LATEST_TIME_COLUMN
from dashboard.refresh_models import (
    ACSConfig, CrimeDateConfig, FiniteRetryConfig, FiniteTimeoutConfig,
    PaginationConfig, RetryConfig, RollingRefreshConfig, ScalarTimeoutConfig,
)
from dashboard.refresh_schemas import CRIME_DEDUPLICATED_SCHEMA, CALLS_DEDUPLICATED_SCHEMA
from dashboard.spd_client import _normalize_timeout
from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_schemas import UOF_NORMALIZED_SCHEMA
from dashboard.uof_snapshot import save_uof_snapshot
from scripts.dashboard import refresh_crime_data
from scripts.dashboard.refresh_uof_data import _prepare_snapshot


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Validation tests must not make network requests")
    monkeypatch.setattr(requests.sessions.Session, "request", unexpected)


@pytest.mark.parametrize("value", [True, False, "1", 1.5, None])
@pytest.mark.parametrize("field", ["page_size", "max_pages"])
def test_strict_pagination(field, value):
    if field == "max_pages" and value is None:
        assert PaginationConfig(page_size=1, max_pages=None).max_pages is None
    else:
        with pytest.raises(ValueError, match=field):
            PaginationConfig(**{"page_size": 1, field: value})


@pytest.mark.parametrize("field", ["rolling_window_days", "overlap_days", "page_size", "timeout"])
@pytest.mark.parametrize("value", [True, "2", -1])
def test_refresh_settings_do_not_coerce(field, value):
    values = dict(rolling_window_days=734, overlap_days=0, page_size=1, timeout=1)
    with pytest.raises(ValueError, match=field):
        RollingRefreshConfig(**{**values, field: value})


@pytest.mark.parametrize("model", [RetryConfig, FiniteRetryConfig])
@pytest.mark.parametrize("field", ["max_retries", "retry_backoff_seconds"])
@pytest.mark.parametrize("value", [True, "2", -1])
def test_retry_settings_are_strict(model, field, value):
    with pytest.raises(ValueError, match=field):
        model(**{**dict(max_retries=0, retry_backoff_seconds=0), field: value})


@pytest.mark.parametrize("value", [True, "1", [1, 2], (1,), (1, 2, 3), (True, 2), (1, "2"), Decimal("1")])
def test_timeout_rejects_unsupported_coercions(value):
    with pytest.raises(ValueError, match="timeout"):
        _normalize_timeout(value)


def test_timeout_forms_and_legacy_finiteness_difference():
    assert _normalize_timeout(3) == 3.0
    assert _normalize_timeout((2, 5.5)) == (2.0, 5.5)
    with pytest.raises(ValueError):
        ScalarTimeoutConfig(timeout=(2, 5))
    for value in (float("nan"), float("inf")):
        ScalarTimeoutConfig(timeout=value)  # Historical calls/crime policy.
        RetryConfig(max_retries=0, retry_backoff_seconds=value)
        with pytest.raises(ValueError):
            FiniteTimeoutConfig(timeout=value)
        with pytest.raises(ValueError):
            FiniteRetryConfig(max_retries=0, retry_backoff_seconds=value)


def test_date_column_and_acs_vintage_contracts():
    for column in ("offense_date", "report_date_time"):
        assert CrimeDateConfig(date_column=column).date_column == column
    with pytest.raises(ValueError):
        CrimeDateConfig(date_column="queued_time")
    for year in (True, "2024", 2008):
        with pytest.raises(ValueError):
            ACSConfig(acs_year=year)


@pytest.mark.parametrize("module,save,load", [
    (spd_snapshot, spd_snapshot.save_spd_call_snapshot, spd_snapshot.load_spd_call_snapshot),
    (crime_snapshot, crime_snapshot.save_crime_snapshot, crime_snapshot.load_crime_snapshot),
])
def test_legacy_snapshot_metadata_and_extra_columns(tmp_path, module, save, load):
    frame = pd.DataFrame({"legacy": [None, "a"], "extra": [2, 3]})
    _, path = save(frame, tmp_path, "2020-01-01")
    metadata = json.loads(path.read_text())
    metadata.update(refreshed_at_utc="legacy date text", optional_provenance={"custom": None})
    path.write_text(json.dumps(metadata))
    loaded, result = load(tmp_path)
    pd.testing.assert_frame_equal(loaded, frame)
    assert result == metadata  # No parsed dates, dropped extras or injected defaults.
    for field in ("row_count", "columns", "source_start_date", "refreshed_at_utc"):
        path.write_text(json.dumps({key: value for key, value in metadata.items() if key != field}))
        with pytest.raises(ValueError, match=field):
            load(tmp_path)


def test_compact_calls_keep_duplicate_ids_nulls_and_extra_columns():
    frame = pd.DataFrame([
        ["a", "2026-01-01", None, None, None],
        ["a", "2026-01-02", None, "unrecognized priority", None],
    ], columns=SOURCE_COLUMNS)
    frame[LATEST_TIME_COLUMN] = frame[SOURCE_COLUMNS[1]]
    frame["extra"] = "retained"
    result = validate_call_metric_source(frame)
    assert len(result) == 2
    assert result["extra"].eq("retained").all()
    assert result[SOURCE_COLUMNS[2]].isna().all()
    for column in SOURCE_COLUMNS + [LATEST_TIME_COLUMN]:
        with pytest.raises(ValueError, match="missing columns"):
            validate_call_metric_source(frame.drop(columns=column))


def test_uof_uniqueness_is_enforced_after_keep_last_resolution():
    frame = uof_records_to_dataframe([
        {"uniqueid": "a", "incident_type": "first"},
        {"uniqueid": "a", "incident_type": "last"},
    ])
    with pytest.raises(SchemaError):
        UOF_NORMALIZED_SCHEMA.validate(frame)
    result = _prepare_snapshot(frame)
    assert result.uniqueid.tolist() == ["a"]
    assert result.incident_type.tolist() == ["last"]
    assert result.occured_date_time.isna().all()


@pytest.mark.parametrize("invalid", [None, [], {}])
def test_uof_non_dataframe_save_keeps_value_error_interface(tmp_path, invalid):
    with pytest.raises(ValueError, match="DataFrame"):
        save_uof_snapshot(invalid, tmp_path)


def test_uof_canonical_snapshot_rejects_extra_or_duplicate_columns(tmp_path):
    frame = uof_records_to_dataframe([{"uniqueid": "a"}])
    for invalid in (frame.assign(extra=1), pd.concat([frame, frame[["uniqueid"]]], axis=1)):
        with pytest.raises(ValueError, match="canonical"):
            save_uof_snapshot(invalid, tmp_path)


@pytest.mark.parametrize("schema,key,times", [
    (CRIME_DEDUPLICATED_SCHEMA, "offense_id", ["offense_date", "report_date_time"]),
    (CALLS_DEDUPLICATED_SCHEMA, "call_sign_dispatch_id", ["cad_event_original_time_queued"]),
])
def test_normalized_snapshot_ids_must_be_unique(schema, key, times):
    frame = pd.DataFrame({key: ["a", "a"], **{column: [pd.NaT, pd.NaT] for column in times}})
    with pytest.raises(SchemaError):
        schema.validate(frame)
    schema.validate(frame.iloc[:1].assign(extra="allowed"))


def test_crime_full_refresh_preserves_source_duplicates_nulls_and_extra_columns(monkeypatch, tmp_path):
    frame = pd.DataFrame({
        "offense_id": ["a", "a"], "offense_date": ["2026-01-01", None],
        "report_date_time": [None, "2026-01-02"], "extra": [1, 2],
    })
    monkeypatch.setattr(refresh_crime_data, "load_crime_dataset", lambda **kwargs: frame.copy())
    refresh_crime_data.full_refresh_crime_snapshot("2026-01-01", output_directory=tmp_path)
    result, _ = crime_snapshot.load_crime_snapshot(tmp_path)
    assert len(result) == 2
    assert result.offense_id.tolist() == ["a", "a"]
    assert result.offense_date.isna().sum() == 1
    assert result.extra.tolist() == [1, 2]
