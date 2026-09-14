import pandas as pd
import pytest
import sys

from dashboard.crime_client import fetch_latest_crime_dashboard_record
from dashboard.spd_client import fetch_latest_spd_dashboard_record
from scripts.dashboard import check_data_freshness
from scripts.dashboard.check_data_freshness import (
    StaleDataError,
    assert_fresh,
    latest_dashboard_date,
    latest_source_date,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, endpoint, *, params, timeout):
        self.calls.append({"endpoint": endpoint, "params": params, "timeout": timeout})
        return FakeResponse(self.payload)


def test_spd_source_query_requests_newest_valid_dashboard_record():
    session = FakeSession([{"cad_event_original_time_queued": "2026-09-03T12:00:00.000", "cad_event_number": "123"}])
    record = fetch_latest_spd_dashboard_record(session=session)
    assert record["cad_event_number"] == "123"
    assert session.calls[0]["params"] == {
        "$select": "cad_event_original_time_queued,cad_event_number",
        "$where": "cad_event_original_time_queued IS NOT NULL AND cad_event_number IS NOT NULL",
        "$order": "cad_event_original_time_queued DESC",
        "$limit": 1,
    }


def test_crime_source_query_requests_newest_valid_dashboard_record(monkeypatch):
    calls = []

    def fake_get(endpoint, *, params, timeout):
        calls.append({"endpoint": endpoint, "params": params, "timeout": timeout})
        return FakeResponse([{"offense_date": "2026-09-03T12:00:00.000", "offense_id": "456"}])

    monkeypatch.setattr("dashboard.crime_client.requests.get", fake_get)
    assert fetch_latest_crime_dashboard_record()["offense_id"] == "456"
    assert calls[0]["params"]["$order"] == "offense_date DESC, offense_id ASC"
    assert "offense_date IS NOT NULL" in calls[0]["params"]["$where"]
    assert "offense_id IS NOT NULL" in calls[0]["params"]["$where"]


@pytest.mark.parametrize("record", [[], [{}], [{"cad_event_number": "1"}], [{"cad_event_original_time_queued": "not-a-date", "cad_event_number": "1"}]])
def test_malformed_or_empty_source_records_fail_clearly(record):
    if record == []:
        with pytest.raises(ValueError, match="no valid dashboard records"):
            fetch_latest_spd_dashboard_record(session=FakeSession(record))
    else:
        with pytest.raises(ValueError, match="missing|invalid"):
            latest_source_date(record[0], time_column="cad_event_original_time_queued", event_id_column="cad_event_number", label="SPD calls")


def test_latest_dashboard_date_uses_dashboard_validity_rules():
    frame = pd.DataFrame(
        {
            "cad_event_original_time_queued": ["2026-09-02T10:00:00", "invalid", "2026-09-04T10:00:00"],
            "cad_event_number": ["1", "2", None],
        }
    )
    assert latest_dashboard_date(frame, time_column="cad_event_original_time_queued", event_id_column="cad_event_number", label="SPD calls") == pd.Timestamp("2026-09-02")


def test_equal_calendar_dates_pass_despite_time_of_day():
    assert_fresh(label="Crime", source_date=pd.Timestamp("2026-09-03T23:59:59"), dashboard_date=pd.Timestamp("2026-09-03T00:00:00"))


def test_older_dashboard_date_fails_with_diagnostic_message():
    with pytest.raises(StaleDataError, match="SPD calls dashboard data is stale: Seattle Open Data latest day=2026-09-03; dashboard latest day=2026-09-02"):
        assert_fresh(label="SPD calls", source_date=pd.Timestamp("2026-09-03"), dashboard_date=pd.Timestamp("2026-09-02"))


def test_freshness_cli_returns_zero_for_fresh_data(monkeypatch):
    monkeypatch.setattr(check_data_freshness, "check_spd_calls_freshness", lambda: None)
    monkeypatch.setattr(check_data_freshness, "check_crime_freshness", lambda: None)
    uof_checks = []
    monkeypatch.setattr(check_data_freshness, "check_uof_freshness", lambda: uof_checks.append("uof"))
    monkeypatch.setattr(sys, "argv", ["check_data_freshness", "--dataset", "all"])
    assert check_data_freshness.main() is None
    assert uof_checks == ["uof"]


