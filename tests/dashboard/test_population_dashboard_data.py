from unittest.mock import Mock

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from dashboard import crime_dashboard_data as crime
from dashboard import population_dashboard_data as population_data
from dashboard import spd_dashboard_data as calls
from dashboard import spd_dashboard_figures as calls_figures


@pytest.fixture
def snapshot_loader(monkeypatch):
    snapshot = pd.DataFrame({
        "geography_type": ["mcpp", "city", "mcpp"],
        "geography_name": [" West  &  Center ", "seattle", "EAST"],
        "population": [301, 900, 600],
        "population_raw": [330, 900, 660],
        "population_year": [2024] * 3,
        "source": ["ACS"] * 3,
        "source_vintage": ["2024 ACS 5-year"] * 3,
        "estimation_method": ["calibrated", "direct", "calibrated"],
        "census_blocks": [12, 0, 24],
        "source_block_groups": [2, 0, 4],
    })
    metadata = {"acs_year": 2024, "city_population": 900, "calibrated_mcpp_total": 901}
    loader = Mock(return_value=(snapshot, metadata))
    monkeypatch.setattr(population_data, "load_population_snapshot", loader)
    return loader


def test_snapshot_adapter_preserves_population_contract_and_provenance(snapshot_loader):
    snapshot, metadata = snapshot_loader.return_value
    original = snapshot.copy(deep=True)
    neighborhoods, city_population, actual_metadata = population_data.load_dashboard_population()

    snapshot_loader.assert_called_once_with()
    assert {"mcpp_neighborhood", "dispatch_neighborhood", "population"} <= set(neighborhoods.columns)
    assert neighborhoods.mcpp_neighborhood.tolist() == ["west and center", "east"]
    pd.testing.assert_series_equal(
        neighborhoods.dispatch_neighborhood, neighborhoods.mcpp_neighborhood, check_names=False,
    )
    assert neighborhoods.population.tolist() == [301, 600]
    assert pd.api.types.is_numeric_dtype(neighborhoods.population)
    assert neighborhoods.geography_type.eq("mcpp").all()
    assert city_population == 900
    assert city_population != neighborhoods.population.sum()
    assert actual_metadata is metadata
    # Retain all snapshot fields, including the distinct raw estimates.
    pd.testing.assert_frame_equal(
        neighborhoods[snapshot.columns], snapshot.iloc[[0, 2]].reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(snapshot, original)


@pytest.mark.parametrize("module,snapshot_name,prepare_name,context_name", [
    (crime, "load_crime_snapshot", "prepare_crime_snapshot", "load_crime_dashboard_context"),
    (calls, "load_spd_call_snapshot", "prepare_call_snapshot", "load_dashboard_context"),
])
def test_context_uses_population_snapshot(
    monkeypatch, snapshot_loader, module, snapshot_name, prepare_name, context_name,
):
    # Stub unrelated event/geography work; keep the real population adapter wired in.
    events = pd.DataFrame({
        module.EVENT_ID_COLUMN: ["event-1"],
        module.TIME_COLUMN: pd.to_datetime(["2026-09-01"]),
        "neighborhood": ["east"],
        "is_excluded_from_crime_analysis": [False],
    })
    event_metadata = {"row_count": 1}
    lookup = pd.DataFrame({module.EVENT_ID_COLUMN: ["event-1"], "mcpp_neighborhood": ["east"]})
    monkeypatch.setattr(module, snapshot_name, lambda _: (events.copy(), event_metadata))
    monkeypatch.setattr(module, prepare_name, lambda df: df)
    monkeypatch.setattr(module, "load_mcpp_boundaries", lambda: gpd.GeoDataFrame())
    monkeypatch.setattr(module, "prepare_mappable_events", lambda df: df)
    monkeypatch.setattr(module, "build_or_load_event_mcpp_lookup", lambda **_: lookup)
    monkeypatch.setattr(module, "prepare_event_mcpp", lambda **_: lookup)
    if module is crime:
        monkeypatch.setattr(module, "prepare_unmappable_events", lambda **_: pd.DataFrame())
    else:
        monkeypatch.setattr(module, "build_response_analysis", lambda df: pd.DataFrame())

    context = getattr(module, context_name)()

    snapshot_loader.assert_called_once_with()
    assert context["neighborhood_population"].population.tolist() == [301, 600]
    assert context["neighborhood_population"].mcpp_neighborhood.tolist() == ["west and center", "east"]
    assert context["neighborhood_population"].dispatch_neighborhood.equals(
        context["neighborhood_population"].mcpp_neighborhood,
    )
    assert context["city_population"] == 900
    assert context["population_metadata"] is snapshot_loader.return_value[1]
    assert context["metadata"] is event_metadata


def test_calls_map_accepts_both_neighborhood_aliases_without_changing_figure(snapshot_loader):
    neighborhoods, _, _ = population_data.load_dashboard_population()
    events = pd.DataFrame({
        calls.EVENT_ID_COLUMN: ["event-1"],
        calls.TIME_COLUMN: pd.to_datetime(["2026-09-01 12:00"]),
        calls.ARRIVAL_TIME_COLUMN: pd.to_datetime(["2026-09-01 12:10"]),
        calls.LAT_COL: [47.6], calls.LON_COL: [-122.33],
        "event_group": ["theft"], "event_importance_bin": ["property/nonviolent"],
        "mcpp_neighborhood": ["east"], "mcpp_precinct": ["east"],
        "priority": ["2"], "initial_call_type": ["theft"], "final_call_type": ["theft"],
    })
    boundaries = gpd.GeoDataFrame({
        "objectid": [1], "plot_feature_id": ["1"],
        "mcpp_neighborhood": ["east"], "mcpp_precinct": ["east"],
    }, geometry=[box(-122.4, 47.5, -122.3, 47.7)], crs="EPSG:4326")
    context = {"event_mcpp": events, "mcpp_boundaries": boundaries,
               "neighborhood_population": neighborhoods}
    figure = calls_figures.make_map_figure(context, ["property/nonviolent"])
    legacy_shape_context = {**context, "neighborhood_population": neighborhoods[
        ["dispatch_neighborhood", "population"]
    ]}
    expected = calls_figures.make_map_figure(legacy_shape_context, ["property/nonviolent"])
    assert len(figure.data) > 0
    assert figure.to_json() == expected.to_json()
