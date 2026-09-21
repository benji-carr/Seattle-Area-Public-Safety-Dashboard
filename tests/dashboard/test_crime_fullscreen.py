from types import SimpleNamespace

import pandas as pd
import plotly.graph_objects as go
import pytest

import app as app_module


CALLBACK_KEY = (
    "..crime-fullscreen-overlay.className..."
    "crime-fullscreen-title.children..."
    "crime-fullscreen-figure.figure.."
)


def _stub_calls_context() -> dict:
    return {
        "valid_time": pd.DataFrame(
            {
                "cad_event_original_time_queued": [
                    "2026-09-01",
                    "2026-09-02",
                ],
                "cad_event_number": ["call-1", "call-2"],
            }
        )
    }


def _stub_crime_context(start="2026-09-01") -> dict:
    return {
        "mcpp_boundaries": pd.DataFrame({"mcpp_neighborhood": ["downtown", "ballard"]}),
        "valid_time": pd.DataFrame(
            {
                "offense_date": [start, "2026-09-02"],
                "offense_sub_category": ["theft", "theft"],
                "mcpp_neighborhood": ["downtown", "downtown"],
            }
        ),
        "event_mcpp": pd.DataFrame(
            {
                "offense_sub_category": ["theft"],
                "mcpp_neighborhood": ["downtown"],
            }
        ),
    }


def _build_stub_app(
    monkeypatch,
    *,
    crime_map_capture=None,
    crime_daily_capture=None,
    crime_start="2026-09-01",
    crime_context=None,
    calls_context=None,
):
    monkeypatch.setattr(
        app_module,
        "load_crime_call_support_context",
        lambda: calls_context if calls_context is not None else _stub_calls_context(),
    )
    monkeypatch.setattr(
        app_module,
        "load_crime_dashboard_context",
        lambda: {**_stub_crime_context(crime_start), **(crime_context or {})},
    )
    def fake_make_crime_daily_figure(context, selected_bins, analysis_state=None):
        if crime_daily_capture is not None:
            crime_daily_capture["selected_bins"] = selected_bins
            crime_daily_capture["analysis_state"] = analysis_state
        return go.Figure()

    def fake_make_crime_map_figure(
        context,
        selected_bins,
        point_start_date,
        point_end_date,
        show_colorbar,
        point_filters=None,
        analysis_state=None,
        metric_mode="raw",
        layer_mode="choropleth",
    ):
        if crime_map_capture is not None:
            crime_map_capture["selected_bins"] = selected_bins
            crime_map_capture["point_start_date"] = point_start_date
            crime_map_capture["point_end_date"] = point_end_date
            crime_map_capture["show_colorbar"] = show_colorbar
            crime_map_capture["point_filters"] = point_filters
            crime_map_capture["analysis_state"] = analysis_state
            crime_map_capture["metric_mode"] = metric_mode
            crime_map_capture["layer_mode"] = layer_mode

        fig = go.Figure()
        fig.add_trace(
            go.Scattermap(
                lat=[47.6, 47.61],
                lon=[-122.33, -122.34],
            )
        )
        return fig

    monkeypatch.setattr(
        app_module,
        "make_crime_daily_figure",
        fake_make_crime_daily_figure,
    )
    monkeypatch.setattr(
        app_module,
        "make_crime_map_figure",
        fake_make_crime_map_figure,
    )

    return app_module.create_app()


def _collect_component_ids(component):
    ids = set()
    component_id = getattr(component, "id", None)

    if component_id is not None:
        ids.add(str(component_id))

    children = getattr(component, "children", None)

    if children is None:
        return ids

    if isinstance(children, (list, tuple)):
        for child in children:
            ids.update(_collect_component_ids(child))
        return ids

    ids.update(_collect_component_ids(children))
    return ids