def test_freshness_cli_returns_two_only_for_stale_data(monkeypatch, capsys):
    monkeypatch.setattr(check_data_freshness, "check_spd_calls_freshness", lambda: (_ for _ in ()).throw(StaleDataError("calls stale")))
    monkeypatch.setattr(sys, "argv", ["check_data_freshness", "--dataset", "calls"])
    with pytest.raises(SystemExit) as error:
        check_data_freshness.main()
    assert error.value.code == 2
    assert "calls stale" in capsys.readouterr().err


def test_freshness_cli_returns_one_for_checker_failures(monkeypatch, capsys):
    monkeypatch.setattr(check_data_freshness, "check_crime_freshness", lambda: (_ for _ in ()).throw(ValueError("API response malformed")))
    monkeypatch.setattr(sys, "argv", ["check_data_freshness", "--dataset", "crime"])
    with pytest.raises(SystemExit) as error:
        check_data_freshness.main()
    assert error.value.code == 1
    assert "Freshness check failed: API response malformed" in capsys.readouterr().err


def test_combined_cli_reports_all_stale_datasets(monkeypatch, capsys):
    monkeypatch.setattr(check_data_freshness, "check_spd_calls_freshness", lambda: (_ for _ in ()).throw(StaleDataError("calls stale")))
    monkeypatch.setattr(check_data_freshness, "check_crime_freshness", lambda: (_ for _ in ()).throw(StaleDataError("crime stale")))
    monkeypatch.setattr(check_data_freshness, "check_uof_freshness", lambda: (_ for _ in ()).throw(StaleDataError("uof stale")))
    monkeypatch.setattr(sys, "argv", ["check_data_freshness", "--dataset", "all"])
    with pytest.raises(SystemExit) as error:
        check_data_freshness.main()
    assert error.value.code == 2
    assert "calls stale\ncrime stale\nuof stale" in capsys.readouterr().err


@pytest.mark.parametrize("source_day,snapshot_day,stale", [
    ("2026-09-01T23:59:59", "2026-09-01T00:00:00", False),
    ("2026-09-02", "2026-09-01", True),
])
def test_uof_freshness_uses_valid_source_and_snapshot_dates(monkeypatch, source_day, snapshot_day, stale):
    snapshot = pd.DataFrame({"uniqueid": ["1", " "], "occured_date_time": [snapshot_day, "2026-09-03"]})
    monkeypatch.setattr(check_data_freshness, "load_uof_snapshot", lambda _: (snapshot, {}))
    fetch = lambda: {"uniqueid": "2", "occured_date_time": source_day}
    if stale:
        with pytest.raises(StaleDataError, match="UOF dashboard data is stale"):
            check_data_freshness.check_uof_freshness(fetch_source=fetch)
    else:
        source, dashboard = check_data_freshness.check_uof_freshness(fetch_source=fetch)
        assert source == dashboard == pd.Timestamp("2026-09-01")


@pytest.mark.parametrize("record", [None, [], {}, {"uniqueid": "1"},
                                      {"uniqueid": " ", "occured_date_time": "2026-09-01"},
                                      {"uniqueid": "1", "occured_date_time": "bad"}])
def test_uof_freshness_rejects_malformed_source(record):
    with pytest.raises(ValueError, match="object|missing|invalid"):
        check_data_freshness.check_uof_freshness(fetch_source=lambda: record)


@pytest.mark.parametrize("snapshot", [pd.DataFrame(),
    pd.DataFrame({"uniqueid": [None], "occured_date_time": ["2026-09-01"]}),
    pd.DataFrame({"uniqueid": ["1"], "occured_date_time": ["bad"]}),
])
def test_uof_freshness_rejects_malformed_snapshot(monkeypatch, snapshot):
    monkeypatch.setattr(check_data_freshness, "load_uof_snapshot", lambda _: (snapshot, {}))
    with pytest.raises(ValueError, match="missing|no valid"):
        check_data_freshness.check_uof_freshness(
            fetch_source=lambda: {"uniqueid": "1", "occured_date_time": "2026-09-01"},
        )


def test_uof_freshness_cli_choice(monkeypatch):
    checks = []
    monkeypatch.setattr(check_data_freshness, "check_uof_freshness", lambda: checks.append("uof"))
    monkeypatch.setattr(sys, "argv", ["check_data_freshness", "--dataset", "uof"])
    check_data_freshness.main()
    assert checks == ["uof"]
