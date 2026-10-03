"""Server aggregation must retain event-level table and KPI methodology."""

import re

import pandas as pd
import pytest

from dashboard import call_metrics_refresh as refresh
from dashboard.crime_call_support_data import (
    SOURCE_COLUMNS, METRICS_SUBDIRECTORY, build_crime_call_support_context,
    load_crime_call_support_context,
)
from dashboard import crime_v1_1_prototypes as metrics
from dashboard.spd_config import EVENT_ID_COLUMN, TIME_COLUMN, ARRIVAL_TIME_COLUMN
from dashboard.spd_snapshot import save_spd_call_snapshot
from scripts.dashboard import refresh_call_metrics as entry
from scripts.dashboard.check_data_freshness import check_spd_calls_freshness, StaleDataError


@pytest.fixture
def source():
    return pd.DataFrame([
        ("invariant", "2026-01-31 23:50", "2026-02-01 00:20", "1", "BALLARD"),
        ("invariant", "2026-02-01 00:01", "2026-02-01 00:05", "1", "BALLARD"),
        ("mixed", "2026-02-01 10:00", "2026-02-01 10:20", None, None),
        ("mixed", "2026-02-01 10:01", "2026-02-01 10:08", "2", "BALLARD"),
        ("changed", "2026-02-01 11:00", "2026-02-01 11:20", "1", "DOWNTOWN"),
        ("changed", "2026-02-01 11:02", "2026-02-01 11:06", "3", "BALLARD"),
        (" A ", "2026-02-01 12:00", "2026-02-01 12:10", "1", "DOWNTOWN"),
        ("a", "2026-02-01 12:01", "2026-02-01 12:08", "2", "BALLARD"),
        ("no-arrival", "2026-02-01 13:00", None, "1", "BALLARD"),
        ("negative", "2026-02-01 14:00", "2026-02-01 13:59", "1", "BALLARD"),
        ("day", "2026-02-01 00:00", "2026-02-02 00:00", "3", "BALLARD"),
        ("over-day", "2026-02-01 00:00", "2026-02-02 00:01", "1", "BALLARD"),
        ("unknown-area", "2026-02-01 15:00", "2026-02-01 15:04", "1", None),
        ("invalid-priority", "2026-02-01 16:00", "2026-02-01 16:07", "bad", "BALLARD"),
        ("quote'id", "2026-02-01 17:00", "2026-02-01 17:10", "1", "BALLARD"),
        ("quote'id", "2026-02-01 17:01", "2026-02-01 17:05", "2", "DOWNTOWN"),
        (None, "2026-02-01 18:00", "2026-02-01 18:05", "1", "BALLARD"),
    ], columns=SOURCE_COLUMNS).assign(**{
        TIME_COLUMN: lambda x: pd.to_datetime(x[TIME_COLUMN]),
        ARRIVAL_TIME_COLUMN: lambda x: pd.to_datetime(x[ARRIVAL_TIME_COLUMN]),
    })


def install_api(monkeypatch, source):
    calls = []

    def request(*, params, **kwargs):
        calls.append(params)
        query = params.get("$query", params.get("$where"))
        start, end = re.findall(r"'([0-9-]+)T00:00:00.000'", query)
        frame = source.loc[source[TIME_COLUMN].ge(start) & source[TIME_COLUMN].lt(end)
                           & source[EVENT_ID_COLUMN].notna()].copy()
        if "$query" in params:
            frame = frame.groupby(refresh.GROUP_COLUMNS, dropna=False, as_index=False).agg(
                queued_min=(TIME_COLUMN, "min"), arrived_min=(ARRIVAL_TIME_COLUMN, "min"),
                latest_queued_time=(TIME_COLUMN, "max"))
            frame = frame.sort_values(refresh.GROUP_COLUMNS)
            limit, offset = map(int, re.search(r"LIMIT (\d+) OFFSET (\d+)", query).groups())
        else:
            literals = query.split(" IN (", 1)[1][:-1]
            ids = [v.replace("''", "'") for v in re.findall(r"'((?:[^']|'')*)'", literals)]
            frame = frame.loc[frame[EVENT_ID_COLUMN].isin(ids)].sort_values(
                [TIME_COLUMN, EVENT_ID_COLUMN], ascending=[False, True])
            limit, offset = params["$limit"], params["$offset"]
        frame = frame.iloc[offset:offset + limit]
        rows = [{k: v for k, v in row.items() if not pd.isna(v)} for row in frame.to_dict("records")]
        return rows, 1

    monkeypatch.setattr(refresh, "_request_with_retries", request)
    return calls