def test_crime_layout_includes_fullscreen_components(monkeypatch):
    app = _build_stub_app(monkeypatch)

    page_callback = app.callback_map["page-content.children"]["callback"].__wrapped__
    crime_page = page_callback("/crime")
    component_ids = _collect_component_ids(crime_page)

    assert "crime-expand-map-button" in component_ids
    assert "crime-expand-daily-button" in component_ids
    assert "crime-fullscreen-figure-store" in component_ids
    assert "crime-fullscreen-overlay" in component_ids
    assert "crime-fullscreen-title" in component_ids
    assert "crime-close-fullscreen-button" in component_ids
    assert "crime-fullscreen-figure" in component_ids


def test_crime_fullscreen_store_callback_switches_targets(monkeypatch):
    app = _build_stub_app(monkeypatch)
    callback = app.callback_map["crime-fullscreen-figure-store.data"]["callback"].__wrapped__

    monkeypatch.setattr(
        app_module,
        "ctx",
        SimpleNamespace(triggered_id="crime-expand-map-button"),
    )
    assert callback(1, None, None) == "map"

    monkeypatch.setattr(
        app_module,
        "ctx",
        SimpleNamespace(triggered_id="crime-expand-daily-button"),
    )
    assert callback(None, 1, None) == "daily"

    monkeypatch.setattr(
        app_module,
        "ctx",
        SimpleNamespace(triggered_id="crime-close-fullscreen-button"),
    )
    assert callback(None, None, 1) is None


def test_crime_fullscreen_overlay_rebuilds_map_with_current_filters(monkeypatch):
    capture = {}
    app = _build_stub_app(
        monkeypatch,
        crime_map_capture=capture,
    )
    callback = app.callback_map[CALLBACK_KEY]["callback"].__wrapped__

    overlay_class, title, figure = callback(
        "map",
        _analysis_state(subcategories=["theft"], neighborhoods=["downtown"]),
        ["map_colorbar"],
        "report", "rate", "both",
    )

    assert overlay_class == "fullscreen-overlay"
    assert title == "Map view | 2026-09-01 to 2026-09-02 | 2 visible points"
    assert figure.data
    assert capture == {
        "selected_bins": ["crimes against persons", "crimes against property"],
        "point_start_date": "2026-09-01",
        "point_end_date": "2026-09-02",
        "show_colorbar": True,
        "point_filters": {"text": "report"},
        "analysis_state": _analysis_state(subcategories=["theft"], neighborhoods=["downtown"]),
        "metric_mode": "rate", "layer_mode": "both",
    }


def test_crime_fullscreen_overlay_rebuilds_daily_chart_with_legend_state(monkeypatch):
    capture = {}
    app = _build_stub_app(
        monkeypatch,
        crime_daily_capture=capture,
    )
    callback = app.callback_map[CALLBACK_KEY]["callback"].__wrapped__

    overlay_class, title, figure = callback(
        "daily",
        _analysis_state(),
        ["daily"],
        "",
    )

    assert overlay_class == "fullscreen-overlay"
    assert title == "Daily crime events"
    assert figure.layout.showlegend is True
    assert capture == {"selected_bins": ["crimes against persons", "crimes against property"], "analysis_state": _analysis_state()}


def _analysis_state(subcategories=None, neighborhoods=None):
    return {
        "start_date": "2026-09-01", "end_date": "2026-09-02",
        "crime_categories": ["crimes against persons", "crimes against property"],
        "crime_subcategories": subcategories or [], "neighborhoods": neighborhoods or [],
    }


def test_crime_type_control_has_individual_options_and_list_state(monkeypatch):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")

    def find(component, target):
        if getattr(component, "id", None) == target:
            return component
        children = getattr(component, "children", None) or []
        if not isinstance(children, (list, tuple)):
            children = [children]
        for child in children:
            result = find(child, target)
            if result is not None:
                return result

    control = find(page, "crime-category-filter")
    categories = app_module.TARGET_CRIME_CATEGORIES
    assert control.multi is True
    assert control.value == categories
    assert [option["value"] for option in control.options] == categories
    assert [option["label"] for option in control.options] == [
        "Crimes Against Persons", "Crimes Against Property", "Crimes Against Society / Other",
    ]
    callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    for selected in [categories, categories[1:], categories[1:2]]:
        state = callback("2026-09-02", "2026-09-02", selected, [], [])
        assert state["crime_categories"] == selected
        assert state["crime_categories"] is not selected
    assert callback("2026-09-02", "2026-09-02", [], [], [])["crime_categories"] == categories
    assert find(page, "crime-analysis-period-heading").children == "Period of Analysis"
    assert find(page, "crime-analysis-period-duration").children == "(Latest day)"


