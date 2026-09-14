from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from dashboard.crime_classification import apply_crime_classification
from dashboard.population_dashboard_data import load_dashboard_population
from dashboard.spd_config import (
    DATA_PROCESSED_DIR,
    GEO_PROCESSED_DIR,
    GEO_EXTERNAL_DIR,
    MCPP_GEOJSON_URL,
)
from dashboard.crime_snapshot import (
    load_crime_snapshot,
)


CRIME_OUTPUT_DIR = DATA_PROCESSED_DIR / "crime"

EVENT_ID_COLUMN = "offense_id"
ROW_ID_COLUMN = "report_number"
TIME_COLUMN = "offense_date"
REPORT_TIME_COLUMN = "report_date_time"

LAT_COL = "latitude"
LON_COL = "longitude"

CATEGORY_COLUMN = "offense_category"
SUB_CATEGORY_COLUMN = "offense_sub_category"
NEIGHBORHOOD_COLUMN = "neighborhood"
PRECINCT_COLUMN = "precinct"
SECTOR_COLUMN = "sector"
BEAT_COLUMN = "beat"

CRIME_MCPP_LOOKUP_FILENAME = "crime_event_mcpp_lookup.parquet"


def clean_text_column(series: pd.Series) -> pd.Series:
    return (
        series
        .astype("string")
        .str.strip()
        .str.lower()
    )

def normalize_neighborhood_name(series: pd.Series) -> pd.Series:
    return (
        series
        .astype("string")
        .str.strip()
        .str.lower()
        .str.replace("&", "and", regex=False)
        .str.replace(r"\s+", " ", regex=True)
    )

