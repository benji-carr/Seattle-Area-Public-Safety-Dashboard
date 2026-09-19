import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from dashboard.crime_controls import make_analysis_state
from dashboard.crime_filters import filter_crime_records
from dashboard.crime_dashboard_figures import make_map_figure, prepare_daily_event_data


@pytest.fixture
def records():
    return pd.DataFrame({
        "offense_id": list("abcdef"), "report_number": list("abcdef"),
        "offense_date": pd.to_datetime(["2026-08-30", "2026-09-01 23:59", "2026-09-01",
                                        "2026-09-02", "2026-09-01", "2026-09-01"], format="mixed"),
        "event_importance_bin": ["crimes against property"] * 5 + ["crimes against persons"],
        "offense_sub_category": ["theft"] * 4 + ["fraud", "assault"],
        "mcpp_neighborhood": ["downtown"] * 3 + ["ballard", "downtown", "downtown"],
        "latitude": [47.6, 47.6, None, 47.6, 47.6, 47.6],
        "longitude": [-122.33, -122.33, None, -122.33, -122.33, -122.33],
    })


@pytest.fixture
def state():
    return make_analysis_state(
        {"start": "2026-09-01", "end": "2026-09-01"}, ["crimes against property"],
        ["theft"], ["downtown"], "2026-09-01", "2026-09-02", ["crimes against property"],
    )


def test_common_filter_keeps_unmappable_and_includes_entire_end_day(records, state):
    assert filter_crime_records(records, state).offense_id.tolist() == ["b", "c"]
    assert len(records) == 6


def test_empty_dimensions_mean_all_and_multiple_neighborhoods_are_union(records, state):
    state.update(crime_categories=[], crime_subcategories=[], neighborhoods=[])
    assert filter_crime_records(records, state).offense_id.tolist() == ["b", "c", "e", "f"]
    state.update(end_date="2026-09-02", neighborhoods=["downtown", "ballard"])
    assert filter_crime_records(records, state).offense_id.tolist() == list("bcdef")


def test_daily_filter_retains_unmappable_and_history_for_navigation(records, state):
    daily, window = prepare_daily_event_data({"valid_time": records}, ["crimes against property"], state)
    assert daily.set_index("date").loc["2026-09-01", "reported_offenses"] == 2
    assert daily.set_index("date").loc["2026-09-02", "reported_offenses"] == 0
    assert window["plot_end_day"] == pd.Timestamp("2026-09-02")
    state["crime_subcategories"] = ["nonexistent"]
    empty, _ = prepare_daily_event_data({"valid_time": records}, ["crimes against property"], state)
    assert empty.reported_offenses.sum() == 0


def test_map_shading_and_points_share_analysis_but_text_only_filters_points(records, state):
    records["offense_category"] = records["event_importance_bin"]
    records["event_group"] = records["event_importance_bin"]
    records["report_date_time"] = records["offense_date"]
    records["mcpp_precinct"] = "west"
    records["block_address"] = "test block"
    boundaries = gpd.GeoDataFrame({
        "objectid": [1, 2], "plot_feature_id": ["1", "2"],
        "mcpp_neighborhood": ["downtown", "ballard"], "mcpp_precinct": ["west", "north"],
        "geometry": [Polygon([(-122.4, 47.5), (-122.3, 47.5), (-122.3, 47.7), (-122.4, 47.5)])] * 2,
    }, crs="EPSG:4326")
    context = {
        "valid_time": records,
        "event_mcpp": records[records.latitude.notna()],
        "unmappable_events": records[records.latitude.isna()],
        "mcpp_boundaries": boundaries,
        "neighborhood_population": pd.DataFrame({"mcpp_neighborhood": ["downtown", "ballard"], "population": [1000, 1000]}),
    }
    def build(text=""):
        return make_map_figure(context, state["crime_categories"], state["start_date"],
                               state["end_date"], analysis_state=state, point_filters={"text": text}, layer_mode="both")
    figure = build()
    assert list(figure.data[0].z) == [2, 0]
    assert sum(len(trace.lat) for trace in figure.data if trace.type == "scattermap") == 1
    text_filtered = build("no matching report")
    assert list(text_filtered.data[0].z) == [2, 0]
    assert sum(len(trace.lat) for trace in text_filtered.data if trace.type == "scattermap") == 0
    state["crime_subcategories"] = ["nonexistent"]
    assert list(build().data[0].z) == [0, 0]
