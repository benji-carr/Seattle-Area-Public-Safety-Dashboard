"""External Census inputs for the annual population snapshot."""

import math
import os
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import pandas as pd
import requests
from dotenv import load_dotenv


DEFAULT_ACS_YEAR = 2024
STATE_FIPS = "53"
KING_COUNTY_FIPS = "033"
SEATTLE_PLACE_FIPS = "63000"
POPULATION_VARIABLE = "B01003_001E"
POPULATION_MOE_VARIABLE = "B01003_001M"
BLOCK_POPULATION_VARIABLE = "P1_001N"
CENSUS_BLOCK_VINTAGE = 2020
PROJECT_ROOT = Path(__file__).resolve().parents[1]
TIGER_BLOCK_URL = (
    "https://www2.census.gov/geo/tiger/TIGER2020PL/STATE/"
    "53_WASHINGTON/53033/tl_2020_53033_tabblock20.zip"
)


def get_census_api_key() -> str:
    """Honor the repository .env, without overriding the process environment."""
    load_dotenv(PROJECT_ROOT / ".env")
    key = os.getenv("CENSUS_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "CENSUS_API_KEY is missing. Set it in the environment or repository .env."
        )
    return key


def validate_acs_year(acs_year: int) -> None:
    if isinstance(acs_year, bool) or not isinstance(acs_year, int) or acs_year < 2009:
        raise ValueError("acs_year must be an integer ACS 5-Year vintage (2009 or later)")


def _validate_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive finite number of seconds")


def parse_census_response(payload: object, required_columns: list[str]) -> pd.DataFrame:
    """Parse Census's header-plus-rows format, preserving zero-padded FIPS."""
    if not isinstance(payload, list) or len(payload) < 2:
        raise ValueError("Census API returned no tabular data rows")
    header = payload[0]
    if (
        not isinstance(header, list)
        or not all(isinstance(column, str) for column in header)
        or len(set(header)) != len(header)
    ):
        raise ValueError("Census API returned an invalid or duplicate column header")
    missing = set(required_columns) - set(header)
    if missing:
        raise ValueError(f"Census API response is missing columns: {sorted(missing)}")
    if any(
        not isinstance(row, list)
        or len(row) != len(header)
        or any(value is not None and not isinstance(value, (str, int, float)) for value in row)
        for row in payload[1:]
    ):
        raise ValueError("Census API returned malformed data rows")
    return pd.DataFrame(payload[1:], columns=header)


def _fetch_population(
    endpoint: str, variables: list[str], geography: dict[str, str],
    required_geography: list[str], timeout: float,
) -> pd.DataFrame:
    _validate_timeout(timeout)
    params = {"get": ",".join(["NAME", *variables]), **geography, "key": get_census_api_key()}
    try:
        with requests.get(endpoint, params=params, timeout=timeout) as response:
            response.raise_for_status()
            try:
                payload = response.json()
            except ValueError:
                raise ValueError("Census API returned invalid JSON; check CENSUS_API_KEY") from None
    except requests.RequestException as exc:
        # requests errors can include the full URL, including the secret API key.
        status = exc.response.status_code if exc.response is not None else None
        raise RuntimeError(
            f"Census population request failed ({type(exc).__name__}, HTTP {status}); "
            "check connectivity and CENSUS_API_KEY"
        ) from None
    frame = parse_census_response(payload, ["NAME", *variables, *required_geography])
    for column, expected in (("state", STATE_FIPS), ("county", KING_COUNTY_FIPS),
                             ("place", SEATTLE_PLACE_FIPS)):
        if column in required_geography and not frame[column].eq(expected).all():
            raise ValueError(f"Census API returned unexpected {column} geography")
    if frame.duplicated(required_geography).any():
        raise ValueError("Census API returned duplicate geographies")
    return frame


def fetch_acs_block_group_population(
    acs_year: int = DEFAULT_ACS_YEAR, *, timeout: float = 60,
) -> pd.DataFrame:
    validate_acs_year(acs_year)
    return _fetch_population(
        f"https://api.census.gov/data/{acs_year}/acs/acs5",
        [POPULATION_VARIABLE, POPULATION_MOE_VARIABLE],
        {"for": "block group:*", "in": f"state:{STATE_FIPS} county:{KING_COUNTY_FIPS} tract:*"},
        ["state", "county", "tract", "block group"], timeout,
    )


def fetch_acs_seattle_population(
    acs_year: int = DEFAULT_ACS_YEAR, *, timeout: float = 60,
) -> pd.DataFrame:
    validate_acs_year(acs_year)
    frame = _fetch_population(
        f"https://api.census.gov/data/{acs_year}/acs/acs5",
        [POPULATION_VARIABLE, POPULATION_MOE_VARIABLE],
        {"for": f"place:{SEATTLE_PLACE_FIPS}", "in": f"state:{STATE_FIPS}"},
        ["state", "place"], timeout,
    )
    if len(frame) != 1:
        raise ValueError("Census API must return exactly one Seattle place estimate")
    return frame


def fetch_2020_block_population(*, timeout: float = 60) -> pd.DataFrame:
    return _fetch_population(
        "https://api.census.gov/data/2020/dec/pl", [BLOCK_POPULATION_VARIABLE],
        {"for": "block:*", "in": f"state:{STATE_FIPS} county:{KING_COUNTY_FIPS} tract:*"},
        ["state", "county", "tract", "block"], timeout,
    )


def download_king_county_2020_block_geometry(
    destination: str | Path, *, timeout: float = 120,
) -> Path:
    """Download to a caller-owned temporary/cache path, never processed project data."""
    _validate_timeout(timeout)
    destination = Path(destination).resolve()
    if destination.is_relative_to(PROJECT_ROOT / "data" / "processed"):
        raise ValueError("TIGER ZIP must be stored in a temporary directory or external cache")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        with requests.get(TIGER_BLOCK_URL, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with partial.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
        with ZipFile(partial) as archive:
            names = set(archive.namelist())
            stem = "tl_2020_53033_tabblock20"
            if not all(f"{stem}{suffix}" in names for suffix in (".shp", ".shx", ".dbf", ".prj")):
                raise ValueError("TIGER ZIP is missing King County block shapefile components")
            if archive.testzip() is not None:
                raise ValueError("TIGER ZIP failed its integrity check")
        partial.replace(destination)
    except requests.RequestException as exc:
        raise RuntimeError(f"King County TIGER download failed ({type(exc).__name__})") from None
    except BadZipFile:
        raise ValueError("King County TIGER download is not a valid ZIP") from None
    finally:
        partial.unlink(missing_ok=True)
    return destination
