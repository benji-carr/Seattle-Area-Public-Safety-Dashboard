"""Parity with the existing CAD methodology, including projected snapshot I/O."""

import ast
import json
from pathlib import Path

import pandas as pd
import pytest
from plotly.utils import PlotlyJSONEncoder

from dashboard import crime_call_support_data as slim
from dashboard import crime_v1_1_prototypes as metrics
from dashboard import spd_snapshot
from dashboard.spd_config import (
    ARRIVAL_TIME_COLUMN, EVENT_ID_COLUMN, LAT_COL, LON_COL, ROW_ID_COLUMN, TIME_COLUMN,
)
from dashboard.spd_dashboard_data import build_response_analysis, prepare_call_snapshot


@pytest.fixture
def snapshot():
    # Deliberately unordered dispatches; earliest arrival is on a later dispatch.
    rows = [
        (" A ", "2026-09-02 10:05", "2026-09-02 10:08", "3", "Ballard"),
        ("a", "2026-09-02 10:00", "2026-09-02 10:20", "1", " DOWNTOWN "),
        ("b", "2026-09-02 11:00", "2026-09-02 11:10", None, None),
        ("b", "2026-09-02 11:01", "2026-09-02 11:15", "2", "BALLARD"),
        ("negative", "2026-09-02 12:00", "2026-09-02 11:59", "1", "downtown"),
        ("over-day", "2026-09-01 00:00", "2026-09-02 00:01", "1", "downtown"),
        ("day", "2026-09-01 00:00", "2026-09-02 00:00", "3", "ballard"),
        ("zero", "2026-09-02 12:00", "2026-09-02 12:00", "2", "downtown"),
        ("no-arrival", "2026-09-02 12:00", None, "1", "ballard"),
        (None, "2026-09-02 12:00", "2026-09-02 12:03", "1", "downtown"),
        ("bad-time", "invalid", "2026-09-02 12:03", "1", "downtown"),
        ("a", None, "2026-09-02 10:09", "2", "ballard"),
        ("previous", "2026-09-01 10:00", "2026-09-01 10:05", "1", "downtown"),
        ("bad-priority", "2026-09-02 09:00", "2026-09-02 09:15", "bad", "downtown"),
        ("blank-neighborhood", "2026-09-02 08:00", "2026-09-02 08:05", "1", " "),
        ("blank-neighborhood", "2026-09-02 08:01", None, "2", "ballard"),
        ("tie", "2026-09-02 08:00", "2026-09-02 08:05", "1", "downtown"),
        ("tie", "2026-09-02 08:00", "2026-09-02 08:06", "3", "ballard"),
    ]
    frame = pd.DataFrame(rows, columns=slim.SOURCE_COLUMNS)
    frame[ROW_ID_COLUMN] = [f"dispatch-{i}" for i in range(len(frame))]
    frame[LAT_COL], frame[LON_COL] = 47.6, -122.33
    for column in ("event_group", "dispatch_precinct", "dispatch_sector", "dispatch_beat",
                   "initial_call_type", "final_call_type"):
        frame[column] = "theft"
    return frame


@pytest.fixture
def contexts(monkeypatch, snapshot):
    prepared = prepare_call_snapshot(snapshot)
    legacy = {
        "valid_time": prepared.dropna(subset=[EVENT_ID_COLUMN, TIME_COLUMN]).copy(),
        "response_analysis": build_response_analysis(prepared),
    }
    def projected_read(directory, *, columns):
        assert columns == [EVENT_ID_COLUMN, TIME_COLUMN, ARRIVAL_TIME_COLUMN,
                           "priority", "dispatch_neighborhood"]
        return snapshot[columns].copy(), {"row_count": len(snapshot)}
    monkeypatch.setattr(slim, "load_spd_call_snapshot", projected_read)
    return slim.load_crime_call_support_context(), legacy


