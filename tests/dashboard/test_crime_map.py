"""Production MapLibre figures consume shared analytical geography unchanged."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from dashboard import crime_dashboard_figures as figures


@pytest.fixture
def map_context():
    names = ["downtown", "capitol hill", "small", "empty"]
    boundaries = gpd.GeoDataFrame({"mcpp_neighborhood": names},
        geometry=[box(-122.4 + i * .01, 47.6, -122.39 + i * .01, 47.61) for i in range(4)],
        crs="EPSG:4326")
    records = pd.DataFrame({
        "offense_id": [f"id-{i}" for i in range(16)],
        "report_number": "shared-report",
        "mcpp_neighborhood": ["downtown"] * 2 + ["capitol hill"] * 3 + ["small"] * 10 + [pd.NA],
        "offense_date": pd.Timestamp("2026-09-01 23:59"),
        "offense_category": figures.CRIMES_AGAINST_PROPERTY,
        "event_importance_bin": figures.CRIMES_AGAINST_PROPERTY,
        "offense_sub_category": "theft", "block_address": "test block",
        "latitude": 47.6, "longitude": -122.33,
    })
    records.loc[1, ["latitude", "longitude"]] = None
    points = records.loc[records.latitude.notna()].copy()
    points.loc[0, "mcpp_neighborhood"] = "capitol hill"  # Analytical assignment wins.
    points.loc[2, "mcpp_neighborhood"] = pd.NA  # Analytical source fallback point.
    points.loc[15, "mcpp_neighborhood"] = "downtown"  # Never fill missing analytical geography.
    return {"valid_time": pd.concat([records, records.iloc[[0]]], ignore_index=True),
            "event_mcpp": points, "mcpp_boundaries": boundaries,
            "neighborhood_population": pd.DataFrame({"mcpp_neighborhood": names,
                                                       "population": [5000, 10000, 4999, None]})}


@pytest.fixture
def map_state():
    return {"start_date": "2026-09-01", "end_date": "2026-09-01",
            "crime_categories": [figures.CRIMES_AGAINST_PROPERTY],
            "crime_subcategories": [], "neighborhoods": []}


def build(context, state, **kwargs):
    return figures.make_map_figure(context, state["crime_categories"], analysis_state=state, **kwargs)


def test_raw_counts_reconcile_unique_analytical_offenses_including_nonpoints(map_context, map_state):
    data, summary = figures.prepare_crime_choropleth_data(map_context, map_context["valid_time"])
    assert data.offense_count.tolist() == [2, 3, 10, 0]
    assert summary == {"total_offenses": 16, "assigned_offenses": 15, "unassigned_offenses": 1}
    fig = build(map_context, map_state)
    assert list(fig.data[0].z) == [2, 3, 10, 0]
    assert fig.layout.meta["total_offenses"] == 16
    assert fig.layout.meta["unassigned_offenses"] == 1
    assert list(fig.data[0].marker.opacity) == [figures.ACTIVE_REGION_OPACITY] * 4
    assert list(fig.data[0].locations) == list(map_context["mcpp_boundaries"].mcpp_neighborhood)


def test_points_use_analytical_assignment_without_mutating_or_falling_back(map_context, map_state):
    original = map_context["event_mcpp"].copy(deep=True)
    points = figures.prepare_crime_point_source(map_context).set_index("offense_id")
    assert len(points) == len(original)
    assert points.loc["id-0", "spatial_mcpp_neighborhood"] == "capitol hill"
    assert points.loc["id-0", "mcpp_neighborhood"] == "downtown"
    assert points.loc["id-2", "mcpp_neighborhood"] == "capitol hill"
    assert pd.isna(points.loc["id-15", "mcpp_neighborhood"])
    fig = build(map_context, {**map_state, "neighborhoods": ["downtown"]}, layer_mode="both")
    assert sum(len(t.lat) for t in fig.data if t.type == "scattermap") == 1
    assert fig.layout.meta["total_offenses"] == 2
    pd.testing.assert_frame_equal(map_context["event_mcpp"], original)


def test_rate_per_100k_threshold_and_active_color_domain(map_context, map_state):
    data, _ = figures.prepare_crime_choropleth_data(map_context, map_context["valid_time"])
    assert data.crime_rate_per_100k.iloc[:3].tolist() == pytest.approx([40, 30, 10 / 4999 * 100000])
    assert data.rate_eligible.tolist() == [True, True, False, False]
    fig = build(map_context, map_state, metric_mode="rate", show_colorbar=True)
    trace = fig.data[0]
    assert list(trace.z) == pytest.approx([40, 30, 0, 0])
    assert (trace.zmin, trace.zmax) == pytest.approx((30, 40))
    assert list(trace.marker.opacity) == [.72, .72, .10, .10]
    assert trace.customdata[2][3] == "Not shown (<5,000 population)"
    assert trace.showscale is True
    subset = build(map_context, {**map_state, "neighborhoods": ["capitol hill", "small"]}, metric_mode="rate")
    assert (subset.data[0].zmin, subset.data[0].zmax) == pytest.approx((30, 31))
    assert list(subset.data[0].marker.opacity) == [.06, .72, .10, .06]
    ineligible = build(map_context, {**map_state, "neighborhoods": ["small"]}, metric_mode="rate")
    assert (ineligible.data[0].zmin, ineligible.data[0].zmax) == (0, 1)


def test_disabled_regions_remain_clickable_with_metrics_and_rescaled_domain(map_context, map_state):
    trace = build(map_context, {**map_state, "neighborhoods": ["downtown", "capitol hill"]}).data[0]
    assert list(trace.z) == [2, 3, 10, 0]
    assert list(trace.marker.opacity) == [.72, .72, .06, .06]
    assert (trace.zmin, trace.zmax) == (2, 3)
    assert [row[5] for row in trace.customdata] == list(trace.locations)


@pytest.mark.parametrize("layer,types", [("choropleth", ["choroplethmap"]),
                                       ("points", ["scattermap"]),
                                       ("both", ["choroplethmap", "scattermap"])])
def test_layer_modes_maplibre_camera_and_hover(map_context, map_state, layer, types):
    fig = build(map_context, map_state, layer_mode=layer)
    assert [t.type for t in fig.data] == types
    assert "map" in fig.to_dict()["layout"] and "mapbox" not in fig.to_dict()["layout"]
    assert fig.layout.uirevision == "v11-crime-map-camera"
    assert fig.layout.height is None and fig.layout.autosize
    for trace in fig.data:
        assert "Status:" not in trace.hovertemplate
        assert "1,000" not in trace.hovertemplate
        if trace.type == "choroplethmap":
            assert trace.colorscale == figures.go.Choroplethmap(colorscale="Viridis").colorscale
            assert "Ctrl+click" in trace.hovertemplate
            assert trace.showscale is False
        else:
            assert "Report ID:" in trace.hovertemplate
            assert "population" not in trace.hovertemplate.lower()
            assert "mappable" not in trace.hovertemplate
            assert trace.customdata[0][5] == "shared-report"


def test_text_filter_changes_only_points(map_context, map_state):
    original = build(map_context, map_state, layer_mode="both")
    filtered = build(map_context, map_state, layer_mode="both", point_filters={"text": "no match"})
    assert len(original.data) == 2 and len(filtered.data) == 1
    assert original.data[0].to_json() == filtered.data[0].to_json()
    assert original.layout.meta == filtered.layout.meta


def test_empty_point_source_keeps_polygons_and_empty_analysis_keeps_maplibre(map_context, map_state):
    map_context["event_mcpp"] = map_context["event_mcpp"].iloc[:0]
    assert list(build(map_context, map_state).data[0].z) == [2, 3, 10, 0]
    empty_state = {**map_state, "crime_subcategories": ["no match"]}
    assert list(build(map_context, empty_state).data[0].z) == [0, 0, 0, 0]
    empty = build(map_context, empty_state, layer_mode="points")
    assert not empty.data
    assert "map" in empty.to_dict()["layout"]


@pytest.mark.parametrize("bad_name", ["-", "Downtown", "unknown"])
def test_figure_rejects_broken_geography_instead_of_repairing(map_context, map_state, bad_name):
    map_context["valid_time"].loc[0, "mcpp_neighborhood"] = bad_name
    with pytest.raises(AssertionError, match="Noncanonical"):
        build(map_context, map_state)


def test_conflicting_assignments_fail_without_duplicate_point_rows(map_context):
    map_context["valid_time"].loc[0, "mcpp_neighborhood"] = "capitol hill"
    with pytest.raises(AssertionError, match="multiple"):
        figures.prepare_crime_point_source(map_context)