def event_sorted(frame):
    return frame.sort_values(EVENT_ID_COLUMN).reset_index(drop=True)


def test_aggregate_fallback_parity_and_exhaustive_pagination(monkeypatch, source):
    requests = install_api(monkeypatch, source)
    compact, stats = refresh.fetch_call_metric_source("2026-01-01", "2026-03-01", page_size=2)
    full = build_crime_call_support_context(source, {})
    reduced = build_crime_call_support_context(compact, {})
    for key in ("valid_time", "response_analysis"):
        pd.testing.assert_frame_equal(event_sorted(full[key]), event_sorted(reduced[key]), check_dtype=False)
    assert stats["fallback_events"] == 5  # mixed, changed, two normalized spellings, quote'id
    assert len(compact) < len(source)
    assert any("OFFSET 2" in p.get("$query", "") for p in requests)
    assert any(p.get("$offset", 0) >= 2 for p in requests if "$query" not in p)
    assert any("quote''id" in p.get("$where", "") for p in requests)
    assert compact[refresh.LATEST_TIME_COLUMN].max() == source.loc[source[EVENT_ID_COLUMN].notna(), TIME_COLUMN].max()
    responses = reduced["response_analysis"].set_index(EVENT_ID_COLUMN)
    assert responses.loc["mixed", "response_time_minutes"] == 8
    assert responses.loc["mixed", "dispatch_neighborhood"] == "ballard"
    assert pd.isna(reduced["valid_time"].set_index(EVENT_ID_COLUMN).loc["mixed", "dispatch_neighborhood"])
    assert "no-arrival" not in responses.index
    assert "no-arrival" in set(reduced["valid_time"][EVENT_ID_COLUMN])
    assert "day" in responses.index and "over-day" not in responses.index

    # Compare the actual metric functions, including all three priority scopes,
    # neighborhood exclusions, minimum event thresholds and the prior KPI period.
    crime = pd.DataFrame({"offense_id": ["crime-a", "crime-b"],
                          "offense_date": pd.to_datetime(["2026-01-31", "2026-02-01"]),
                          "mcpp_neighborhood": ["ballard", "downtown"]})
    crime_context = {"valid_time": crime,
                     "mcpp_boundaries": pd.DataFrame({"mcpp_neighborhood": ["ballard", "downtown"]})}
    a = metrics.prepare_multimetric_sources(full, crime_context)
    b = metrics.prepare_multimetric_sources(reduced, crime_context)
    for scope, priorities in metrics.RESPONSE_PRIORITY_OPTIONS.items():
        for metric in ("response_time", "call_volume", "crime_volume"):
            for minimum in (1, 3):
                x, xp = metrics.prepare_multimetric_ranking(a, "2026-02-01", "2026-02-01", metric, scope, minimum)
                y, yp = metrics.prepare_multimetric_ranking(b, "2026-02-01", "2026-02-01", metric, scope, minimum)
                assert xp == yp
                pd.testing.assert_frame_equal(x, y)
        args = ("2026-02-01", "2026-02-01", priorities, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-02-01"))
        x = metrics.get_response_kpi_values(full["response_analysis"], *args)
        y = metrics.get_response_kpi_values(reduced["response_analysis"], *args)
        for key in x:
            if "median" in key:
                assert x[key] == pytest.approx(y[key], nan_ok=True)
            else:
                assert x[key] == y[key]


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"offset": -1}, {"limit": True}])
def test_query_validation(kwargs):
    with pytest.raises(ValueError):
        refresh.build_metric_query("2026-01-01", "2026-02-01", **kwargs)


