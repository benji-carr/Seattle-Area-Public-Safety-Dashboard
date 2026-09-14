from unittest.mock import Mock

import pandas as pd
import pytest

from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_snapshot import load_uof_snapshot, save_uof_snapshot
from scripts.dashboard import refresh_uof_data as refresh


def fetch_result(records):
    return {"dataframe": uof_records_to_dataframe(records),
            "metadata": {"exhausted": True, "request_count": 1, "pages_fetched": 1}}


def test_initial_refresh_fetches_complete_history_and_deduplicates(tmp_path, monkeypatch):
    fetch = Mock(return_value=fetch_result([
        {"uniqueid": "old", "occured_date_time": "2015-08-24", "incident_type": "OIS"},
        {"uniqueid": "new", "occured_date_time": "2026-09-01", "incident_type": "Level 1"},
        {"uniqueid": "new", "occured_date_time": "2026-09-01", "incident_type": "Level 2"},
    ]))
    monkeypatch.setattr(refresh, "fetch_uof_dataset", fetch)
    refresh.incremental_refresh_uof_snapshot(tmp_path)
    assert fetch.call_args.kwargs["start_date"] is None
    assert fetch.call_args.kwargs["max_pages"] is None
    frame, metadata = load_uof_snapshot(tmp_path)
    assert frame.uniqueid.tolist() == ["old", "new"]
    assert frame.incident_type.tolist() == ["OIS", "Level 2"]
    assert metadata["source_start_date"].startswith("2015-08-24")
    assert metadata["source_end_date"].startswith("2026-09-01")


def test_incremental_overlap_keeps_refreshed_copy_and_all_history(tmp_path, monkeypatch):
    old = uof_records_to_dataframe([
        {"uniqueid": "old", "occured_date_time": "2015-08-24", "incident_type": "OIS"},
        {"uniqueid": "update", "occured_date_time": "2026-09-01", "incident_type": "Level 1"},
        {"uniqueid": "undated", "occured_date_time": "invalid"},
    ])
    save_uof_snapshot(old, tmp_path)
    fetch = Mock(return_value=fetch_result([
        {"uniqueid": "update", "occured_date_time": "2026-08-31", "incident_type": "Level 3 - OIS"},
        {"uniqueid": "new", "occured_date_time": "2026-09-02"},
    ]))
    monkeypatch.setattr(refresh, "fetch_uof_dataset", fetch)
    refresh.incremental_refresh_uof_snapshot(tmp_path)
    assert fetch.call_args.kwargs["start_date"] == "2026-08-02"
    assert fetch.call_args.kwargs["max_pages"] is None
    frame, _ = load_uof_snapshot(tmp_path)
    assert frame.uniqueid.tolist() == ["old", "update", "new", "undated"]
    updated = frame.set_index("uniqueid").loc["update"]
    assert updated.incident_type == "Level 3 - OIS"
    assert updated.occured_date_time == pd.Timestamp("2026-08-31")
    # Repeating the same overlap is idempotent for the source rows.
    refresh.incremental_refresh_uof_snapshot(tmp_path)
    pd.testing.assert_frame_equal(load_uof_snapshot(tmp_path)[0], frame)


@pytest.mark.parametrize("records,exhausted,match", [
    ([], True, "no records"), ([{"uniqueid": "1"}], False, "truncated"),
    ([{"occured_date_time": "2026-09-01"}], True, "missing uniqueid"),
])
def test_unsafe_initial_fetch_does_not_write_snapshot(tmp_path, monkeypatch, records, exhausted, match):
    result = fetch_result(records)
    result["metadata"]["exhausted"] = exhausted
    monkeypatch.setattr(refresh, "fetch_uof_dataset", Mock(return_value=result))
    with pytest.raises(ValueError, match=match):
        refresh.incremental_refresh_uof_snapshot(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_empty_incremental_fetch_keeps_existing_rows(tmp_path, monkeypatch):
    old = uof_records_to_dataframe([{"uniqueid": "old", "occured_date_time": "2015-08-24"}])
    save_uof_snapshot(old, tmp_path)
    monkeypatch.setattr(refresh, "fetch_uof_dataset", Mock(return_value=fetch_result([])))
    refresh.incremental_refresh_uof_snapshot(tmp_path)
    pd.testing.assert_frame_equal(load_uof_snapshot(tmp_path)[0], old)


def test_partial_snapshot_fails_without_fetching(tmp_path, monkeypatch):
    (tmp_path / "uof_metadata.json").write_text("{}")
    fetch = Mock()
    monkeypatch.setattr(refresh, "fetch_uof_dataset", fetch)
    with pytest.raises(FileNotFoundError):
        refresh.incremental_refresh_uof_snapshot(tmp_path)
    fetch.assert_not_called()


@pytest.mark.parametrize("missing", ["uniqueid", "occured_date_time"])
def test_incremental_validates_required_columns(tmp_path, monkeypatch, missing):
    (tmp_path / "uof_data.parquet").touch()
    frame = uof_records_to_dataframe([{"uniqueid": "1", "occured_date_time": "2024-01-01"}]).drop(columns=missing)
    monkeypatch.setattr(refresh, "load_uof_snapshot", lambda _: (frame, {}))
    with pytest.raises(ValueError, match="missing required columns"):
        refresh.incremental_refresh_uof_snapshot(tmp_path)


def test_refresh_command_runs_freshness_after_refresh(monkeypatch):
    actions = []
    monkeypatch.setattr(refresh, "incremental_refresh_uof_snapshot", lambda: actions.append("refresh"))
    def check(*, fetch_source):
        assert callable(fetch_source)
        actions.append("check")
    monkeypatch.setattr(refresh, "check_uof_freshness", check)
    refresh.main()
    assert actions == ["refresh", "check"]
