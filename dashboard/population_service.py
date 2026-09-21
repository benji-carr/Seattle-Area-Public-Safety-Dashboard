"""Pure population modeling following v1_1_population_methodology.ipynb.

Denominators use ALL county blocks, before spatial filtering. Representative
points are constructed in EPSG:2285 and joined with ``within``. As in the
notebook, MCPP totals are rounded both before and after city calibration.
"""

import geopandas as gpd
import numpy as np
import pandas as pd

from dashboard.population_client import (
    BLOCK_POPULATION_VARIABLE, DEFAULT_ACS_YEAR, POPULATION_MOE_VARIABLE,
    POPULATION_VARIABLE, validate_acs_year,
)


PROJECTED_CRS = "EPSG:2285"
MAX_RECONCILIATION_FRACTION = 0.01
POPULATION_COLUMNS = [
    "geography_type", "geography_name", "population", "population_raw",
    "population_year", "source", "source_vintage", "estimation_method",
    "census_blocks", "source_block_groups",
]
SOURCE = "U.S. Census Bureau ACS 5-Year"
MCPP_METHOD = (
    "ACS block-group population distributed using 2020 Census block population "
    "weights and calibrated to ACS Seattle city population"
)


class PopulationValidationError(ValueError):
    """QA failure with any diagnostics computed before the failure."""

    def __init__(self, message: str, qa: dict | None = None):
        super().__init__(message)
        self.qa = qa or {}


def _require_columns(frame: pd.DataFrame, columns: list[str]) -> None:
    missing = set(columns) - set(frame.columns)
    if missing:
        raise PopulationValidationError(f"Population input is missing columns: {sorted(missing)}")


def _fips(frame: pd.DataFrame, column: str, width: int) -> pd.Series:
    _require_columns(frame, [column])
    values = frame[column].astype("string").str.strip()
    if not values.str.fullmatch(r"[0-9]{1," + str(width) + "}").fillna(False).all():
        raise PopulationValidationError(f"Invalid Census {column} FIPS values")
    return values.str.zfill(width)


def _nonnegative(values: pd.Series, label: str) -> pd.Series:
    numbers = pd.to_numeric(values, errors="coerce")
    if numbers.isna().any() or not np.isfinite(numbers).all() or (numbers < 0).any():
        raise PopulationValidationError(f"{label} missing, nonfinite, or negative")
    return numbers


def prepare_acs_block_groups(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, [POPULATION_VARIABLE, POPULATION_MOE_VARIABLE])
    out = pd.DataFrame(index=frame.index)
    out["bg_geoid"] = (
        _fips(frame, "state", 2) + _fips(frame, "county", 3)
        + _fips(frame, "tract", 6) + _fips(frame, "block group", 1)
    )
    if out["bg_geoid"].duplicated().any():
        raise PopulationValidationError("Duplicate ACS block-group GEOIDs")
    # Missing/sentinel estimates outside the matched geography do not affect Seattle.
    out["acs_population"] = pd.to_numeric(frame[POPULATION_VARIABLE], errors="coerce")
    out["acs_population_moe"] = pd.to_numeric(frame[POPULATION_MOE_VARIABLE], errors="coerce")
    return out


def prepare_decennial_blocks(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, [BLOCK_POPULATION_VARIABLE])
    out = pd.DataFrame(index=frame.index)
    out["block_geoid"] = (
        _fips(frame, "state", 2) + _fips(frame, "county", 3)
        + _fips(frame, "tract", 6) + _fips(frame, "block", 4)
    )
    out["bg_geoid"] = out["block_geoid"].str[:12]
    if out["block_geoid"].duplicated().any():
        raise PopulationValidationError("Duplicate Census block GEOIDs")
    out["population_2020"] = _nonnegative(frame[BLOCK_POPULATION_VARIABLE], "Census block population")
    return out


def calculate_population_weights(blocks: pd.DataFrame) -> pd.DataFrame:
    """Use complete county block groups, including blocks outside Seattle."""
    _require_columns(blocks, ["bg_geoid", "population_2020"])
    out = blocks.copy()
    out["population_2020"] = _nonnegative(out["population_2020"], "Census block population")
    out["bg_population_2020"] = out.groupby("bg_geoid")["population_2020"].transform("sum")
    out["population_weight"] = out["population_2020"].div(
        out["bg_population_2020"].where(out["bg_population_2020"] > 0)
    )
    return out