def test_crime_analysis_controls_and_state_ownership(monkeypatch):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")
    ids = _collect_component_ids(page)
    assert {"crime-analysis-state-store", "crime-analysis-period-label",
            "crime-category-filter", "crime-subcategory-filter",
            "crime-neighborhood-filter", "crime-point-text-filter"} <= ids
    assert "crime-point-subcategory-filter" not in ids
    assert "crime-point-neighborhood-filter" not in ids
    state_inputs = app.callback_map["crime-analysis-state-store.data"]["inputs"]
    assert {item["id"] for item in state_inputs} == {
        "crime-analysis-start-date-input", "crime-analysis-end-date-input",
        "crime-category-filter",
        "crime-subcategory-filter", "crime-neighborhood-filter",
    }
    map_key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    assert {item["id"] for item in app.callback_map[map_key]["inputs"]} == {
        "crime-analysis-state-store", "crime-legend-toggle", "crime-point-text-filter",
        "crime-map-metric", "crime-map-layer",
    }
    assert {item["id"] for item in app.callback_map["crime-daily-figure.figure"]["inputs"]} == {
        "crime-analysis-state-store", "crime-legend-toggle", "crime-daily-relayout-debounced-store",
    }


def test_analysis_state_callback_defaults_and_selections(monkeypatch):
    import json
    app = _build_stub_app(monkeypatch)
    callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    default = callback("2026-09-02", "2026-09-02", None, None, None)
    assert default == {
        "start_date": "2026-09-02", "end_date": "2026-09-02",
        "crime_categories": app_module.TARGET_CRIME_CATEGORIES,
        "crime_subcategories": [], "neighborhoods": [],
    }
    state = callback("2026-09-01", "2026-09-02",
                     ["crimes against persons", "crimes against property"], ["theft"], ["downtown", "ballard"])
    assert json.loads(json.dumps(state)) == state
    assert state == _analysis_state(["theft"], ["downtown", "ballard"])
    key = "crime-analysis-period-duration.children"
    assert app.callback_map[key]["callback"].__wrapped__(state) == "(Latest day)"


def test_crime_calendar_presets_survive_range_and_state_callbacks(monkeypatch):
    app = _build_stub_app(monkeypatch, crime_start="2025-01-01")
    range_callback = _date_inputs_callback(app)
    state_callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    period_callback = app.callback_map[
        "crime-analysis-period-duration.children"
    ]["callback"].__wrapped__
    for start, expected in [("2026-08-26", "1 week"), ("2026-08-02", "1 month"),
                            ("2025-09-02", "1 year")]:
        selected_range = _chart_date_update(
            range_callback, {"xaxis.range": [start, "2026-09-02"]},
            "Sep 02, 2026", "Sep 02, 2026",
        )
        state = state_callback(*selected_range, app_module.TARGET_CRIME_CATEGORIES, [], [])
        assert state["start_date"] == start
        assert state["end_date"] == "2026-09-02"
        assert period_callback(state) == f"(Latest {expected.removeprefix('1 ')})"


def test_map_and_daily_callbacks_use_common_state(monkeypatch):
    map_capture, daily_capture = {}, {}
    app = _build_stub_app(monkeypatch, crime_map_capture=map_capture, crime_daily_capture=daily_capture)
    state = _analysis_state(["theft"], ["downtown"])
    key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    callback = app.callback_map[key]["callback"].__wrapped__
    graph, label = callback(state, ["map_colorbar"], "report")
    assert map_capture["analysis_state"] == state
    assert map_capture["point_filters"] == {"text": "report"}
    assert map_capture["show_colorbar"] is True
    assert "2" in label
    daily = app.callback_map["crime-daily-figure.figure"]["callback"].__wrapped__
    fig = daily(state, ["daily"])
    assert daily_capture["analysis_state"] == state
    assert list(fig.layout.xaxis.range) == ["2026-09-01", "2026-09-02"]
    assert fig.layout.showlegend is True
    callback(state, [], "another search")
    assert map_capture["analysis_state"] == state
    assert map_capture["show_colorbar"] is False


