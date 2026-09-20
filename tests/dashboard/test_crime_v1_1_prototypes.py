"""Approved workbook methodology and canonical crime-dashboard composition."""
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
def test_canonical_crime_and_alias_share_approved_layout_without_duplicate_ids(monkeypatch, route):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__(route)
    ids = [str(c.id) for c in walk(page) if getattr(c, "id", None) is not None]
    assert len(ids) == len(set(ids))
    assert {"crime-analysis-state-store", "crime-analysis-start-date-input", "crime-analysis-end-date-input",
            "crime-category-filter", "crime-subcategory-filter", "crime-neighborhood-filter",
            "crime-map-figure", "crime-daily-figure", "crime-expand-map-button",
            "crime-fullscreen-overlay", "crime-fullscreen-figure-store", "crime-fullscreen-figure",
            "crime-close-fullscreen-button", "crime-map-region-toggle"} <= set(ids)
    assert {"crime-v11-count-body", "crime-v11-category-body", "crime-v11-response-body", "crime-v11-ranking-body",
            "crime-v11-rate-body", "crime-v11-uof-card", "crime-v11-ois-card", "crime-map-mount-host"} <= set(ids)
    assert "crime-v1-1-map-mount-host" not in ids
    assert page.className == "crime-v11-page"
    assert not any("100dvh" in str(getattr(c, "style", {})) for c in walk(page))
    assert not any("crime-app-shell" in getattr(c, "className", "") for c in walk(page))
    assert find_component(page, "crime-v11-ranking-min-events").value == 5
    headings = [c.children for c in walk(page) if c.__class__.__name__ in {"H1", "H2"}]
    assert headings == ["Seattle Crime Dashboard", "Overall Crime Count", "Overall Crime Rate / 100k",
                        "Top-Level Crime Categories", "Neighborhood Public-Safety KPIs Ranking",
                        "Median Qualified Response Time", "UOF (Use of Force) Incidents",
                        "OIS (Officer Involved Shooting) Events"]
    assert any(getattr(c, "children", None) == "Crime Geography" for c in walk(page))
    assert find_component(page, "crime-daily-figure") is not None
    assert app.server.test_client().get(route).status_code == 200
    assert app.server.test_client().get("/_dash-dependencies").status_code == 200


@pytest.mark.parametrize("layer", ["choropleth", "points", "both"])
def test_map_mount_preserves_graph_contract_and_layer_key(layer):
    figure = {"data": [], "layout": {"title": {"text": "first"}}}
    mount = app_module.make_crime_map_mount(figure, layer)
    assert mount.key == f"crime-map-mount-{layer}"
    assert mount.style == {"height": "100%", "width": "100%"}
    graphs = [c for c in walk(mount) if getattr(c, "id", None) == "crime-map-figure"]
    assert len(graphs) == 1
    graph = graphs[0]
    assert graph.__class__.__name__ == "Graph"
    assert graph.figure == figure
    assert graph.className == "map-graph"
    assert graph.responsive is True
    assert graph.config == {"responsive": True, "displaylogo": False}
    assert graph.style == app_module.GRAPH_STYLE
    assert app_module.make_crime_map_mount({"data": []}, layer).key == mount.key
    assert len({app_module.make_crime_map_mount(figure, mode).key
                for mode in ("choropleth", "points", "both")}) == 3


def test_canonical_map_mount_key_depends_only_on_layer(monkeypatch):
    capture = {}
    app = _build_stub_app(monkeypatch, crime_map_capture=capture)
    callback = app.callback_map[next(key for key in app.callback_map if "crime-map-mount-host.children" in key)]
    assert callback["inputs"] == [{"id": identifier, "property": prop} for identifier, prop in [
        ("crime-analysis-state-store", "data"), ("crime-legend-toggle", "value"),
        ("crime-point-text-filter", "value"), ("crime-map-metric", "value"), ("crime-map-layer", "value")]]
    assert callback["state"] == []
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")
    host = find_component(page, "crime-map-mount-host")
    assert host.children.key == "crime-map-mount-choropleth"
    assert sum(getattr(c, "id", None) == "crime-map-figure" for c in walk(page)) == 1
    state = _analysis_state()
    for layer in ("choropleth", "points", "both"):
        variants = [
            (state, "raw", ""),
            (state, "raw", ""),
            ({**state, "start_date": "2026-09-01"}, "raw", ""),
            ({**state, "crime_categories": [CANONICAL_CRIME_TYPES[0]]}, "raw", ""),
            (state, "rate", ""),
            (state, "raw", "test search"),
        ]
        for selected_state, metric, text in variants:
            mount, label = callback["callback"].__wrapped__(selected_state, [], text, metric, layer)
            assert mount.key == f"crime-map-mount-{layer}"
            graph = find_component(mount, "crime-map-figure")
            assert graph.figure.to_dict()["data"][0]["type"] == "scattermap"
            assert sum(getattr(c, "id", None) == "crime-map-figure" for c in walk(mount)) == 1
            assert capture["metric_mode"] == metric and capture["layer_mode"] == layer
            assert capture["point_filters"]["text"] == text
            assert label.endswith("visible points: 2")


