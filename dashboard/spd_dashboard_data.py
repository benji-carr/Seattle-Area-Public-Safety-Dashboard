from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from dashboard.population_dashboard_data import load_dashboard_population
from dashboard.spd_config import (
    PROJECT_ROOT,
    DATA_PROCESSED_DIR,
    DATA_EXTERNAL_DIR,
    GEO_PROCESSED_DIR,
    GEO_EXTERNAL_DIR,
    MCPP_GEOJSON_URL,
    EVENT_ID_COLUMN,
    ROW_ID_COLUMN,
    TIME_COLUMN,
    ARRIVAL_TIME_COLUMN,
    LAT_COL,
    LON_COL,
)
from dashboard.spd_event_bins import (
    ensure_event_importance_bin,
)
from dashboard.spd_snapshot import (
    load_spd_call_snapshot,
)


def clean_text_column(series: pd.Series) -> pd.Series:
    return (
        series
        .astype("string")
        .str.strip()
        .str.lower()
    )


def prepare_call_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    required_columns = [
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        TIME_COLUMN,
        ARRIVAL_TIME_COLUMN,
        LAT_COL,
        LON_COL,
        "event_group",
        "dispatch_neighborhood",
        "dispatch_precinct",
        "dispatch_sector",
        "dispatch_beat",
        "priority",
        "initial_call_type",
        "final_call_type",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in out.columns
    ]

    if missing_columns:
        raise ValueError(
            f"SPD snapshot is missing required columns: {missing_columns}"
        )

    out[TIME_COLUMN] = pd.to_datetime(
        out[TIME_COLUMN],
        errors="coerce",
    )

    out[ARRIVAL_TIME_COLUMN] = pd.to_datetime(
        out[ARRIVAL_TIME_COLUMN],
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

    out["priority"] = pd.to_numeric(
        out["priority"],
        errors="coerce",
    )

    text_columns = [
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        "event_group",
        "dispatch_neighborhood",
        "dispatch_precinct",
        "dispatch_sector",
        "dispatch_beat",
        "initial_call_type",
        "final_call_type",
    ]

    for column in text_columns:
        out[column] = clean_text_column(out[column])

    out["date"] = out[TIME_COLUMN].dt.date

    out = ensure_event_importance_bin(out)

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


def build_or_load_event_mcpp_lookup(
    mappable_events: pd.DataFrame,
    mcpp_boundaries: gpd.GeoDataFrame,
) -> pd.DataFrame:
    lookup_path = GEO_PROCESSED_DIR / "event_mcpp_lookup.parquet"

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

    event_mcpp["mcpp_neighborhood"] = clean_text_column(
        event_mcpp["mcpp_neighborhood"]
    )

    event_mcpp["mcpp_precinct"] = clean_text_column(
        event_mcpp["mcpp_precinct"]
    )

    event_mcpp = ensure_event_importance_bin(event_mcpp)

    return event_mcpp


def build_response_analysis(df: pd.DataFrame) -> pd.DataFrame:
    response_df = df.copy()

    response_df[TIME_COLUMN] = pd.to_datetime(
        response_df[TIME_COLUMN],
        errors="coerce",
    )

    response_df[ARRIVAL_TIME_COLUMN] = pd.to_datetime(
        response_df[ARRIVAL_TIME_COLUMN],
        errors="coerce",
    )

    response_analysis = (
        response_df
        .dropna(subset=[EVENT_ID_COLUMN])
        .sort_values(TIME_COLUMN)
        .groupby(EVENT_ID_COLUMN, as_index=False)
        .agg(
            queued_time=(TIME_COLUMN, "min"),
            first_arrival_time=(ARRIVAL_TIME_COLUMN, "min"),
            event_group=("event_group", "first"),
            priority=("priority", "first"),
            dispatch_neighborhood=("dispatch_neighborhood", "first"),
            dispatch_records=(ROW_ID_COLUMN, "nunique"),
        )
    )

    response_analysis["response_time_minutes"] = (
        response_analysis["first_arrival_time"]
        - response_analysis["queued_time"]
    ).dt.total_seconds() / 60

    response_analysis = response_analysis[
        response_analysis["response_time_minutes"].notna()
        & (response_analysis["response_time_minutes"] >= 0)
        & (response_analysis["response_time_minutes"] <= 24 * 60)
    ].copy()

    response_analysis["queued_time"] = pd.to_datetime(
        response_analysis["queued_time"],
        errors="coerce",
    )

    response_analysis["event_group"] = clean_text_column(
        response_analysis["event_group"]
    )

    response_analysis["dispatch_neighborhood"] = clean_text_column(
        response_analysis["dispatch_neighborhood"]
    )

    response_analysis = ensure_event_importance_bin(response_analysis)

    return response_analysis.reset_index(drop=True)


def calculate_years_observed(response_analysis: pd.DataFrame) -> float:
    if response_analysis.empty:
        return 1.0

    min_time = response_analysis["queued_time"].min()
    max_time = response_analysis["queued_time"].max()

    if pd.isna(min_time) or pd.isna(max_time):
        return 1.0

    days_observed = (max_time - min_time).days

    if days_observed <= 0:
        return 1.0

    return days_observed / 365.25


def load_dashboard_context() -> dict[str, Any]:
    df, metadata = load_spd_call_snapshot(DATA_PROCESSED_DIR)

    df = prepare_call_snapshot(df)

    valid_time = df[
        df[TIME_COLUMN].notna()
        & df[EVENT_ID_COLUMN].notna()
    ].copy()

    mcpp_boundaries = load_mcpp_boundaries()

    mappable_events = prepare_mappable_events(df)

    event_mcpp_lookup = build_or_load_event_mcpp_lookup(
        mappable_events=mappable_events,
        mcpp_boundaries=mcpp_boundaries,
    )

    event_mcpp = prepare_event_mcpp(
        mappable_events=mappable_events,
        event_mcpp_lookup=event_mcpp_lookup,
    )

    response_analysis = build_response_analysis(df)

    neighborhood_population, city_population, population_metadata = load_dashboard_population()

    years_observed = calculate_years_observed(response_analysis)

    context = {
        "df": df,
        "metadata": metadata,
        "valid_time": valid_time,
        "mcpp_boundaries": mcpp_boundaries,
        "mappable_events": mappable_events,
        "event_mcpp_lookup": event_mcpp_lookup,
        "event_mcpp": event_mcpp,
        "response_analysis": response_analysis,
        "neighborhood_population": neighborhood_population,
        "city_population": city_population,
        "population_metadata": population_metadata,
        "years_observed": years_observed,
    }

    return context


if __name__ == "__main__":
    context = load_dashboard_context()

    print("Dashboard context loaded.")
    print(f"Snapshot rows: {len(context['df']):,}")
    print(f"Valid-time rows: {len(context['valid_time']):,}")
    print(f"Mappable events: {context['mappable_events'][EVENT_ID_COLUMN].nunique():,}")
    print(f"MCPP-matched events: {context['event_mcpp']['mcpp_neighborhood'].notna().sum():,}")
    print(f"Response-analysis events: {context['response_analysis'][EVENT_ID_COLUMN].nunique():,}")
    print(f"Years observed: {context['years_observed']:.2f}")
    print(f"MCPP boundary polygons: {len(context['mcpp_boundaries']):,}")
