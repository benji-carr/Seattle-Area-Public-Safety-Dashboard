"""Global dropdown ownership and native modifier bridge in inline/fullscreen maps."""
from pathlib import Path
import shutil
import subprocess

from dash.exceptions import PreventUpdate
import pytest

import app as app_module
from test_crime_fullscreen import _analysis_state, _build_stub_app, CALLBACK_KEY
from test_crime_map import map_context, map_state


@pytest.mark.parametrize("current,clicked,expected", [
    ([], "capitol hill", ["downtown", "ballard"]),
    (["downtown", "ballard"], "capitol hill", []),
    (["ballard", "capitol hill"], "capitol hill", ["ballard"]),
    (["ballard"], "capitol hill", ["capitol hill", "ballard"]),
])
def test_toggle_updates_real_dropdown_in_option_order(monkeypatch, current, clicked, expected):
    app = _build_stub_app(monkeypatch)
    callback = app.callback_map["crime-neighborhood-filter.value"]["callback"].__wrapped__
    options = [{"value": name} for name in ["downtown", "capitol hill", "ballard"]]
    assert callback({"neighborhood": clicked}, current, options) == expected


@pytest.mark.parametrize("payload,current", [
    ({"neighborhood": "downtown"}, ["downtown"]),
    ({"neighborhood": "invalid"}, []), ({"neighborhood": []}, []),
    ({}, []), (None, []), ("downtown", []),
])
def test_toggle_rejects_invalid_payload_and_last_disabled_region(monkeypatch, payload, current):
    app = _build_stub_app(monkeypatch)
    callback = app.callback_map["crime-neighborhood-filter.value"]["callback"].__wrapped__
    with pytest.raises(PreventUpdate):
        callback(payload, current, [{"value": "downtown"}, {"value": "ballard"}])


def find_component(node, target):
    if getattr(node, "id", None) == target:
        return node
    children = getattr(node, "children", None)
    for child in children if isinstance(children, (list, tuple)) else [children]:
        if child is not None:
            found = find_component(child, target)
            if found is not None:
                return found


def test_map_controls_defaults_expand_button_and_persistent_responsive_graph(monkeypatch):
    app = _build_stub_app(monkeypatch)
    page = app.callback_map["page-content.children"]["callback"].__wrapped__("/crime")
    for identifier, default, labels in [
        ("crime-map-metric", "raw", ["Raw", "Rate /100k"]),
        ("crime-map-layer", "choropleth", ["Neighborhoods", "Points", "Both"]),
    ]:
        control = find_component(page, identifier)
        assert control.value == default
        assert [option["label"] for option in control.options] == labels
    expand = find_component(page, "crime-expand-map-button")
    assert expand.className == "expand-button" and expand.title == "Expand crime map"
    for identifier in ["crime-map-figure", "crime-fullscreen-figure"]:
        assert find_component(page, identifier).responsive is True
    dropdown = find_component(page, "crime-neighborhood-filter")
    # Boundary options include the canonical region with no analytical records.
    assert [option["value"] for option in dropdown.options] == ["ballard", "downtown"]
    bridge = app.callback_map["crime-map-listener-anchor.children"]
    assert {i["id"] for i in bridge["inputs"]} == {"crime-map-figure", "crime-fullscreen-figure"}
    assert app.callback_map["crime-neighborhood-filter.value"]["inputs"] == [
        {"id": "crime-map-region-toggle", "property": "data"},
    ]


