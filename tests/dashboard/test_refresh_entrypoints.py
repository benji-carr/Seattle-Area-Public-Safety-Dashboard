from unittest.mock import Mock, call

import pytest
import requests
import pandas as pd

from scripts.dashboard import refresh_crime_data, refresh_spd_data


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Refresh entrypoint tests must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_request)

def test_spd_command_runs_incremental_refresh_then_verifies_freshness(
    monkeypatch,
):
    module = refresh_spd_data
    actions = Mock()
    full = Mock(
        side_effect=AssertionError(
            "main must delegate bootstrap to incremental refresh"
        )
    )
    latest = Mock(return_value={"source": "mocked"})

    # Your current main() still calculates this before invoking incremental.
    actions.start.return_value = "2024-08-28"

    def check_freshness(*, fetch_source):
        assert callable(fetch_source)
        assert fetch_source() is latest.return_value

    actions.check.side_effect = check_freshness

    monkeypatch.setattr(module, "get_default_start_date", actions.start)
    monkeypatch.setattr(
        module, "incremental_refresh_spd_call_snapshot", actions.refresh
    )
    monkeypatch.setattr(module, "full_refresh_spd_call_snapshot", full)
    monkeypatch.setattr(module, "check_spd_calls_freshness", actions.check)
    monkeypatch.setattr(module, "fetch_latest_spd_dashboard_record", latest)
    monkeypatch.setattr(module.logging, "basicConfig", Mock())

    module.main()

    network_settings = dict(
        timeout=120.0,
        max_retries=5,
        retry_backoff_seconds=5.0,
    )
    refresh_settings = dict(
        output_directory=module.CALL_OUTPUT_DIRECTORY,
        rolling_window_days=734,
        overlap_days=200,
        page_size=5000,
        **network_settings,
    )

    actions.start.assert_called_once_with(
        rolling_window_days=734,
        **network_settings,
    )
    actions.refresh.assert_called_once_with(**refresh_settings)
    actions.check.assert_called_once()
    latest.assert_called_once_with(**network_settings)
    full.assert_not_called()

    assert actions.mock_calls == [
        call.start(rolling_window_days=734, **network_settings),
        call.refresh(**refresh_settings),
        call.check(
            fetch_source=actions.check.call_args.kwargs["fetch_source"]
        ),
    ]


def test_crime_command_runs_incremental_refresh_then_verifies_freshness(monkeypatch):
    module = refresh_crime_data
    actions = Mock()
    full = Mock(side_effect=AssertionError("main must delegate bootstrap to incremental refresh"))
    start = Mock(side_effect=AssertionError("main must not query the full-refresh start date"))
    latest = Mock(return_value={"offense_date": "2026-09-01", "offense_id": "a"})

    def check(*, fetch_source):
        assert fetch_source() == latest.return_value

    actions.check.side_effect = check
    monkeypatch.setattr(module, "incremental_refresh_crime_snapshot", actions.refresh)
    monkeypatch.setattr(module, "full_refresh_crime_snapshot", full)
    monkeypatch.setattr(module, "get_default_start_date", start)
    monkeypatch.setattr(module, "check_crime_freshness", actions.check)
    monkeypatch.setattr(module, "fetch_latest_crime_dashboard_record", latest)
    module.main()

    assert actions.mock_calls == [
        call.refresh(output_directory=module.CRIME_OUTPUT_DIR, rolling_window_days=734,
                     overlap_days=200, page_size=5000, timeout=120.0,
                     max_retries=5, retry_backoff_seconds=5.0),
        call.check(fetch_source=actions.check.call_args.kwargs["fetch_source"]),
    ]
    latest.assert_called_once_with(timeout=120.0, max_retries=5, retry_backoff_seconds=5.0)
    full.assert_not_called()
    start.assert_not_called()


def test_crime_incremental_overlap_deduplication_and_offense_retention(monkeypatch, tmp_path):
    module = refresh_crime_data
    old = pd.DataFrame([
        {"offense_id": "updated", "offense_date": "2026-09-01 12:00", "report_date_time": "2026-09-01"},
        {"offense_id": "old", "offense_date": "2024-01-01 12:00", "report_date_time": "2024-01-01"},
    ])
    new = pd.DataFrame([
        {"offense_id": "updated", "offense_date": "2026-09-01 13:00", "report_date_time": "2026-09-02"},
        {"offense_id": "new", "offense_date": "2026-09-02 12:00", "report_date_time": "2026-09-02"},
    ])
    fetch = Mock(return_value=new)
    save = Mock(return_value=(tmp_path / "snapshot", tmp_path / "metadata"))
    monkeypatch.setattr(module, "load_crime_snapshot", lambda _: (old, {}))
    monkeypatch.setattr(module, "load_crime_dataset", fetch)
    monkeypatch.setattr(module, "save_crime_snapshot", save)
    module.incremental_refresh_crime_snapshot(tmp_path)
    assert fetch.call_args.kwargs["start_date"] == "2026-02-13"
    assert fetch.call_args.kwargs["date_column"] == "report_date_time"
    assert fetch.call_args.kwargs["max_pages"] is None
    result = save.call_args.kwargs["df"].set_index("offense_id")
    assert set(result.index) == {"updated", "new"}
    assert result.loc["updated", "offense_date"] == pd.Timestamp("2026-09-01 13:00")
    cutoff = pd.Timestamp("2026-09-02 12:00") - pd.Timedelta(days=734)
    assert save.call_args.kwargs["source_start_date"] == cutoff.date().isoformat()
    assert save.call_args.kwargs["source_date_column"] == "offense_date"


def test_crime_incremental_bootstraps_missing_snapshot(monkeypatch, tmp_path):
    module = refresh_crime_data
    monkeypatch.setattr(module, "load_crime_snapshot", Mock(side_effect=FileNotFoundError))
    monkeypatch.setattr(module, "get_default_start_date", Mock(return_value="2024-08-29"))
    full = Mock()
    monkeypatch.setattr(module, "full_refresh_crime_snapshot", full)
    module.incremental_refresh_crime_snapshot(tmp_path)
    full.assert_called_once_with(
        start_date="2024-08-29", page_size=5000, max_pages=None, timeout=120.0,
        output_directory=tmp_path, date_column="offense_date", max_retries=5,
        retry_backoff_seconds=5.0,
    )
