"""Workbook methodology parity and the temporary scrolling-route composition."""
import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dashboard import crime_v1_1_prototypes as prototype
from dashboard.analysis_windows import get_previous_period
from dashboard.crime_classification import CANONICAL_CRIME_TYPES
from dashboard.crime_filters import filter_crime_records
from test_crime_fullscreen import _analysis_state, _build_stub_app
from test_crime_map_interaction import find_component


@pytest.fixture
def crime():
    persons, property_, society = CANONICAL_CRIME_TYPES
    return pd.DataFrame({
        "offense_id": ["history", "previous", "a", "a", "b", "c"],
        "report_number": ["shared"] * 6,
        "offense_date": pd.to_datetime(["2026-08-01", "2026-09-01", "2026-09-02",
                                        "2026-09-02", "2026-09-02", "2026-09-02"]),
        "event_importance_bin": [property_] * 5 + [persons],
        "offense_sub_category": ["theft"] * 4 + ["fraud", "assault"],
        "mcpp_neighborhood": ["downtown"] * 4 + ["ballard", "ballard"],
    })


@pytest.fixture
def state():
    return {"start_date": "2026-09-02", "end_date": "2026-09-02",
            "crime_categories": [CANONICAL_CRIME_TYPES[1]],
            "crime_subcategories": ["theft"], "neighborhoods": ["downtown"]}


@pytest.fixture
def response():
    return pd.DataFrame({
        "cad_event_number": range(9),
        "queued_time": pd.to_datetime(["2026-08-30", "2026-08-31"] + ["2026-09-01 23:59"] * 7, format="mixed"),
        "priority": [1, 1, 1, 2, 3, 1, 1, 4, 1],
        "response_time_minutes": [2., 4., 10., 10., 10., 10., 20., 100., 30.],
        "dispatch_neighborhood": ["alpha", "alpha", "alpha", "alpha", "beta", "beta", "gamma", "gamma", None],
    })


def workbook_functions():
    """Execute only the three pure reference functions, never notebook setup/apps."""
    notebook = json.loads((Path(__file__).resolve().parents[2] / "notebooks/v1_1_figure_workbook.ipynb").read_text(encoding="utf-8"))
    names = {"get_category_counts", "get_response_kpi_values", "build_neighborhood_response_ranking"}
    functions = []
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            functions.extend(node for node in ast.parse("".join(cell["source"])).body
                             if isinstance(node, ast.FunctionDef) and node.name in names)
    assert {f.name for f in functions} == names
    namespace = {"pd": pd, "np": np, "get_previous_period": get_previous_period,
                 "CANONICAL_CRIME_TYPES": CANONICAL_CRIME_TYPES, "filter_crime_records": filter_crime_records}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "workbook-reference", "exec"), namespace)
    return namespace