@pytest.mark.parametrize("layer,count", [("choropleth", 0), ("points", 1), ("both", 1)])
def test_fullscreen_equals_inline_real_map_and_rendered_point_count(monkeypatch, map_context, map_state, layer, count):
    real_builder = app_module.make_crime_map_figure
    app = _build_stub_app(monkeypatch, crime_context=map_context)
    monkeypatch.setattr(app_module, "make_crime_map_figure", real_builder)
    inline_key = next(key for key in app.callback_map if "crime-map-figure.figure" in key)
    inline = app.callback_map[inline_key]["callback"].__wrapped__
    fullscreen = app.callback_map[CALLBACK_KEY]["callback"].__wrapped__
    state = {**map_state, "neighborhoods": ["downtown"], "crime_subcategories": ["theft"]}
    figure, label = inline(state, ["map_colorbar"], "id-0", "rate", layer)
    overlay, title, expanded = fullscreen("map", state, ["map_colorbar"], "id-0", "rate", layer)
    assert overlay == "fullscreen-overlay"
    assert expanded.to_json() == figure.to_json()
    assert app_module.count_map_points(expanded) == count
    assert f"visible points: {count}" in label
    assert f"{count} visible points" in title
    assert expanded.layout.autosize and expanded.layout.height is None
    assert fullscreen(None, state, [], "", "raw", layer) == ("fullscreen-overlay hidden", "", {})
    # A fullscreen event follows dropdown -> shared analysis state -> both figures.
    toggle = app.callback_map["crime-neighborhood-filter.value"]["callback"].__wrapped__
    options = [{"value": name} for name in map_context["mcpp_boundaries"].mcpp_neighborhood]
    enabled = toggle({"neighborhood": "downtown", "graph": "crime-fullscreen-figure"}, [], options)
    shared = app.callback_map["crime-analysis-state-store.data"]["callback"].__wrapped__(
        state["start_date"], state["end_date"], state["crime_categories"], ["theft"], enabled,
    )
    changed, _ = inline(shared, [], "", "raw", "both")
    reopened = fullscreen("map", shared, [], "", "raw", "both")[2]
    assert reopened.to_json() == changed.to_json()
    assert changed.data[0].marker.opacity[0] == .06


def test_native_bridge_modifiers_both_graphs_repeated_events_and_daily_ignored():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for native Plotly bridge regression")
    asset = Path(__file__).resolve().parents[2] / "assets" / "crime_map.js"
    script = r"""
const assert = require('node:assert/strict'), vm = require('node:vm'), fs = require('node:fs');
const commits = [], timers = [];
function graph() { return {handlers: [], on(event, fn) {assert.equal(event, 'plotly_click'); this.handlers.push(fn);}}; }
let inline = graph(), fullscreen = graph();
const document = {getElementById(id) {return {querySelector() {return id === 'crime-map-figure' ? inline : fullscreen;}};}};
const window = {setTimeout(fn) {timers.push(fn);}, dash_clientside: {no_update: {}, set_props(id, props) {commits.push({id, props});}}};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {window, document, Date});
const bind = window.dash_clientside.crime_map.bind_region_toggle;
function flush() {while(timers.length) timers.shift()();}
bind(); flush(); bind(); flush();
assert.equal(inline.handlers.length, 1); assert.equal(fullscreen.handlers.length, 1);
function event(type, modifier) {return {event: modifier, points: [{data: {type}, customdata: ['Display', 1, 5000, 20, 'Enabled', 'downtown']}]};}
inline.handlers[0](event('choroplethmap', {}));
inline.handlers[0](event('scattermap', {ctrlKey: true}));
fullscreen.handlers[0](event('scatter', {metaKey: true}));
assert.equal(commits.length, 0);
inline.handlers[0](event('choroplethmap', {ctrlKey: true}));
fullscreen.handlers[0](event('choroplethmap', {metaKey: true}));
fullscreen.handlers[0](event('choroplethmap', {metaKey: true}));
assert.equal(commits.length, 3);
assert.equal(commits[0].props.data.graph, 'crime-map-figure');
assert.equal(commits[1].props.data.graph, 'crime-fullscreen-figure');
assert.notEqual(commits[1].props.data.sequence, commits[2].props.data.sequence);
for (const c of commits) {assert.equal(c.id, 'crime-map-region-toggle'); assert.equal(c.props.data.neighborhood, 'downtown'); assert.equal(typeof c.props.data.timestamp, 'number');}
// Graph rendering can complete after the figure callback; retries bind once.
inline = null; bind(); timers.shift()(); inline = graph(); flush();
assert.equal(inline.handlers.length, 1); assert.equal(fullscreen.handlers.length, 1);
"""
    result = subprocess.run([node, "-", str(asset)], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
