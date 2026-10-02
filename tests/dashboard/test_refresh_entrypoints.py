from unittest.mock import Mock, call

import pytest
import requests

from scripts.dashboard import refresh_crime_data, refresh_spd_data


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Refresh entrypoint tests must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", unexpected_request)


@pytest.mark.parametrize("module,prefix,output_name", [
    (refresh_spd_data, "spd_call", "CALL_OUTPUT_DIRECTORY"),
    (refresh_crime_data, "crime", "CRIME_OUTPUT_DIR"),
], ids=["spd", "crime"])
def test_refresh_command_runs_full_refresh_then_verifies_freshness(
    monkeypatch, module, prefix, output_name,
):
    assert module.DEFAULT_TIMEOUT == 120.0
    assert module.DEFAULT_MAX_RETRIES == 5
    assert module.DEFAULT_RETRY_BACKOFF_SECONDS == 5.0
    assert module.DEFAULT_PAGE_SIZE == 5000
    assert module.DEFAULT_MAX_PAGES is None
    assert module.DEFAULT_ROLLING_WINDOW_DAYS == 734
    network_settings = dict(
        timeout=module.DEFAULT_TIMEOUT,
        max_retries=module.DEFAULT_MAX_RETRIES,
        retry_backoff_seconds=module.DEFAULT_RETRY_BACKOFF_SECONDS,
    )
    start_date = "2024-08-28"
    actions = Mock()
    actions.start.return_value = start_date
    latest = Mock(return_value={"source": "mocked"})
    incremental = Mock(side_effect=AssertionError("main must not use incremental refresh"))
    check_name = "check_spd_calls_freshness" if prefix == "spd_call" else "check_crime_freshness"
    latest_name = "fetch_latest_spd_dashboard_record" if prefix == "spd_call" else "fetch_latest_crime_dashboard_record"

    def check_freshness(*, fetch_source):
        assert callable(fetch_source)
        assert fetch_source() is latest.return_value

    actions.check.side_effect = check_freshness
    monkeypatch.setattr(module, "get_default_start_date", actions.start)
    monkeypatch.setattr(module, f"full_refresh_{prefix}_snapshot", actions.refresh)
    monkeypatch.setattr(module, f"incremental_refresh_{prefix}_snapshot", incremental)
    monkeypatch.setattr(module, check_name, actions.check)
    monkeypatch.setattr(module, latest_name, latest)
    monkeypatch.setattr(module.logging, "basicConfig", Mock())

    module.main()

    refresh_settings = dict(
        start_date=start_date,
        page_size=module.DEFAULT_PAGE_SIZE,
        max_pages=module.DEFAULT_MAX_PAGES,
        output_directory=getattr(module, output_name),
        **network_settings,
    )
    if prefix == "crime":
        assert module.EVENT_DATE_COLUMN == "offense_date"
        refresh_settings["date_column"] = module.EVENT_DATE_COLUMN
    actions.start.assert_called_once_with(
        rolling_window_days=module.DEFAULT_ROLLING_WINDOW_DAYS, **network_settings,
    )
    actions.refresh.assert_called_once_with(**refresh_settings)
    actions.check.assert_called_once()
    assert actions.mock_calls == [
        call.start(rolling_window_days=module.DEFAULT_ROLLING_WINDOW_DAYS, **network_settings),
        call.refresh(**refresh_settings),
        call.check(fetch_source=actions.check.call_args.kwargs["fetch_source"]),
    ]
    latest.assert_called_once_with(**network_settings)
    incremental.assert_not_called()