def test_one_canonical_map_callback_owns_mount_and_label_without_duplicate_outputs(monkeypatch):
    app = _build_stub_app(monkeypatch)
    map_key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    callback = app.callback_map[map_key]["callback"].__wrapped__
    args = (_analysis_state(), [], "", "raw", "points")
    mount, label = callback(*args)
    assert find_component(mount, "crime-map-figure").figure.to_dict()["data"][0]["type"] == "scattermap"
    assert mount.key == "crime-map-mount-points"
    assert label == "Map points: 2026-09-01 to 2026-09-02 | visible points: 2"
    owners = {}
    for key, callback in app.callback_map.items():
        outputs = callback["output"]
        for output in outputs if isinstance(outputs, (tuple, list)) else [outputs]:
            property_key = (str(output.component_id), output.component_property)
            assert property_key not in owners
            assert not output.allow_duplicate
            owners[property_key] = key
    assert owners[("crime-map-point-window-label", "children")] == map_key
    assert owners[("crime-map-mount-host", "children")] == map_key
    assert ("crime-map-figure", "figure") not in owners
    assert ("crime-v1-1-map-mount-host", "children") not in owners


def test_crime_alias_is_same_page_and_landing_and_calls_routes_are_preserved(monkeypatch):
    from plotly.utils import PlotlyJSONEncoder
    app = _build_stub_app(monkeypatch)
    route = app.callback_map["page-content.children"]["callback"].__wrapped__
    canonical = json.dumps(route("/crime"), cls=PlotlyJSONEncoder, sort_keys=True)
    for alias in ("/crime/", "/crime-v1-1", "/crime-v1-1/"):
        assert json.dumps(route(alias), cls=PlotlyJSONEncoder, sort_keys=True) == canonical
    calls = route("/calls")
    assert find_component(calls, "map-figure") is not None
    assert find_component(calls, "daily-figure") is not None
    assert find_component(calls, "scatter-figure") is not None
    assert find_component(calls, "crime-map-mount-host") is None
    assert any(getattr(c, "href", None) == "/crime" for c in walk(route("/")))


@pytest.mark.parametrize("target,helper,extra", [
    ("crime-v11-count-body", "render_crime_count", ["percent"]),
    ("crime-v11-rate-body", "render_crime_rate", ["percent"]),
    ("crime-v11-category-body", "render_category_comparison", ["raw"]),
    ("crime-v11-response-body", "render_response_kpi", ["Priority 1 only", "percent"]),
])
def test_crime_callbacks_forward_shared_state_and_local_controls(monkeypatch, target, helper, extra):
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
    route("/calls")
    assert not loaded
    page = route("/crime")
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


@pytest.fixture
def multimetric_contexts():
    response = pd.DataFrame([
        ("history", "2026-08-30", "Alpha", 1, 1.),
        ("a1", "2026-09-01", " Alpha ", 1, 10.),
        ("a1", "2026-09-01", "alpha", 1, 10.),
        ("a2", "2026-09-01", "alpha", 2, 30.),
        ("b1", "2026-09-01", "beta", 1, 10.),
        ("b2", "2026-09-01", "beta", 1, 10.),
        ("b3", "2026-09-01", "beta", 1, 10.),
        ("g1", "2026-09-01", "gamma", 3, 40.),
        ("unknown", "2026-09-01", "not canonical", 1, 100.),
        ("a3", "2026-09-02", "alpha", 1, 80.),
    ], columns=["cad_event_number", "queued_time", "dispatch_neighborhood", "priority", "response_time_minutes"])
    calls = pd.DataFrame([
        ("history", "2026-08-31", "alpha"),
        ("c1", "2026-09-01", " Alpha "),
        ("c1", "2026-09-02", "beta"),  # Later duplicate must not create a second neighborhood event.
        ("c2", "2026-09-01", "alpha"),
        ("c3", "2026-09-01", "beta"),
        ("c4", "2026-09-01", "unknown"),
        ("c5", "2026-09-02", "gamma"),
        ("last", "2026-09-03", "unknown"),
    ], columns=["cad_event_number", prototype.CALL_TIME_COLUMN, "dispatch_neighborhood"])
    crime = pd.DataFrame([
        ("history", "2026-08-29", "alpha"),
        ("x", "2026-09-01", "Alpha"), ("x", "2026-09-01", "alpha"),
        ("y", "2026-09-01", "beta"), ("z", "2026-09-01", "beta"),
        ("u", "2026-09-01", "unknown"),
        ("next", "2026-09-02", "gamma"), ("last", "2026-09-04", "alpha"),
    ], columns=["offense_id", "offense_date", "mcpp_neighborhood"])
    crime["report_number"] = "one shared report"
    boundaries = pd.DataFrame({"mcpp_neighborhood": [" Alpha ", "Beta", "gamma"] + [f"zero {i:02}" for i in range(55)]})
    return {"response_analysis": response, "valid_time": calls}, {"valid_time": crime, "mcpp_boundaries": boundaries}