def prepare_crime_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare all source rows, retaining classification/exclusion evidence for QA."""
    out = df.copy()

    required_columns = [
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        TIME_COLUMN,
        REPORT_TIME_COLUMN,
        LAT_COL,
        LON_COL,
        CATEGORY_COLUMN,
        SUB_CATEGORY_COLUMN,
        "nibrs_group_a_b",
        "nibrs_crime_against_category",
        "nibrs_offense_code_description",
        "nibrs_offense_code",
        "shooting_type_group",
        "block_address",
        NEIGHBORHOOD_COLUMN,
        PRECINCT_COLUMN,
        SECTOR_COLUMN,
        BEAT_COLUMN,
        "reporting_area",
        "census_block_2020",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in out.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Crime snapshot is missing required columns: {missing_columns}"
        )

    out[TIME_COLUMN] = pd.to_datetime(
        out[TIME_COLUMN],
        errors="coerce",
    )

    out[REPORT_TIME_COLUMN] = pd.to_datetime(
        out[REPORT_TIME_COLUMN],
        errors="coerce",
    )

    out[LAT_COL] = pd.to_numeric(
        out[LAT_COL],
        errors="coerce",
    )

    out[LON_COL] = pd.to_numeric(
        out[LON_COL],
        errors="coerce",
    )

    text_columns = [
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        CATEGORY_COLUMN,
        SUB_CATEGORY_COLUMN,
        "nibrs_group_a_b",
        "nibrs_crime_against_category",
        "nibrs_offense_code_description",
        "nibrs_offense_code",
        "shooting_type_group",
        "block_address",
        NEIGHBORHOOD_COLUMN,
        PRECINCT_COLUMN,
        SECTOR_COLUMN,
        BEAT_COLUMN,
        "reporting_area",
        "census_block_2020",
    ]

    for column in text_columns:
        out[column] = clean_text_column(out[column])

    out["date"] = out[TIME_COLUMN].dt.date
    out = apply_crime_classification(out)

    # Compatibility columns for repurposing the calls-dashboard figure logic.
    # The calls dashboard filters by event_importance_bin.
    # For crime, we use offense_category as the equivalent broad dashboard category.
    out["event_group"] = out[CATEGORY_COLUMN]
    out["event_importance_bin"] = out[CATEGORY_COLUMN]

    return out


def load_mcpp_boundaries() -> gpd.GeoDataFrame:
    processed_mcpp_geojson_path = (
        GEO_PROCESSED_DIR / "spd_mcpp_boundaries.geojson"
    )

    external_mcpp_geojson_path = (
        GEO_EXTERNAL_DIR / "spd_mcpp_boundaries.geojson"
    )

    if processed_mcpp_geojson_path.exists():
        boundaries = gpd.read_file(processed_mcpp_geojson_path)

    elif external_mcpp_geojson_path.exists():
        boundaries = gpd.read_file(external_mcpp_geojson_path)

    else:
        GEO_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        GEO_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)

        boundaries = gpd.read_file(MCPP_GEOJSON_URL)

        boundaries.to_file(
            processed_mcpp_geojson_path,
            driver="GeoJSON",
        )

        boundaries.to_file(
            external_mcpp_geojson_path,
            driver="GeoJSON",
        )

    if boundaries.crs is not None:
        boundaries = boundaries.to_crs(epsg=4326)
    else:
        boundaries = boundaries.set_crs(epsg=4326)

    boundaries.columns = [
        column.lower().strip()
        for column in boundaries.columns
    ]

    if "mcpp_neighborhood" not in boundaries.columns:
        if "neighborhood" not in boundaries.columns:
            raise ValueError(
                "MCPP boundaries must contain either 'mcpp_neighborhood' "
                "or 'neighborhood'."
            )

        boundaries["mcpp_neighborhood"] = clean_text_column(
            boundaries["neighborhood"]
        )

    else:
        boundaries["mcpp_neighborhood"] = clean_text_column(
            boundaries["mcpp_neighborhood"]
        )

    if "mcpp_precinct" not in boundaries.columns:
        if "precinct" not in boundaries.columns:
            raise ValueError(
                "MCPP boundaries must contain either 'mcpp_precinct' "
                "or 'precinct'."
            )

        boundaries["mcpp_precinct"] = clean_text_column(
            boundaries["precinct"]
        )

    else:
        boundaries["mcpp_precinct"] = clean_text_column(
            boundaries["mcpp_precinct"]
        )

    if "objectid" not in boundaries.columns:
        boundaries["objectid"] = range(1, len(boundaries) + 1)

    boundaries = boundaries[
        [
            "objectid",
            "mcpp_neighborhood",
            "mcpp_precinct",
            "geometry",
        ]
    ].copy()

    boundaries["plot_feature_id"] = (
        boundaries["objectid"]
        .astype(str)
    )

    boundaries["mcpp_neighborhood_display"] = (
        boundaries["mcpp_neighborhood"]
        .astype("string")
        .str.title()
    )

    return boundaries


def prepare_mappable_events(df: pd.DataFrame) -> pd.DataFrame:
    mappable_events = (
        df[
            df[EVENT_ID_COLUMN].notna()
            & df[TIME_COLUMN].notna()
            & df[LAT_COL].notna()
            & df[LON_COL].notna()
            & df[LAT_COL].between(47.45, 47.75)
            & df[LON_COL].between(-122.46, -122.20)
        ]
        .sort_values(TIME_COLUMN, ascending=False)
        .drop_duplicates(subset=EVENT_ID_COLUMN)
        .copy()
    )

    return mappable_events.reset_index(drop=True)

def prepare_unmappable_events(
    df: pd.DataFrame,
    mapped_event_ids: list[str] | set[str] | None = None,
) -> pd.DataFrame:
    required_columns = [
        EVENT_ID_COLUMN,
        TIME_COLUMN,
        CATEGORY_COLUMN,
        SUB_CATEGORY_COLUMN,
        NEIGHBORHOOD_COLUMN,
        "event_group",
        "event_importance_bin",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            "Crime data is missing required columns for unmappable events: "
            + ", ".join(missing_columns)
        )

    out = df.copy()

    out[EVENT_ID_COLUMN] = out[EVENT_ID_COLUMN].astype("string").str.strip()
    out[TIME_COLUMN] = pd.to_datetime(out[TIME_COLUMN], errors="coerce")

    out[NEIGHBORHOOD_COLUMN] = clean_text_column(out[NEIGHBORHOOD_COLUMN])
    out["mcpp_neighborhood"] = normalize_neighborhood_name(
        out[NEIGHBORHOOD_COLUMN]
    )

    out["event_group"] = clean_text_column(out["event_group"])
    out["event_importance_bin"] = clean_text_column(out["event_importance_bin"])

    out = out[
        out[EVENT_ID_COLUMN].notna()
        & (out[EVENT_ID_COLUMN] != "")
        & out[TIME_COLUMN].notna()
        & out["mcpp_neighborhood"].notna()
        & (out["mcpp_neighborhood"] != "")
        & out["event_importance_bin"].notna()
        & (out["event_importance_bin"] != "")
    ].copy()

    if mapped_event_ids is not None:
        mapped_event_id_set = {
            str(event_id).strip()
            for event_id in mapped_event_ids
            if pd.notna(event_id) and str(event_id).strip() != ""
        }

        out = out[
            ~out[EVENT_ID_COLUMN].isin(mapped_event_id_set)
        ].copy()

    if LAT_COL in out.columns and LON_COL in out.columns:
        latitude = pd.to_numeric(out[LAT_COL], errors="coerce")
        longitude = pd.to_numeric(out[LON_COL], errors="coerce")

        has_valid_seattle_coordinates = (
            latitude.between(47.45, 47.80)
            & longitude.between(-122.45, -122.20)
        )

        out["unmappable_reason"] = "missing or invalid coordinates"
        out.loc[
            has_valid_seattle_coordinates,
            "unmappable_reason",
        ] = "valid coordinates but no MCPP spatial match"

    else:
        out["unmappable_reason"] = "missing coordinate columns"

    out = out.sort_values(TIME_COLUMN, ascending=False)
    out = out.drop_duplicates(subset=EVENT_ID_COLUMN, keep="first")

    preferred_columns = [
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        TIME_COLUMN,
        REPORT_TIME_COLUMN,
        CATEGORY_COLUMN,
        SUB_CATEGORY_COLUMN,
        "nibrs_crime_against_category",
        "nibrs_group_a_b",
        "nibrs_offense_code_description",
        "nibrs_offense_code",
        "block_address",
        PRECINCT_COLUMN,
        SECTOR_COLUMN,
        BEAT_COLUMN,
        NEIGHBORHOOD_COLUMN,
        "mcpp_neighborhood",
        "event_group",
        "event_importance_bin",
        "unmappable_reason",
    ]

    existing_columns = [
        column
        for column in preferred_columns
        if column in out.columns
    ]

    return out[existing_columns].reset_index(drop=True)


def build_or_load_event_mcpp_lookup(
    mappable_events: pd.DataFrame,
    mcpp_boundaries: gpd.GeoDataFrame,
) -> pd.DataFrame:
    lookup_path = GEO_PROCESSED_DIR / CRIME_MCPP_LOOKUP_FILENAME

    if lookup_path.exists():
        lookup = pd.read_parquet(lookup_path)

    else:
        GEO_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

        event_points_gdf = gpd.GeoDataFrame(
            mappable_events,
            geometry=gpd.points_from_xy(
                mappable_events[LON_COL],
                mappable_events[LAT_COL],
            ),
            crs="EPSG:4326",
        )

        lookup_gdf = gpd.sjoin(
            event_points_gdf[[EVENT_ID_COLUMN, "geometry"]],
            mcpp_boundaries[
                [
                    "mcpp_neighborhood",
                    "mcpp_precinct",
                    "geometry",
                ]
            ],
            how="left",
            predicate="within",
        ).drop(columns=["index_right"], errors="ignore")

        lookup = (
            lookup_gdf[
                [
                    EVENT_ID_COLUMN,
                    "mcpp_neighborhood",
                    "mcpp_precinct",
                ]
            ]
            .dropna(subset=[EVENT_ID_COLUMN])
            .drop_duplicates(subset=EVENT_ID_COLUMN)
            .copy()
        )

        lookup.to_parquet(lookup_path, index=False)

    lookup["mcpp_neighborhood"] = clean_text_column(
        lookup["mcpp_neighborhood"]
    )

    lookup["mcpp_precinct"] = clean_text_column(
        lookup["mcpp_precinct"]
    )

    return lookup


def prepare_event_mcpp(
    mappable_events: pd.DataFrame,
    event_mcpp_lookup: pd.DataFrame,
) -> pd.DataFrame:
    event_mcpp = mappable_events.merge(
        event_mcpp_lookup,
        on=EVENT_ID_COLUMN,
        how="left",
    )

    event_mcpp[TIME_COLUMN] = pd.to_datetime(
        event_mcpp[TIME_COLUMN],
        errors="coerce",
    )

    event_mcpp[REPORT_TIME_COLUMN] = pd.to_datetime(
        event_mcpp[REPORT_TIME_COLUMN],
        errors="coerce",
    )

    event_mcpp["mcpp_neighborhood"] = clean_text_column(
        event_mcpp["mcpp_neighborhood"]
    )

    event_mcpp["mcpp_precinct"] = clean_text_column(
        event_mcpp["mcpp_precinct"]
    )

    event_mcpp[CATEGORY_COLUMN] = clean_text_column(
        event_mcpp[CATEGORY_COLUMN]
    )

    event_mcpp["event_group"] = event_mcpp[CATEGORY_COLUMN]
    event_mcpp["event_importance_bin"] = event_mcpp[CATEGORY_COLUMN]

    return event_mcpp


def calculate_years_observed(valid_time: pd.DataFrame) -> float:
    if valid_time.empty:
        return 1.0

    min_time = valid_time[TIME_COLUMN].min()
    max_time = valid_time[TIME_COLUMN].max()

    if pd.isna(min_time) or pd.isna(max_time):
        return 1.0

    days_observed = (max_time - min_time).days

    if days_observed <= 0:
        return 1.0

    return days_observed / 365.25


def load_crime_dashboard_context() -> dict[str, Any]:
    """Keep all classified rows in df; every analytical derivative uses analysis_df.

    Classification inclusion is independent of timestamps and coordinates. Only
    map points require valid coordinates; valid_time retains unmappable crimes.
    """
    df, metadata = load_crime_snapshot(CRIME_OUTPUT_DIR)

    df = prepare_crime_snapshot(df)
    analysis_df = df.loc[~df["is_excluded_from_crime_analysis"]].copy()

    valid_time = analysis_df[
        analysis_df[TIME_COLUMN].notna()
        & analysis_df[EVENT_ID_COLUMN].notna()
    ].copy()

    mcpp_boundaries = load_mcpp_boundaries()

    mappable_events = prepare_mappable_events(analysis_df)

    event_mcpp_lookup = build_or_load_event_mcpp_lookup(
        mappable_events=mappable_events,
        mcpp_boundaries=mcpp_boundaries,
    )

    event_mcpp = prepare_event_mcpp(
        mappable_events=mappable_events,
        event_mcpp_lookup=event_mcpp_lookup,
    )

    mapped_event_ids = (
        event_mcpp[EVENT_ID_COLUMN]
        .dropna()
        .astype("string")
        .str.strip()
        .unique()
        .tolist()
    )

    unmappable_events = prepare_unmappable_events(
        df=analysis_df,
        mapped_event_ids=mapped_event_ids,
    )

    # Assign analytical neighborhoods without dropping records lacking coordinates.
    lookup = event_mcpp_lookup.drop_duplicates(EVENT_ID_COLUMN).set_index(EVENT_ID_COLUMN)
    valid_time["mcpp_neighborhood"] = normalize_neighborhood_name(
        valid_time[EVENT_ID_COLUMN].map(lookup["mcpp_neighborhood"])
        .fillna(valid_time[NEIGHBORHOOD_COLUMN])
    )

    neighborhood_population, city_population, population_metadata = load_dashboard_population()

    years_observed = calculate_years_observed(valid_time)

    context = {
        "df": df,
        "metadata": metadata,
        "valid_time": valid_time,
        "mcpp_boundaries": mcpp_boundaries,
        "mappable_events": mappable_events,
        "event_mcpp_lookup": event_mcpp_lookup,
        "event_mcpp": event_mcpp,
        "unmappable_events": unmappable_events,
        "neighborhood_population": neighborhood_population,
        "city_population": city_population,
        "population_metadata": population_metadata,
        "years_observed": years_observed,
    }

    return context


if __name__ == "__main__":
    context = load_crime_dashboard_context()

    print("Crime dashboard context loaded.")
    print(f"Snapshot rows: {len(context['df']):,}")
    print(f"Valid-time rows: {len(context['valid_time']):,}")
    print(f"Mappable offenses: {context['mappable_events'][EVENT_ID_COLUMN].nunique():,}")
    print(
        "MCPP-matched offenses: "
        f"{context['event_mcpp']['mcpp_neighborhood'].notna().sum():,}"
    )
    print(f"Years observed: {context['years_observed']:.2f}")
    print(f"MCPP boundary polygons: {len(context['mcpp_boundaries']):,}")