def test_snapshot_projection_and_default_full_load(tmp_path, snapshot, monkeypatch):
    spd_snapshot.save_spd_call_snapshot(snapshot, tmp_path, "2026-09-01")
    reads = []
    read_parquet = pd.read_parquet
    def record_read(path, **kwargs):
        reads.append(kwargs)
        return read_parquet(path, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", record_read)
    context = slim.load_crime_call_support_context(tmp_path)
    assert reads == [{"columns": slim.SOURCE_COLUMNS}]
    assert context["metadata"]["row_count"] == len(snapshot)
    full, metadata = spd_snapshot.load_spd_call_snapshot(tmp_path)
    assert reads[-1] == {}
    pd.testing.assert_frame_equal(full, snapshot)
    assert metadata["columns"] == list(snapshot.columns)


@pytest.mark.parametrize("columns", [None, slim.SOURCE_COLUMNS])
def test_snapshot_reads_still_validate_row_counts(tmp_path, snapshot, columns):
    _, metadata_path = spd_snapshot.save_spd_call_snapshot(snapshot, tmp_path, "2026-09-01")
    metadata = json.loads(metadata_path.read_text())
    metadata["row_count"] += 1
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="Row count mismatch"):
        spd_snapshot.load_spd_call_snapshot(tmp_path, columns=columns)


@pytest.mark.parametrize("columns", [None, slim.SOURCE_COLUMNS])
def test_snapshot_reads_still_validate_columns(tmp_path, snapshot, columns):
    _, metadata_path = spd_snapshot.save_spd_call_snapshot(snapshot, tmp_path, "2026-09-01")
    metadata = json.loads(metadata_path.read_text())
    metadata["columns"].remove(EVENT_ID_COLUMN)
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="Column mismatch|Projected columns"):
        spd_snapshot.load_spd_call_snapshot(tmp_path, columns=columns)


def test_call_volume_is_slim_and_matches_first_event_methodology(contexts):
    context, legacy = contexts
    actual = context["valid_time"]
    assert list(actual) == slim.CALL_VOLUME_COLUMNS
    assert actual[EVENT_ID_COLUMN].is_unique
    assert actual[[EVENT_ID_COLUMN, TIME_COLUMN]].notna().all().all()
    expected = legacy["valid_time"].sort_values(TIME_COLUMN).drop_duplicates(EVENT_ID_COLUMN)
    pd.testing.assert_frame_equal(actual, expected[slim.CALL_VOLUME_COLUMNS].reset_index(drop=True))
    assert actual[EVENT_ID_COLUMN].nunique() == legacy["valid_time"][EVENT_ID_COLUMN].nunique()
    assert actual.groupby("dispatch_neighborhood")[EVENT_ID_COLUMN].nunique().to_dict() == (
        expected.groupby("dispatch_neighborhood")[EVENT_ID_COLUMN].nunique().to_dict())
    assert set(context) == {"valid_time", "response_analysis", "metadata"}


def test_response_is_slim_and_exactly_matches_existing_methodology(contexts):
    context, legacy = contexts
    actual = context["response_analysis"]
    assert list(actual) == slim.RESPONSE_COLUMNS
    pd.testing.assert_frame_equal(actual, legacy["response_analysis"][slim.RESPONSE_COLUMNS])
    events = actual.set_index(EVENT_ID_COLUMN)
    assert events.loc["a", "queued_time"] == pd.Timestamp("2026-09-02 10:00")
    assert events.loc["a", "response_time_minutes"] == 8
    assert events.loc["a", ["priority", "dispatch_neighborhood"]].tolist() == [1, "downtown"]
    assert events.loc["b", ["priority", "dispatch_neighborhood"]].tolist() == [2, "ballard"]
    assert events.loc["blank-neighborhood", "dispatch_neighborhood"] == ""
    assert events.loc["zero", "response_time_minutes"] == 0
    assert events.loc["day", "response_time_minutes"] == 1440
    assert not {"negative", "over-day", "no-arrival", "bad-time"} & set(events.index)