def _date_inputs_callback(app):
    key = next(
        key for key in app.callback_map
        if "crime-analysis-start-date-input.value" in key
        and "crime-analysis-end-date-input.value" in key
    )
    return app.callback_map[key]["callback"].__wrapped__


def _chart_date_update(callback, relayout, start, end, state=None):
    return callback(relayout, None, None, None, None, start, end, state)


def test_crime_date_sync_preserves_slider_and_ignores_presentation(monkeypatch):
    import pytest
    from dash.exceptions import PreventUpdate
    app = _build_stub_app(monkeypatch)
    callback = _date_inputs_callback(app)
    assert _chart_date_update(
        callback, {"xaxis.range": ["2026-09-01", "2026-09-02"]},
        "Sep 02, 2026", "Sep 02, 2026",
    ) == ("Sep 01, 2026", "Sep 02, 2026")
    with pytest.raises(PreventUpdate):
        _chart_date_update(callback, {"autosize": True}, "Sep 01, 2026", "Sep 02, 2026")
    with pytest.raises(PreventUpdate):
        _chart_date_update(
            callback, {"xaxis.range": ["2026-09-01", "2026-09-02"]},
            "Sep 01, 2026", "Sep 02, 2026",
        )


def test_plain_text_date_inputs_defaults_and_no_competing_store(monkeypatch):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")
    def walk(node):
        yield node
        children = getattr(node, "children", None)
        if children is None:
            return
        if not isinstance(children, (list, tuple)):
            children = [children]
        for child in children:
            yield from walk(child)
    nodes = {getattr(node, "id", None): node for node in walk(page)}
    start_input = nodes["crime-analysis-start-date-input"]
    end_input = nodes["crime-analysis-end-date-input"]
    assert start_input.type == end_input.type == "text"
    assert start_input.debounce is end_input.debounce is True
    assert start_input.value == end_input.value == "Sep 02, 2026"
    assert "crime-analysis-date-range" not in nodes
    assert "crime-daily-visible-range-store" not in nodes
    assert [(i["id"], i["property"]) for i in app.callback_map["crime-analysis-state-store.data"]["inputs"]][:2] == [
        ("crime-analysis-start-date-input", "value"),
        ("crime-analysis-end-date-input", "value"),
    ]


def test_invalid_picker_edits_preserve_state_and_chart_clamps_to_data(monkeypatch):
    import pytest
    from dash.exceptions import PreventUpdate
    app = _build_stub_app(monkeypatch)
    state = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    for start, end in [(None, "2026-09-02"), ("", "2026-09-02"),
                       ("invalid", "2026-09-02"), ("2026-09-02", "2026-09-01"),
                       ("2025-09-01", "2026-09-02"), ("2026-09-01", "2026-09-03")]:
        with pytest.raises(PreventUpdate):
            state(start, end, None, [], [])
    assert _chart_date_update(
        _date_inputs_callback(app), {"xaxis.range": ["2020-01-01", "2030-01-01"]},
        "Sep 02, 2026", "Sep 02, 2026",
    ) == ("Sep 02, 2025", "Sep 02, 2026")


