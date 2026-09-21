"""Refresh the annual Census population stream, without changing dashboard consumers.

Run: python scripts/dashboard/refresh_population_data.py [--acs-year 2024]
"""

import argparse
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

# Support direct script execution from any working directory.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import geopandas as gpd
import pandas as pd

from dashboard.crime_dashboard_data import load_mcpp_boundaries
from dashboard.population_client import (
    DEFAULT_ACS_YEAR, KING_COUNTY_FIPS, POPULATION_VARIABLE,
    download_king_county_2020_block_geometry, fetch_2020_block_population,
    fetch_acs_block_group_population, fetch_acs_seattle_population,
    get_census_api_key, validate_acs_year,
)
from dashboard.population_service import build_population_estimates, prepare_mcpp_boundaries
from dashboard.population_snapshot import POPULATION_OUTPUT_DIR, save_population_snapshot


LOGGER = logging.getLogger(__name__)


def refresh_population_snapshot(
    acs_year: int = DEFAULT_ACS_YEAR,
    *, output_directory: str | Path = POPULATION_OUTPUT_DIR,
    timeout: float = 60, download_timeout: float = 120,
) -> tuple[Path, Path]:
    validate_acs_year(acs_year)
    get_census_api_key()  # Fail before downloading geography if configuration is missing.
    mcpp = prepare_mcpp_boundaries(load_mcpp_boundaries())
    LOGGER.info("Refreshing ACS %s population using %s current MCPP polygons", acs_year, len(mcpp))
    acs = fetch_acs_block_group_population(acs_year, timeout=timeout)
    seattle = fetch_acs_seattle_population(acs_year, timeout=timeout)
    city_population = float(pd.to_numeric(seattle[POPULATION_VARIABLE], errors="coerce").iloc[0])
    decennial = fetch_2020_block_population(timeout=timeout)
    # The ZIP is temporary, never persisted as project data. Only geometry in the
    # notebook's Seattle extent is read; population denominators retain all county blocks.
    with TemporaryDirectory(prefix="spd_population_") as temp_directory:
        archive = download_king_county_2020_block_geometry(
            Path(temp_directory) / "tl_2020_53033_tabblock20.zip", timeout=download_timeout,
        )
        geometry = gpd.read_file(
            f"zip://{archive}", bbox=tuple(mcpp.to_crs("EPSG:4269").total_bounds),
        )
        if "COUNTYFP20" in geometry.columns:
            geometry = geometry.loc[geometry["COUNTYFP20"] == KING_COUNTY_FIPS].copy()
        population, qa = build_population_estimates(
            acs, decennial, geometry, mcpp, city_population, acs_year=acs_year,
        )
    for metric, value in qa.items():
        LOGGER.info("Population QA %s=%s", metric, value)
    paths = save_population_snapshot(population, qa, output_directory)
    LOGGER.info("Saved %s population rows to %s; metadata: %s", len(population), *paths)
    return paths


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acs-year", type=int, default=DEFAULT_ACS_YEAR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    refresh_population_snapshot(acs_year=args.acs_year)


if __name__ == "__main__":
    main()