def reference_multimetric(sources):
    notebook = json.loads((Path(__file__).resolve().parents[2] / "notebooks/v1_1_table.ipynb").read_text(encoding="utf-8"))
    function = next(node for cell in notebook["cells"] if cell["cell_type"] == "code"
                    for node in ast.parse("".join(cell["source"])).body
                    if isinstance(node, ast.FunctionDef) and node.name == "build_neighborhood_multimetric_ranking")
    namespace = {"pd": pd, "CALL_EVENT_ID_COLUMN": prototype.CALL_EVENT_ID_COLUMN,
                 "CALL_TIME_COLUMN": prototype.CALL_TIME_COLUMN,
                 "canonical_ranking_neighborhoods": sources["canonical"],
                 "canonical_ranking_set": set(sources["canonical"]["mcpp_neighborhood"])}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "approved-table-workbook", "exec"), namespace)
    return namespace[function.name]


@pytest.mark.parametrize("metric,first", [("response_time", ["gamma", "alpha", "beta"]),
                                          ("call_volume", ["alpha", "beta", "gamma"]),
                                          ("crime_volume", ["beta", "alpha", "gamma"])])
def test_multimetric_counts_order_and_notebook_parity(multimetric_contexts, metric, first):
    calls, crime = multimetric_contexts
    originals = [frame.copy(deep=True) for frame in [calls["response_analysis"], calls["valid_time"], crime["valid_time"]]]
    sources = prototype.prepare_multimetric_sources(calls, crime)
    ranking, period = prototype.prepare_multimetric_ranking(sources, "2026-09-01", "2026-09-01", metric, "Priority 1–3", 1, True)
    expected = reference_multimetric(sources)(sources["response"], sources["call_events"], sources["crime"],
                                             *period, [1, 2, 3], rank_metric=metric, min_response_events=1, top_n=None)
    pd.testing.assert_frame_equal(ranking, expected)
    assert ranking.ranking_neighborhood.tolist()[:3] == first
    indexed = ranking.set_index("ranking_neighborhood")
    assert indexed.loc["alpha", "call_volume"] == 2
    assert indexed.loc["beta", "crime_volume"] == 2
    assert indexed.loc["alpha", "crime_volume"] == 1
    assert indexed.loc["alpha", "qualified_response_events"] == 2
    assert indexed.loc["alpha", "median_response_minutes"] == 10
    assert "unknown" not in indexed.index and "not canonical" not in indexed.index
    for original, actual in zip(originals, [calls["response_analysis"], calls["valid_time"], crime["valid_time"]]):
        pd.testing.assert_frame_equal(original, actual)