def test_manual_dates_drive_daily_viewport_and_period_label(monkeypatch):
    app = _build_stub_app(monkeypatch, crime_start="2025-01-01")
    state_callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    state = state_callback("2026-04-01T12:00:00", "2026-07-31", None, [], [])
    figure = app.callback_map["crime-daily-figure.figure"]["callback"].__wrapped__(state, [])
    assert list(figure.layout.xaxis.range) == ["2026-04-01", "2026-07-31"]
    key = "crime-analysis-period-duration.children"
    duration = app.callback_map[key]["callback"].__wrapped__(state)
    assert "Last" not in duration and "Latest" not in duration
    same_day = state_callback("2026-09-02", "2026-09-02", None, [], [])
    figure = app.callback_map["crime-daily-figure.figure"]["callback"].__wrapped__(same_day, [])
    assert list(figure.layout.xaxis.range) == [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02")]
    assert same_day["start_date"] == same_day["end_date"] == "2026-09-02"
    assert app.callback_map[key]["callback"].__wrapped__(same_day) == "(Latest day)"


@pytest.mark.parametrize("selected_date", ["2026-09-06", "2026-09-01", "2026-01-01", "2024-03-01"])
def test_same_day_presentation_contains_daily_segment_without_expanding_analysis(monkeypatch, selected_date):
    from copy import deepcopy
    from dashboard.crime_dashboard_figures import make_daily_figure
    from dashboard.crime_filters import filter_crime_records

    selected_day = pd.Timestamp(selected_date)
    records = pd.DataFrame({
        "offense_date": pd.date_range(end=selected_day, periods=10, freq="D"),
        "offense_id": range(10), "report_number": range(10),
        "event_importance_bin": ["crimes against property"] * 10,
        "offense_sub_category": ["theft"] * 10,
        "mcpp_neighborhood": ["downtown"] * 10,
    })
    map_capture = {}
    app = _build_stub_app(monkeypatch, crime_context={"valid_time": records},
                          crime_map_capture=map_capture)
    # Exercise the real data preparation and traces through the cached wrapper.
    monkeypatch.setattr(app_module, "make_crime_daily_figure", make_daily_figure)
    state_callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    state = state_callback(selected_date, selected_date, ["crimes against property"], [], [])
    before = deepcopy(state)
    daily_callback = app.callback_map["crime-daily-figure.figure"]["callback"].__wrapped__
    figure = daily_callback(state, [])
    start, end = map(pd.Timestamp, figure.layout.xaxis.range)
    assert (start, end) == (selected_day - pd.Timedelta(days=1), selected_day)
    assert end > start
    assert state == before
    assert state["start_date"] == state["end_date"] == selected_date
    assert [trace.name for trace in figure.data] == ["Daily reported offenses", "7-day average"]
    for trace in figure.data:
        assert trace.mode == "lines"
        x = pd.DatetimeIndex(trace.x)
        visible = x[(x >= start) & (x <= end)]
        assert list(visible) == [start, selected_day]
        assert x[-1] == end and x[-1] > start
        assert pd.notna(trace.y[-2:]).all()
    assert figure.layout.uirevision == f"crime-analysis-{selected_date}-{selected_date} 23:59:59.999"
    assert figure.layout.xaxis.uirevision is None

    map_key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    app.callback_map[map_key]["callback"].__wrapped__(state, [], "")
    assert map_capture["point_start_date"] == map_capture["point_end_date"] == selected_date
    assert map_capture["analysis_state"] == before
    assert filter_crime_records(records, map_capture["analysis_state"]).offense_id.tolist() == [9]

    # A multi-day analysis still uses its exact requested bounds.
    multi_start = (selected_day - pd.Timedelta(days=5)).date().isoformat()
    multi = state_callback(multi_start, selected_date, ["crimes against property"], [], [])
    assert list(daily_callback(multi, []).layout.xaxis.range) == [multi_start, selected_date]


def test_independent_date_edits_normalize_and_invalid_edits_revert(monkeypatch):
    app = _build_stub_app(monkeypatch, crime_start="2025-01-01")
    callback = _date_inputs_callback(app)
    previous = _analysis_state()
    monkeypatch.setattr(
        app_module,
        "ctx",
        SimpleNamespace(triggered_id="crime-analysis-start-date-input"),
    )
    assert callback(None, 1, None, None, None, "bad date", "Sep 02, 2026", previous) == (
        "Sep 01, 2026", app_module.no_update,
    )
    assert callback(None, 2, None, None, None, "Sep 03, 2026", "Sep 02, 2026", previous) == (
        "Sep 01, 2026", app_module.no_update,
    )
    assert callback(None, 3, None, None, None, "2/11/2026", "Sep 02, 2026", previous) == (
        "Feb 11, 2026", app_module.no_update,
    )
    state_callback = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__
    state = state_callback(
        "Feb 11, 2026", "Sep 02, 2026", app_module.TARGET_CRIME_CATEGORIES, [], [],
    )
    assert state["start_date"] == "2026-02-11"
    assert state["end_date"] == "2026-09-02"

    previous = {**previous, "start_date": "2026-02-11"}
    monkeypatch.setattr(
        app_module,
        "ctx",
        SimpleNamespace(triggered_id="crime-analysis-end-date-input"),
    )
    assert callback(None, None, None, 1, None, "Feb 11, 2026", "August 31, 2026", previous) == (
        app_module.no_update, "Aug 31, 2026",
    )


@pytest.mark.parametrize("relayout", [
    {"xaxis.autorange": True},
    {"xaxis.range": ["2020-01-01", "2030-01-01"]},
    {"xaxis.range[0]": "2020-01-01", "xaxis.range[1]": "2030-01-01"},
    {"xaxis.range[0]": "2025-09-02", "xaxis.range[1]": "2026-09-02"},
])
def test_crime_callbacks_bound_year_reset_and_map(monkeypatch, relayout):
    capture = {}
    app = _build_stub_app(monkeypatch, crime_start="2024-09-01", crime_map_capture=capture)
    dates = _chart_date_update(_date_inputs_callback(app), relayout, "Sep 02, 2026", "Sep 02, 2026")
    state = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__(*dates, None, [], [])
    assert (state["start_date"], state["end_date"]) == ("2025-09-02", "2026-09-02")
    map_key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    app.callback_map[map_key]["callback"].__wrapped__(state, [], "")
    assert (capture["point_start_date"], capture["point_end_date"]) == ("2025-09-02", "2026-09-02")


@pytest.mark.parametrize("input_id,start,end", [
    ("crime-analysis-start-date-input", "2024-09-01", "2026-09-02"),
    ("crime-analysis-end-date-input", "2026-09-01", "2027-09-02"),
])
def test_outside_analysis_manual_input_reverts_even_when_history_exists(monkeypatch, input_id, start, end):
    from dash.exceptions import PreventUpdate
    app = _build_stub_app(monkeypatch, crime_start="2024-09-01")
    monkeypatch.setattr(app_module, "ctx", SimpleNamespace(triggered_id=input_id))
    result = _date_inputs_callback(app)(None, 1, 1, 1, 1, start, end, _analysis_state())
    expected = ("Sep 01, 2026", app_module.no_update) if "start" in input_id else (app_module.no_update, "Sep 02, 2026")
    assert result == expected
    with pytest.raises(PreventUpdate):
        app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__(start, end, None, [], [])


def test_stale_crime_stores_are_bounded_for_map(monkeypatch):
    from dash.exceptions import PreventUpdate
    capture = {}
    app = _build_stub_app(monkeypatch, crime_start="2024-01-01", crime_map_capture=capture)
    state = {**_analysis_state(), "start_date": "2024-01-01", "end_date": "2030-01-01"}
    map_key = next(key for key in app.callback_map if "crime-map-mount-host.children" in key)
    _, label = app.callback_map[map_key]["callback"].__wrapped__(state, [], "")
    assert capture["point_start_date"] == "2025-09-02"
    assert capture["point_end_date"] == "2026-09-02"
    assert "2024" not in label and "2030" not in label


def test_outside_relayout_reapplies_viewport_even_when_canonical_state_is_unchanged(monkeypatch):
    app = _build_stub_app(monkeypatch, crime_start="2024-01-01")
    state = {**_analysis_state(), "start_date": "2025-09-02", "end_date": "2026-09-02"}
    crime_daily = app.callback_map["crime-daily-figure.figure"]["callback"].__wrapped__
    cached_crime = crime_daily(state, [])
    relayout = {"xaxis.range": ["2020-01-01", "2030-01-01"]}
    figure = crime_daily(state, [], relayout)
    assert list(figure.layout.xaxis.range) == ["2025-09-02", "2026-09-02"]
    assert figure.layout.uirevision is None
    assert cached_crime.layout.uirevision is not None