def _validate_polygons(frame: gpd.GeoDataFrame, label: str) -> None:
    if not isinstance(frame, gpd.GeoDataFrame) or "geometry" not in frame:
        raise PopulationValidationError(f"Missing {label} geometries")
    if frame.empty or frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise PopulationValidationError(f"Missing {label} geometries")
    if not frame.geometry.is_valid.all() or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise PopulationValidationError(f"Invalid {label} geometries")
    if frame.crs is None:
        raise PopulationValidationError(f"Missing {label} CRS")


def prepare_mcpp_boundaries(mcpp: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    _validate_polygons(mcpp, "MCPP")
    _require_columns(mcpp, ["mcpp_neighborhood"])
    out = mcpp[["mcpp_neighborhood", "geometry"]].copy()
    # Same normalization as the dashboard's normalize_neighborhood_name helper.
    names = (out["mcpp_neighborhood"].astype("string").str.strip().str.lower()
             .str.replace("&", "and", regex=False).str.replace(r"\s+", " ", regex=True))
    if names.isna().any() or names.eq("").any():
        raise PopulationValidationError("Missing MCPP neighborhood names")
    if names.duplicated().any():
        raise PopulationValidationError("Duplicate MCPP neighborhood names")
    out["mcpp_neighborhood"] = names
    return out


def attach_block_geometry(
    blocks: pd.DataFrame, geometry: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    _validate_polygons(geometry, "Census block")
    geometry = geometry.rename(columns={"GEOID20": "block_geoid"})
    _require_columns(geometry, ["block_geoid"])
    geometry = geometry[["block_geoid", "geometry"]].copy()
    geometry["block_geoid"] = _fips(geometry, "block_geoid", 15)
    out = geometry.merge(blocks, on="block_geoid", how="left", validate="one_to_one")
    out["population_2020"] = _nonnegative(out["population_2020"], "Census block population")
    return out


def assign_blocks_to_mcpp(
    blocks: gpd.GeoDataFrame, mcpp: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    mcpp = prepare_mcpp_boundaries(mcpp)
    points = blocks.to_crs(PROJECTED_CRS).copy()
    points.geometry = points.geometry.representative_point()
    assigned = gpd.sjoin(
        points, mcpp.to_crs(PROJECTED_CRS), how="inner", predicate="within",
    ).drop(columns="index_right")
    if assigned["block_geoid"].duplicated().any():
        raise PopulationValidationError("Census blocks assigned to multiple MCPPs; check overlapping boundaries")
    return assigned.reset_index(drop=True)


def estimate_block_population(blocks: pd.DataFrame, acs: pd.DataFrame) -> pd.DataFrame:
    out = blocks.merge(acs, on="bg_geoid", how="left", validate="many_to_one")
    missing = out["acs_population"].isna()
    problems = (out["bg_population_2020"] == 0) & (out["acs_population"] > 0)
    qa = {
        "unmatched_acs_blocks": int(missing.sum()),
        "zero_weight_block_groups": int(out.loc[problems, "bg_geoid"].nunique()),
    }
    if missing.any():
        raise PopulationValidationError(
            f"ACS population missing for matched block groups: {qa['unmatched_acs_blocks']} blocks", qa,
        )
    out["acs_population"] = _nonnegative(out["acs_population"], "Matched ACS population")
    if problems.any():
        raise PopulationValidationError(
            f"Positive ACS population with zero 2020 block-group population: {qa['zero_weight_block_groups']} groups", qa,
        )
    out["estimated_population"] = out["acs_population"] * out["population_weight"]
    # The notebook's groupby sum yields zero for zero-ACS/zero-denominator groups.
    out.loc[out["bg_population_2020"] == 0, "estimated_population"] = 0.0
    out["estimated_population"] = _nonnegative(out["estimated_population"], "Estimated block population")
    return out


def aggregate_mcpp_population(blocks: pd.DataFrame, expected_mcpps: set[str]) -> pd.DataFrame:
    out = blocks.groupby("mcpp_neighborhood", as_index=False).agg(
        population_raw=("estimated_population", "sum"),
        census_blocks=("block_geoid", "nunique"),
        source_block_groups=("bg_geoid", "nunique"),
    )
    actual = set(out["mcpp_neighborhood"])
    if actual != set(expected_mcpps):
        raise PopulationValidationError(
            f"Final MCPP coverage mismatch: missing={sorted(set(expected_mcpps) - actual)}, "
            f"unexpected={sorted(actual - set(expected_mcpps))}"
        )
    out["population_raw"] = _nonnegative(out["population_raw"], "Raw MCPP population").round().astype("int64")
    return out


def calibrate_mcpp_population(
    mcpp: pd.DataFrame, city_population: float,
) -> tuple[pd.DataFrame, dict]:
    if not np.isfinite(city_population) or city_population <= 0:
        raise PopulationValidationError("Direct ACS Seattle city population must be positive and finite")
    out = mcpp.copy()
    raw = float(_nonnegative(out["population_raw"], "Raw MCPP population").sum())
    factor = city_population / raw if raw > 0 else float("inf")
    qa = {
        "raw_mcpp_total": raw,
        "city_population": float(city_population),
        "raw_reconciliation_difference": raw - city_population,
        "raw_reconciliation_percentage": (raw - city_population) / city_population * 100,
        "calibration_factor": factor,
    }
    if not np.isfinite(factor):
        raise PopulationValidationError("Nonfinite calibration factor", qa)
    if abs(raw - city_population) > city_population * MAX_RECONCILIATION_FRACTION:
        raise PopulationValidationError(
            f"Raw population reconciliation exceeds 1%: {qa['raw_reconciliation_percentage']:+.4f}%", qa,
        )
    out["population"] = (out["population_raw"] * factor).round().astype("int64")
    qa["calibrated_mcpp_total"] = int(out["population"].sum())
    return out, qa


def build_population_estimates(
    acs_block_groups: pd.DataFrame,
    decennial_blocks: pd.DataFrame,
    block_geometry: gpd.GeoDataFrame,
    mcpp_boundaries: gpd.GeoDataFrame,
    city_population: float,
    *, acs_year: int = DEFAULT_ACS_YEAR,
) -> tuple[pd.DataFrame, dict]:
    """Return the tidy MCPP + direct city rows and serializable QA metrics."""
    validate_acs_year(acs_year)
    mcpp = prepare_mcpp_boundaries(mcpp_boundaries)
    acs = prepare_acs_block_groups(acs_block_groups)
    blocks = calculate_population_weights(prepare_decennial_blocks(decennial_blocks))
    assigned = assign_blocks_to_mcpp(attach_block_geometry(blocks, block_geometry), mcpp)
    estimated = estimate_block_population(assigned, acs)
    raw = aggregate_mcpp_population(estimated, set(mcpp["mcpp_neighborhood"]))
    calibrated, qa = calibrate_mcpp_population(raw, city_population)
    qa.update({
        "mcpp_count": len(mcpp),
        "mcpps_represented": len(calibrated),
        "assigned_2020_population": int(assigned["population_2020"].sum()),
        "assigned_block_count": len(assigned),
        "unmatched_acs_blocks": int(estimated["acs_population"].isna().sum()),
        "zero_weight_block_groups": int(estimated.loc[
            (estimated["bg_population_2020"] == 0) & (estimated["acs_population"] > 0), "bg_geoid"
        ].nunique()),
    })
    calibrated = calibrated.rename(columns={"mcpp_neighborhood": "geography_name"})
    calibrated["geography_type"] = "mcpp"
    calibrated["estimation_method"] = MCPP_METHOD
    city = pd.DataFrame([{
        "geography_type": "city", "geography_name": "seattle",
        "population": city_population, "population_raw": city_population,
        "estimation_method": "direct Seattle place estimate",
        "census_blocks": pd.NA, "source_block_groups": pd.NA,
    }])
    result = pd.concat([calibrated.sort_values("geography_name"), city], ignore_index=True)
    result["population_year"] = acs_year
    result["source_vintage"] = acs_year
    result["source"] = SOURCE
    for column in ("census_blocks", "source_block_groups"):
        result[column] = result[column].astype("Int64")
    return result[POPULATION_COLUMNS], qa
