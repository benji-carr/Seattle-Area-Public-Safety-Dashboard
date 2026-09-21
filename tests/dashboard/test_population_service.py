import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from dashboard import population_service as service


def test_geoids_preserve_padding(population_inputs):
    acs, decennial, _, _ = population_inputs
    acs["county"] = 33
    acs["tract"] = 100
    prepared = service.prepare_acs_block_groups(acs)
    blocks = service.prepare_decennial_blocks(decennial)
    assert prepared["bg_geoid"].tolist() == ["530330001001"]
    assert blocks["block_geoid"].tolist() == ["530330001001001", "530330001001002", "530330001001003"]
    assert blocks["bg_geoid"].eq("530330001001").all()


def test_full_county_weights_sum_to_one(population_inputs):
    _, decennial, _, _ = population_inputs
    blocks = service.calculate_population_weights(service.prepare_decennial_blocks(decennial))
    assert blocks["population_weight"].tolist() == pytest.approx([0.3, 0.6, 0.1])
    assert blocks.groupby("bg_geoid")["population_weight"].sum().tolist() == pytest.approx([1])


def test_spatial_redistribution_spans_two_mcpps_and_keeps_outside_denominator(population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    blocks = service.calculate_population_weights(service.prepare_decennial_blocks(decennial))
    assigned = service.assign_blocks_to_mcpp(service.attach_block_geometry(blocks, geometry), mcpp)
    estimated = service.estimate_block_population(assigned, service.prepare_acs_block_groups(acs))
    assert estimated["estimated_population"].tolist() == pytest.approx([300, 600])
    assert estimated["bg_geoid"].nunique() == 1
    assert estimated["mcpp_neighborhood"].nunique() == 2
    assert estimated["population_weight"].sum() == pytest.approx(0.9)
    assert assigned.crs.to_epsg() == 2285
    raw = service.aggregate_mcpp_population(estimated, {"west and center", "east"})
    assert raw.set_index("mcpp_neighborhood")["population_raw"].to_dict() == {"east": 600, "west and center": 300}
    assert raw["census_blocks"].tolist() == [1, 1]
    assert raw["source_block_groups"].tolist() == [1, 1]


def test_build_constructs_city_row_and_qa(population_inputs):
    result, qa = service.build_population_estimates(*population_inputs, 900, acs_year=2023)
    assert list(result.columns) == service.POPULATION_COLUMNS
    assert len(result) == 3
    city = result.iloc[-1]
    assert city["geography_name"] == "seattle"
    assert city["geography_type"] == "city"
    assert city["population"] == city["population_raw"] == 900
    assert pd.isna(city["census_blocks"])
    assert result["population_year"].eq(2023).all()
    assert result["source_vintage"].eq(2023).all()
    assert result["source"].eq("U.S. Census Bureau ACS 5-Year").all()
    assert qa == {
        "raw_mcpp_total": 900, "city_population": 900, "calibration_factor": 1,
        "raw_reconciliation_difference": 0, "raw_reconciliation_percentage": 0,
        "calibrated_mcpp_total": 900, "mcpp_count": 2, "mcpps_represented": 2,
        "assigned_2020_population": 90, "assigned_block_count": 2,
        "unmatched_acs_blocks": 0, "zero_weight_block_groups": 0,
    }


def test_calibration_rounds_as_notebook():
    result, qa = service.calibrate_mcpp_population(pd.DataFrame({"population_raw": [300, 600]}), 905)
    assert qa["calibration_factor"] == pytest.approx(905 / 900)
    assert qa["raw_reconciliation_difference"] == -5
    assert qa["raw_reconciliation_percentage"] == pytest.approx(-5 / 905 * 100)
    assert result["population"].tolist() == [302, 603]


def test_aggregation_rounds_before_calibration():
    blocks = pd.DataFrame({
        "mcpp_neighborhood": ["a", "a", "b"], "estimated_population": [10.2, 10.2, 20.6],
        "block_geoid": ["1", "2", "3"], "bg_geoid": ["g", "g", "g"],
    })
    raw = service.aggregate_mcpp_population(blocks, {"a", "b"})
    assert raw["population_raw"].tolist() == [20, 21]
    _, qa = service.calibrate_mcpp_population(raw, 41)
    assert qa["raw_mcpp_total"] == 41


@pytest.mark.parametrize("raw", [989, 1011])
def test_reconciliation_guard(raw):
    with pytest.raises(service.PopulationValidationError, match="exceeds 1%") as caught:
        service.calibrate_mcpp_population(pd.DataFrame({"population_raw": [raw]}), 1000)
    assert abs(caught.value.qa["raw_reconciliation_percentage"]) > 1


@pytest.mark.parametrize("raw", [990, 1010])
def test_exact_one_percent_is_allowed(raw):
    result, _ = service.calibrate_mcpp_population(pd.DataFrame({"population_raw": [raw]}), 1000)
    assert result["population"].iloc[0] == 1000


@pytest.mark.parametrize("city", [0, -1, np.nan, np.inf])
def test_nonpositive_or_nonfinite_city_fails(city):
    with pytest.raises(ValueError, match="city population must be positive and finite"):
        service.calibrate_mcpp_population(pd.DataFrame({"population_raw": [900]}), city)


def test_nonfinite_factor_fails():
    with pytest.raises(ValueError, match="Nonfinite calibration factor"):
        service.calibrate_mcpp_population(pd.DataFrame({"population_raw": [0]}), 900)


@pytest.mark.parametrize("missing_row", [False, True])
def test_missing_acs_matches_fail(population_inputs, missing_row):
    acs, decennial, geometry, mcpp = population_inputs
    if missing_row:
        acs = acs.iloc[:0]
    else:
        acs.loc[0, "B01003_001E"] = None
    with pytest.raises(service.PopulationValidationError, match="ACS population missing") as caught:
        service.build_population_estimates(acs, decennial, geometry, mcpp, 900)
    assert caught.value.qa["unmatched_acs_blocks"] == 2


def test_zero_denominator_positive_acs_fails(population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    decennial["P1_001N"] = "0"
    with pytest.raises(service.PopulationValidationError, match="Positive ACS population with zero") as caught:
        service.build_population_estimates(acs, decennial, geometry, mcpp, 900)
    assert caught.value.qa["zero_weight_block_groups"] == 1


def test_zero_population_mcpp_is_allowed(population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    decennial["P1_001N"] = ["0", "90", "10"]
    result, _ = service.build_population_estimates(acs, decennial, geometry, mcpp, 900)
    assert result.set_index("geography_name").loc["west and center", "population"] == 0


def test_zero_acs_zero_denominator_estimates_zero():
    blocks = pd.DataFrame({"bg_geoid": ["a"], "population_2020": [0]})
    weighted = service.calculate_population_weights(blocks)
    result = service.estimate_block_population(weighted, pd.DataFrame({"bg_geoid": ["a"], "acs_population": [0]}))
    assert result["estimated_population"].tolist() == [0]


@pytest.mark.parametrize("value", [None, "bad", "-999999999", "inf"])
def test_missing_or_invalid_block_population_fails(population_inputs, value):
    _, decennial, _, _ = population_inputs
    decennial.loc[0, "P1_001N"] = value
    with pytest.raises(ValueError, match="Census block population"):
        service.prepare_decennial_blocks(decennial)


def test_geometry_without_census_population_fails(population_inputs):
    _, decennial, geometry, _ = population_inputs
    with pytest.raises(ValueError, match="Census block population"):
        service.attach_block_geometry(service.prepare_decennial_blocks(decennial.iloc[1:]), geometry)


@pytest.mark.parametrize("expected,match", [({"east"}, "unexpected"), ({"east", "west and center", "missing"}, "missing")])
def test_expected_mcpp_coverage(population_inputs, expected, match):
    acs, decennial, geometry, mcpp = population_inputs
    blocks = service.calculate_population_weights(service.prepare_decennial_blocks(decennial))
    assigned = service.assign_blocks_to_mcpp(service.attach_block_geometry(blocks, geometry), mcpp)
    estimated = service.estimate_block_population(assigned, service.prepare_acs_block_groups(acs))
    with pytest.raises(ValueError, match=match):
        service.aggregate_mcpp_population(estimated, expected)


def test_mcpp_with_no_assigned_blocks_fails(population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    geometry = geometry.iloc[[0, 2]]
    with pytest.raises(ValueError, match="coverage mismatch.*east"):
        service.build_population_estimates(acs, decennial, geometry, mcpp, 300)


@pytest.mark.parametrize("geometry,match", [
    (None, "Missing MCPP geometries"), (Polygon(), "Missing MCPP geometries"),
    (Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)]), "Invalid MCPP geometries"),
])
def test_bad_mcpp_geometry_fails(population_inputs, geometry, match):
    _, _, _, mcpp = population_inputs
    mcpp.loc[0, "geometry"] = geometry
    with pytest.raises(ValueError, match=match):
        service.prepare_mcpp_boundaries(mcpp)


def test_normalized_duplicate_names_fail(population_inputs):
    _, _, _, mcpp = population_inputs
    mcpp.loc[1, "mcpp_neighborhood"] = "West and Center"
    with pytest.raises(ValueError, match="Duplicate MCPP"):
        service.prepare_mcpp_boundaries(mcpp)


def test_overlapping_mcpp_assignment_fails(population_inputs):
    _, decennial, geometry, mcpp = population_inputs
    mcpp.loc[1, "geometry"] = mcpp.loc[0, "geometry"]
    blocks = service.attach_block_geometry(service.prepare_decennial_blocks(decennial), geometry)
    with pytest.raises(ValueError, match="multiple MCPPs"):
        service.assign_blocks_to_mcpp(blocks, mcpp)


def test_geographic_input_crs_uses_projected_representative_points(population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    result, qa = service.build_population_estimates(
        acs, decennial, geometry.to_crs(4326), mcpp.to_crs(4326), 900,
    )
    assert qa["assigned_block_count"] == 2
    assert result.iloc[:2]["population"].tolist() == [600, 300]