def test_month_boundaries_and_query_contract():
    assert list(refresh.month_windows("2025-12-30", "2026-02-02")) == [
        ("2025-12-30", "2026-01-01"), ("2026-01-01", "2026-02-01"), ("2026-02-01", "2026-02-02")]
    query = refresh.build_metric_query("2026-01-01", "2026-02-01")["$query"]
    assert "AS queued_min" in query and "AS arrived_min" in query
    assert "priority IN" not in query  # call volume includes every priority
    assert "GROUP BY cad_event_number,priority,dispatch_neighborhood" in query
    with pytest.raises(ValueError):
        list(refresh.month_windows("2026-02-01", "2026-02-01"))


def test_compact_snapshot_preferred_and_freshness_uses_max_queue(tmp_path, source):
    raw = source.iloc[[0]].copy()
    save_spd_call_snapshot(raw, tmp_path, "2026-01-01")
    compact = raw.copy()
    compact[refresh.LATEST_TIME_COLUMN] = pd.Timestamp("2026-02-01 00:01")
    save_spd_call_snapshot(compact, tmp_path / METRICS_SUBDIRECTORY, "2026-01-01")
    context = load_crime_call_support_context(tmp_path)
    assert len(context["valid_time"]) == 1
    latest = {EVENT_ID_COLUMN: "invariant", TIME_COLUMN: "2026-02-01 00:01"}
    check_spd_calls_freshness(snapshot_dir=tmp_path, fetch_source=lambda: latest)
    with pytest.raises(StaleDataError):
        check_spd_calls_freshness(snapshot_dir=tmp_path, fetch_source=lambda: {**latest, TIME_COLUMN: "2026-02-02"})


def test_partial_compact_snapshot_does_not_fall_back(tmp_path, source):
    save_spd_call_snapshot(source, tmp_path, "2026-01-01")
    (tmp_path / METRICS_SUBDIRECTORY).mkdir()
    with pytest.raises(FileNotFoundError):
        load_crime_call_support_context(tmp_path)


def test_conflicting_values_at_identical_first_time_are_not_silently_reordered(monkeypatch, source):
    source = source.loc[source[EVENT_ID_COLUMN] == "changed"].copy()
    source[TIME_COLUMN] = pd.Timestamp("2026-02-01 11:00")
    install_api(monkeypatch, source)
    with pytest.raises(ValueError, match="Ambiguous first"):
        refresh.fetch_call_metric_source("2026-02-01", "2026-02-02")


def test_duplicate_aggregate_groups_abort_pagination(monkeypatch):
    row = {EVENT_ID_COLUMN: "a", "priority": "1", "dispatch_neighborhood": "BALLARD",
           "queued_min": "2026-02-01T00:00:00", "latest_queued_time": "2026-02-01T00:00:00"}
    monkeypatch.setattr(refresh, "_request_with_retries", lambda **kwargs: ([row], 1))
    with pytest.raises(ValueError, match="Duplicate aggregate groups"):
        refresh.fetch_call_metric_source("2026-02-01", "2026-02-02", page_size=1)


def test_refresh_reconciles_deletions_and_failure_preserves_snapshot(tmp_path, monkeypatch, source):
    monkeypatch.setattr(entry, "fetch_latest_spd_dashboard_record", lambda **kw: {TIME_COLUMN: "2026-02-01"})
    source = source.loc[source[EVENT_ID_COLUMN].notna()].copy()
    source[refresh.LATEST_TIME_COLUMN] = source[TIME_COLUMN]
    def fetch(start, end):
        assert start == "2024-01-29" and end == "2026-02-02"
        return source, {"query_request_count": 2}
    monkeypatch.setattr(entry, "fetch_call_metric_source", fetch)
    snapshot, metadata = entry.refresh_call_metrics(tmp_path)
    assert snapshot.exists() and metadata.exists()
    source = source.loc[source[EVENT_ID_COLUMN] != "changed"].copy()
    entry.refresh_call_metrics(tmp_path)
    assert "changed" not in set(load_crime_call_support_context(tmp_path)["valid_time"][EVENT_ID_COLUMN])
    before = snapshot.read_bytes(), metadata.read_bytes()
    def fail(*args, **kwargs):
        raise TimeoutError("source failed")
    monkeypatch.setattr(entry, "fetch_call_metric_source", fail)
    with pytest.raises(TimeoutError):
        entry.refresh_call_metrics(tmp_path)
    assert before == (snapshot.read_bytes(), metadata.read_bytes())