@pytest.mark.parametrize("priority", list(metrics.RESPONSE_PRIORITY_OPTIONS))
@pytest.mark.parametrize("metric", ["response_time", "call_volume", "crime_volume"])
@pytest.mark.parametrize("minimum", [1, 3])
def test_crime_rankings_and_fullscreen_use_slim_context(contexts, monkeypatch, priority, metric, minimum):
    from test_crime_fullscreen import _build_stub_app
    from test_crime_v1_1_prototypes import walk
    context, legacy = contexts
    neighborhoods = ["downtown", "ballard"] + [f"empty {i}" for i in range(12)]
    crime = {
        "valid_time": pd.DataFrame({
            "offense_id": [1, 2], "offense_date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "mcpp_neighborhood": ["downtown", "ballard"], "offense_sub_category": ["theft"] * 2,
        }),
        "mcpp_boundaries": pd.DataFrame({"mcpp_neighborhood": neighborhoods}),
    }
    app = _build_stub_app(monkeypatch, calls_context=context, crime_context=crime)
    sources = metrics.prepare_multimetric_sources(legacy, crime)
    callback = app.callback_map["crime-v11-ranking-body.children"]["callback"].__wrapped__
    toggle_key = next(k for k in app.callback_map if "crime-v11-ranking-panel.className" in k)
    toggle = app.callback_map[toggle_key]["callback"].__wrapped__
    panel = "crime-v11-ranking-panel"
    for fullscreen in (False, True):
        if fullscreen:
            panel = toggle(1, panel)[0]
        actual = callback("2026-09-01", "2026-09-02", metric, priority, minimum, panel)
        expected = metrics.render_multimetric_ranking(
            sources, "2026-09-01", "2026-09-02", metric, priority, minimum, fullscreen)
        assert json.dumps(actual, cls=PlotlyJSONEncoder) == json.dumps(expected, cls=PlotlyJSONEncoder)
        if metric in ("call_volume", "crime_volume"):
            assert sum(c.__class__.__name__ == "Tr" for c in walk(actual[0])) == (15 if fullscreen else 11)


@pytest.mark.parametrize("priority", list(metrics.RESPONSE_PRIORITY_OPTIONS))
@pytest.mark.parametrize("mode", ["raw", "percent"])
def test_response_kpi_and_previous_period_are_unchanged(contexts, priority, mode):
    context, legacy = contexts
    state = {"start_date": "2026-09-02", "end_date": "2026-09-02"}
    assert json.dumps(metrics.render_response_kpi(context, state, priority, mode), cls=PlotlyJSONEncoder) == (
        json.dumps(metrics.render_response_kpi(legacy, state, priority, mode), cls=PlotlyJSONEncoder))


def test_empty_projected_snapshot(monkeypatch, snapshot):
    monkeypatch.setattr(slim, "load_spd_call_snapshot", lambda *args, **kwargs: (
        snapshot[slim.SOURCE_COLUMNS].iloc[:0].copy(), {}))
    context = slim.load_crime_call_support_context()
    for key, columns in [("valid_time", slim.CALL_VOLUME_COLUMNS), ("response_analysis", slim.RESPONSE_COLUMNS)]:
        assert context[key].empty
        assert list(context[key]) == columns


def test_production_app_has_no_full_calls_loader_or_calls_ui(monkeypatch):
    from test_crime_fullscreen import _build_stub_app
    source = (Path(__file__).parents[2] / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "load_calls_dashboard_context" not in source
    assert "spd_dashboard_data" not in source
    assert "spd_dashboard_figures" not in source
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert not {"make_landing_page", "make_calls_page", "make_page_nav"} & functions
    assert not any("calls" in name for name in functions)
    app = _build_stub_app(monkeypatch)
    assert all(key == "page-content.children" or "crime-" in key for key in app.callback_map)
