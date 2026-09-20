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
import app as app_module
from dashboard.uof_dashboard_data import derive_ois_events


@pytest.fixture
def uof_context():
    frame = pd.DataFrame({
        "occured_date_time": ["2025-09-01", "2025-09-02", "2025-09-02", "2026-09-02 23:59",
                              "2026-09-02", "2026-09-03", "invalid", "2026-09-02", "2026-09-02"],
        "incident_num": ["old", "A", "A", "B", "C", "future", "invalid", "E", " "],
        "incident_type": ["OIS"] * 7 + ["Type I", "Type I"],
        "beat": ["Q1", "q1", " Q1 ", "q1", "Q1", "Q1", "Q1", "Q2", "Q2"],
        "officer_id": list("abcdefghi"), "subject_id": list("abcdefghi"),
    })
    return {"df": frame, "ois_events": derive_ois_events(frame),
            "latest_available_date": pd.Timestamp("2026-12-31")}


@pytest.fixture(autouse=True)
def stub_uof_loader(monkeypatch, uof_context):
    monkeypatch.setattr(app_module, "load_uof_dashboard_context", lambda: uof_context)


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
    """Execute only pure reference functions, never notebook setup/apps."""
    notebook = json.loads((Path(__file__).resolve().parents[2] / "notebooks/v1_1_figure_workbook.ipynb").read_text(encoding="utf-8"))
    names = {"get_category_counts", "get_response_kpi_values", "build_neighborhood_response_ranking",
             "get_citywide_crime_rate"}
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
    prototype_ids = {"crime-v11-count-body", "crime-v11-category-body", "crime-v11-response-body", "crime-v11-ranking-body",
                     "crime-v11-rate-body", "crime-v11-uof-card", "crime-v11-ois-card"}
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
    ("crime-v11-rate-body", "render_crime_rate", ["percent"]),
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


def test_rate_matches_workbook_direct_population_and_ignores_only_neighborhood(crime, state):
    state = {**state, "crime_subcategories": []}
    context = {"valid_time": crime, "city_population": 200_000,
               "neighborhood_population": pd.DataFrame({"population": [1, 2]})}
    reference = workbook_functions()["get_citywide_crime_rate"]
    for neighborhoods in [[], ["downtown"], ["no match"]]:
        selected = {**state, "neighborhoods": neighborhoods}
        assert prototype.get_citywide_crime_rate(crime, selected, context["city_population"]) == 1.0
        assert prototype.get_citywide_crime_rate(crime, selected, context["city_population"]) == reference(crime, selected, 200_000)
    assert prototype.prepare_crime_count(crime, state)[0] == 1  # Count still honors geography.
    body, style = prototype.render_crime_rate(context, state, "raw")
    assert body[0].children == "1.0"
    assert body[1].children == "↗ 0.5"
    assert "0.5 per 100k" in body[-1].children
    assert style["borderColor"] == "#22c55e"
    assert style["background"] == "rgba(34,197,94,0.08)"
    assert prototype.render_crime_rate(context, state, "percent")[0][1].children == "↗ 100.0%"
    assert prototype.get_citywide_crime_rate(crime, {**state, "crime_subcategories": ["theft"]}, 200_000) == .5
    assert prototype.get_citywide_crime_rate(crime, {**state, "crime_categories": [CANONICAL_CRIME_TYPES[0]]}, 200_000) == .5
    for population in [None, 0, -1, np.nan]:
        body, _ = prototype.render_crime_rate({**context, "city_population": population}, state, "raw")
        assert body[0].children == "—"
        assert body[-1].children == "City population unavailable"


def test_fixed_context_counts_use_crime_year_and_production_units(crime, uof_context):
    values = prototype.prepare_fixed_context_kpis({"valid_time": crime}, uof_context)
    assert values == {"start_date": "2025-09-02", "end_date": "2026-09-02", "uof": 4, "ois": 2}
    cards = prototype.make_fixed_context_cards({"valid_time": crime}, uof_context)
    for card in cards:
        assert card.children[-2].children == "Citywide publicly available reports in the last year"
        assert card.children[-1].children == "Sep 02, 2025 – Sep 02, 2026"
        assert not any(getattr(c, "className", "") in {"crime-v11-change", "crime-v11-radio"} for c in walk(card))


def test_new_layout_positions_and_static_cards_have_no_filter_callbacks(monkeypatch, uof_context):
    loaded = []
    monkeypatch.setattr(app_module, "load_uof_dashboard_context", lambda: loaded.append(True) or uof_context)
    app = _build_stub_app(monkeypatch)
    route = app.callback_map["page-content.children"]["callback"].__wrapped__
    route("/crime")
    assert not loaded  # The baseline does not acquire the new data dependency.
    page = route("/crime-v1-1")
    main = next(c for c in walk(page) if getattr(c, "className", "") == "crime-v11-content")
    controls_index = next(i for i,c in enumerate(main.children) if getattr(c, "className", "") == "crime-v11-display-controls")
    controls = main.children[controls_index]
    assert controls.children[0].children == "Controls"
    assert controls.open is False
    assert find_component(controls, "crime-legend-toggle") is not None
    assert find_component(controls, "crime-point-text-filter") is not None
    assert find_component(main.children[controls_index-1], "crime-analysis-start-date-input") is not None
    assert main.children[controls_index+1].className == "crime-v11-kpi-grid"
    assert sum(getattr(c, "className", "") == "crime-v11-display-controls" for c in walk(page)) == 1
    assert not any(getattr(c, "className", "") == "crime-v11-section-label" for c in walk(page))
    summary = next(c for c in walk(page) if getattr(c, "className", "") == "crime-v11-kpi-grid")
    assert [getattr(c, "id", None) for c in summary.children[:2]] == ["crime-v11-count-card", "crime-v11-rate-card"]
    assert "crime-v11-category-card" in summary.children[2].className
    primary = next(c for c in walk(page) if getattr(c, "className", "") == "crime-v11-primary-grid")
    assert primary.children[0].className == "crime-v11-figure-pair"
    assert find_component(primary.children[0].children[0], "crime-map-figure") is not None
    assert find_component(primary.children[0].children[1], "crime-daily-figure") is not None
    region = primary.children[1]
    assert region.className == "crime-v11-response-region"
    assert "crime-v11-table-card" in region.children[0].className
    kpis = region.children[1]
    assert kpis.className == "crime-v11-response-kpis"
    assert kpis.children[0].id == "crime-v11-response-card"
    assert kpis.children[1].className == "crime-v11-context-grid"
    assert [card.id for card in kpis.children[1].children] == ["crime-v11-uof-card", "crime-v11-ois-card"]
    assert kpis.children[1].children[0].children[0].children == "UOF (Use of Force) Incidents"
    assert kpis.children[1].children[1].children[0].children == "OIS (Officer Involved Shooting) Events"
    assert all("crime-v11-uof" not in key and "crime-v11-ois" not in key for key in app.callback_map)
    for key in ["uof", "ois"]:
        assert find_component(page, f"crime-v11-{key}-value").children == ("4" if key == "uof" else "2")
    route("/crime-v1-1/")
    assert len(loaded) == 1