def test_crime_count_uses_all_filters_unique_offenses_and_equal_previous_period(crime, state):
    current, previous, period = prototype.prepare_crime_count(crime, state)
    assert (current, previous) == (1, 1)
    assert period == (pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01"))
    assert prototype.prepare_crime_count(crime, {**state, "crime_subcategories": []})[:2] == (1, 1)
    assert prototype.prepare_crime_count(crime, {**state, "crime_subcategories": [], "neighborhoods": []})[:2] == (2, 1)
    assert prototype.prepare_crime_count(crime, {**state, "start_date": "2026-08-01", "end_date": "2026-08-01"})[1:] == (None, None)


def test_category_comparison_retains_workbook_dates_only_scope(crime, state):
    current, previous, period = prototype.prepare_category_comparison(crime, state)
    assert list(current.values()) == [1, 2, 0]
    assert list(previous.values()) == [0, 1, 0]
    reference = workbook_functions()["get_category_counts"]
    dates_only = {**state, "crime_subcategories": [], "neighborhoods": []}
    assert current == reference(crime, dates_only)
    assert prototype.prepare_category_comparison(crime, {**state, "neighborhoods": ["none"]}) == (current, previous, period)


@pytest.mark.parametrize("priorities", [[1, 2, 3], [1, 2], [1]])
def test_response_kpi_and_ranking_match_workbook_functions(response, priorities):
    reference = workbook_functions()
    args = (response, "2026-09-01", "2026-09-01", priorities,
            pd.Timestamp("2026-08-30"), pd.Timestamp("2026-09-01"))
    assert prototype.get_response_kpi_values(*args) == reference["get_response_kpi_values"](*args)
    for minimum in [1, 2, 5]:
        actual = prototype.build_neighborhood_response_ranking(*args[:4], min_events=minimum)
        expected = reference["build_neighborhood_response_ranking"](*args[:4], min_events=minimum)
        pd.testing.assert_frame_equal(actual, expected)


def test_response_uses_own_dates_and_priority_only_with_visible_scope(response, state):
    original = response.copy(deep=True)
    source, period, history = prototype.prepare_response_period({"response_analysis": response}, state)
    assert period == ("2026-09-01", "2026-09-01")  # Crime date is later than CAD coverage.
    values = prototype.get_response_kpi_values(source, *period, [1, 2, 3], *history)
    assert values["current_events"] == 6  # Includes the qualified event with no neighborhood.
    assert values["current_median"] == 10
    assert values["previous_median"] == 4
    assert "Sep 01, 2026" in prototype.response_scope_note(period, history)
    assert "do not filter" in prototype.response_scope_note(period, history)
    ranking = prototype.build_neighborhood_response_ranking(source, *period, [1, 2, 3])
    assert ranking.dispatch_neighborhood.tolist() == ["gamma", "alpha", "beta"]
    assert ranking.qualified_events.tolist() == [1, 2, 2]
    assert prototype.build_neighborhood_response_ranking(source, *period, [1, 2, 3], min_events=5).empty
    pd.testing.assert_frame_equal(response, original)


def test_workbook_change_presentation_and_missing_values():
    assert prototype._change(4, 2, "percent")[0] == "↗ 100.0%"
    assert prototype._change(4, 0, "percent")[0] == "↗ —"
    assert prototype._change(4, 2, "raw")[2] == "#22c55e"
    assert prototype._change(4, 2, "raw", response=True)[2] == "#f97316"
    assert prototype._change(2, 4, "raw", response=True)[2] == "#22c55e"
    assert prototype._change(np.nan, 4, "raw", response=True)[0] == "→ —"


def walk(node):
    yield node
    children = getattr(node, "children", None)
    for child in children if isinstance(children, (tuple, list)) else [children]:
        if child is not None:
            yield from walk(child)


@pytest.mark.parametrize("route", ["/crime", "/crime/", "/crime-v1-1", "/crime-v1-1/"])
def test_routes_share_map_controls_fullscreen_and_have_no_duplicate_ids(monkeypatch, route):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__(route)
    ids = [str(c.id) for c in walk(page) if getattr(c, "id", None) is not None]
    assert len(ids) == len(set(ids))
    assert {"crime-analysis-state-store", "crime-analysis-start-date-input", "crime-analysis-end-date-input",
            "crime-category-filter", "crime-subcategory-filter", "crime-neighborhood-filter",
            "crime-map-figure", "crime-daily-figure", "crime-expand-map-button",
            "crime-fullscreen-overlay", "crime-fullscreen-figure-store", "crime-fullscreen-figure",
            "crime-close-fullscreen-button", "crime-map-region-toggle"} <= set(ids)
    prototype_ids = {"crime-v11-count-body", "crime-v11-category-body", "crime-v11-response-body", "crime-v11-ranking-body"}
    if "v1-1" in route:
        assert prototype_ids <= set(ids)
        assert page.className == "crime-v11-page"
        assert not any("100dvh" in str(getattr(c, "style", {})) for c in walk(page))
        assert find_component(page, "crime-v11-ranking-min-events").value == 5
    else:
        assert prototype_ids.isdisjoint(ids)
        assert any(getattr(c, "children", None) == "Seattle Crime Dashboard" for c in walk(page))
    assert app.server.test_client().get(route).status_code == 200
    assert app.server.test_client().get("/_dash-dependencies").status_code == 200


@pytest.mark.parametrize("target,helper,extra", [
    ("crime-v11-count-body", "render_crime_count", ["percent"]),
    ("crime-v11-category-body", "render_category_comparison", ["raw"]),
    ("crime-v11-response-body", "render_response_kpi", ["Priority 1 only", "percent"]),
    ("crime-v11-ranking-body", "render_response_ranking", ["Priority 1–2", 5]),
])
def test_prototype_callbacks_forward_shared_state_and_local_controls(monkeypatch, target, helper, extra):
    app = _build_stub_app(monkeypatch)
    captured = []
    monkeypatch.setattr(prototype, helper, lambda *args: captured.append(args) or "rendered")
    key = next(k for k in app.callback_map if target in k)
    callback = app.callback_map[key]
    assert callback["inputs"][0] == {"id": "crime-analysis-state-store", "property": "data"}
    state = _analysis_state(["theft"], ["downtown"])
    assert callback["callback"].__wrapped__(state, *extra) == "rendered"
    assert captured[0][1] == state
    assert list(captured[0][2:]) == extra
