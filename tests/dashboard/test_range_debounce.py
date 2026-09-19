from pathlib import Path
import shutil
import subprocess

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from dashboard import crime_dashboard_figures as crime
from test_crime_fullscreen import _build_stub_app, _collect_component_ids


def test_only_clientside_callbacks_consume_raw_relayout(monkeypatch):
    app = _build_stub_app(monkeypatch)
    for callback in app.callback_map.values():
        if "callback" in callback:
            assert all(item["property"] != "relayoutData" for item in callback["inputs"])
    dependencies = app.server.test_client().get("/_dash-dependencies").get_json()
    for name, graph in [("calls", "daily-figure"), ("crime", "crime-daily-figure")]:
        store = f"{name}-daily-relayout-debounced-store"
        page = app.callback_map["page-content.children"]["callback"].__wrapped__(f"/{name}")
        assert store in _collect_component_ids(page)
        debounce = next(item for item in dependencies if item["output"] == f"{store}.data")
        assert debounce["clientside_function"] == {"namespace": "range_debounce", "function_name": name}
        assert debounce["inputs"] == [{"id": graph, "property": "relayoutData"}]
        consumers = [item for item in app.callback_map.values() if "callback" in item
                     and {"id": store, "property": "data"} in item["inputs"]]
        assert len(consumers) == 2  # Date-state handling AND viewport correction.


def test_debounce_commits_only_latest_range_and_keeps_timers_independent():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the clientside timer unit test")
    asset = Path(__file__).parents[2] / "assets/range_debounce.js"
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let now = 0, nextId = 0;
const timers = new Map(), commits = [];
const no_update = {};
const window = {dash_clientside: {no_update, set_props: (id, props) => commits.push({id, props, at: now})}};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), {
    window,
    setTimeout: (fn, delay) => {const id = ++nextId; timers.set(id, {fn, at: now + delay}); return id;},
    clearTimeout: id => timers.delete(id)
});
function advance(ms) {
    const end = now + ms;
    while (true) {
        const due = [...timers.entries()].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at-b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]); now = due[1].at; due[1].fn();
    }
    now = end;
}
const {calls, crime} = window.dash_clientside.range_debounce;
const first = {'xaxis.range': ['2026-09-01', '2026-09-04']};
const final = {'xaxis.range[0]': '2026-09-02', 'xaxis.range[1]': '2026-09-05'};
for (const ignored of [null, {}, {autosize: true}, {'yaxis.range': [0,10]}, {'xaxis.autorange': false}]) {
    assert.equal(calls(ignored), no_update);
}
assert.equal(timers.size, 0);
assert.equal(calls(first), no_update);
advance(200);
assert.equal(crime({'xaxis.autorange': true}), no_update);
advance(200);
assert.equal(calls(final), no_update);
advance(400);
calls({autosize: true}); // Unrelated events neither cancel nor extend a range timer.
advance(149);
assert.equal(commits.length, 0);
advance(1);
assert.equal(commits.length, 1);
assert.equal(commits[0].id, 'crime-daily-relayout-debounced-store');
assert.equal(commits[0].props.data['xaxis.autorange'], true);
assert.equal(commits[0].at, 950);
advance(199);
assert.equal(commits.length, 1);
advance(1);
assert.equal(commits.length, 2);
assert.equal(commits[1].id, 'calls-daily-relayout-debounced-store');
assert.equal(commits[1].props.data, final);
assert.equal(commits[1].at, 1150);
advance(2000);
assert.equal(commits.length, 2);
"""
    result = subprocess.run([node, "-", str(asset)], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def overlapping_crime_figure():
    """Real map construction, with one point of each category at one location."""
    categories = crime.CANONICAL_CRIME_TYPES
    records = pd.DataFrame({
        "offense_id": [1, 2, 3], "report_number": [1, 2, 3],
        "offense_date": pd.to_datetime(["2026-09-01"] * 3),
        "report_date_time": pd.to_datetime(["2026-09-01"] * 3),
        "latitude": 47.6, "longitude": -122.33, "event_importance_bin": categories,
        "event_group": categories, "offense_category": categories,
        "offense_sub_category": "test", "mcpp_neighborhood": "downtown",
        "mcpp_precinct": "west", "block_address": "test block",
    })
    boundaries = gpd.GeoDataFrame({
        "objectid": [1], "plot_feature_id": ["1"], "mcpp_neighborhood": ["downtown"],
        "mcpp_precinct": ["west"],
        "geometry": [Polygon([(-122.4,47.5),(-122.3,47.5),(-122.3,47.7),(-122.4,47.5)])],
    }, crs="EPSG:4326")
    context = {"valid_time": records, "event_mcpp": records, "mcpp_boundaries": boundaries,
               "neighborhood_population": pd.DataFrame({"mcpp_neighborhood": ["downtown"], "population": [1000]})}
    state = {"start_date": "2026-09-01", "end_date": "2026-09-01", "crime_categories": categories}
    return crime.make_map_figure(context, categories, state["start_date"], state["end_date"],
                                 analysis_state=state, layer_mode="both")


def test_person_points_render_last_with_original_legend_order():
    figure = overlapping_crime_figure()
    points = [trace for trace in figure.data if trace.type == "scattermap"]
    assert [trace.legendgroup for trace in points] == [
        crime.CRIMES_AGAINST_SOCIETY, crime.CRIMES_AGAINST_PROPERTY, crime.CRIMES_AGAINST_PERSONS,
    ]
    assert [trace.legendgroup for trace in sorted(points, key=lambda trace: trace.legendrank)] == crime.CANONICAL_CRIME_TYPES
    assert figure.data[0].type == "choroplethmap"
    assert list(figure.data[0].z) == [3]
    for trace in points:
        assert list(trace.lat) == [47.6] and list(trace.lon) == [-122.33]
        assert trace.marker.size == 7 and trace.marker.opacity == 0.72
        assert trace.marker.color == crime.get_category_color(trace.legendgroup)