def test_multimetric_priority_threshold_and_shared_dates(multimetric_contexts):
    sources = prototype.prepare_multimetric_sources(*multimetric_contexts)
    assert sources["analysis_bounds"] == (pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-02"))
    def rank(day, metric="call_volume", priority="Priority 1–3", minimum=1):
        return prototype.prepare_multimetric_ranking(sources, day, day, metric, priority, minimum, True)[0].set_index("ranking_neighborhood")
    all_priorities = rank("2026-09-01")
    only_one = rank("2026-09-01", priority="Priority 1 only")
    pd.testing.assert_frame_equal(all_priorities[["call_volume", "crime_volume"]], only_one[["call_volume", "crime_volume"]])
    assert only_one.loc["alpha", "qualified_response_events"] == 1
    assert pd.isna(only_one.loc["gamma", "median_response_minutes"])
    eligible = rank("2026-09-01", "response_time", minimum=2)
    assert eligible.index.tolist() == ["alpha", "beta"]  # Name breaks median ties, not n.
    assert rank("2026-09-01", "response_time", minimum=4).empty
    for metric in ["call_volume", "crime_volume"]:
        full = rank("2026-09-01", metric, minimum=9999)
        assert len(full) == 58
        assert full.loc["zero 54", ["call_volume", "crime_volume", "qualified_response_events"]].tolist() == [0, 0, 0]
        normal, _ = prototype.prepare_multimetric_ranking(sources, "2026-09-01", "2026-09-01", metric, "Priority 1–3", 9999)
        assert len(normal) == 10
    next_day = rank("2026-09-02")
    assert next_day.loc["alpha", ["median_response_minutes", "call_volume", "crime_volume"]].tolist() == [80., 0, 0]
    _, clamped = prototype.prepare_multimetric_ranking(sources, "2026-08-01", "2026-09-04", "crime_volume", "Priority 1–3", 1)
    assert clamped == ("2026-08-31", "2026-09-02")
    # More than ten eligible response rows: fullscreen has no arbitrary cap.
    sources["response"] = pd.DataFrame({"ranking_neighborhood": sources["canonical"]["mcpp_neighborhood"],
        "queued_time": pd.Timestamp("2026-09-01"), "priority": 1, "response_time_minutes": 5., "cad_event_number": range(58)})
    for full, expected in [(False, 10), (True, 58)]:
        rows, _ = prototype.prepare_multimetric_ranking(sources, "2026-09-01", "2026-09-01", "response_time", "Priority 1 only", 1, full)
        assert len(rows) == expected


def test_multimetric_markup_fullscreen_and_date_only_callback(monkeypatch, multimetric_contexts):
    sources = prototype.prepare_multimetric_sources(*multimetric_contexts)
    rendered = prototype.render_multimetric_ranking(sources, "2026-09-01", "2026-09-01", "call_volume", "Priority 1–3", 5)
    headings = [c.children for c in walk(rendered[0]) if c.__class__.__name__ == "Th"]
    assert headings == ["Rank", "Neighborhood", "Median Response", "Call Volume", "Crime Volume"]
    assert any(getattr(c, "children", None) == "n=2" for c in walk(rendered[0]))
    assert rendered[-1].children == (
        "Shared period: Sep 01, 2026 – Sep 01, 2026. Response uses the selected priority scope; "
        "Call Volume counts distinct CAD events; Crime Volume counts distinct reported offenses. "
        "Crime type, subcategory, and neighborhood selections do not filter this figure.")
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")
    assert any(getattr(c, "children", None) == "Neighborhood Public-Safety KPIs Ranking" for c in walk(page))
    button = find_component(page, "crime-v11-ranking-expand")
    assert (button.children, button.className, button.title) == ("↗", "expand-button", "Expand neighborhood ranking")
    assert find_component(page, "crime-v11-ranking-metric").value == "response_time"
    toggle_key = next(key for key in app.callback_map if "crime-v11-ranking-panel.className" in key)
    toggle = app.callback_map[toggle_key]["callback"].__wrapped__
    full = toggle(1, "crime-v11-ranking-panel")
    assert full == ("crime-v11-ranking-panel fullscreen-overlay", "×", "close-fullscreen-button", "Close fullscreen view")
    assert toggle(2, full[0]) == ("crime-v11-ranking-panel", "↗", "expand-button", "Expand neighborhood ranking")
    callback = app.callback_map["crime-v11-ranking-body.children"]
    assert [item["id"] for item in callback["inputs"]] == ["crime-analysis-start-date-input", "crime-analysis-end-date-input",
        "crime-v11-ranking-metric", "crime-v11-ranking-priority", "crime-v11-ranking-min-events", "crime-v11-ranking-panel"]
    # No global crime dimensions or entire shared state are connected.
    monkeypatch.setattr(prototype, "prepare_multimetric_sources", lambda *args: sources)
    actual = callback["callback"].__wrapped__("2026-09-01", "2026-09-01", "call_volume", "Priority 1–3", 9999, full[0])
    assert sum(c.__class__.__name__ == "Tr" for c in walk(actual[0])) == 59


def test_multimetric_empty_or_disjoint_coverage(multimetric_contexts):
    calls, crime = multimetric_contexts
    calls["response_analysis"] = calls["response_analysis"].iloc[:0]
    sources = prototype.prepare_multimetric_sources(calls, crime)
    assert sources["analysis_bounds"] is None
    rendered = prototype.render_multimetric_ranking(sources, "2026-09-01", "2026-09-01", "crime_volume", "Priority 1–3", 1)
    assert rendered.children == "Shared crime/CAD period unavailable."
