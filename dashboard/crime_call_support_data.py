"""Projected SPD snapshot inputs for crime Call Volume and Response Time only."""

from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.spd_config import (
    ARRIVAL_TIME_COLUMN, DATA_PROCESSED_DIR, EVENT_ID_COLUMN, TIME_COLUMN,
)
from dashboard.spd_snapshot import load_spd_call_snapshot


CALL_VOLUME_COLUMNS = [EVENT_ID_COLUMN, TIME_COLUMN, "dispatch_neighborhood"]
RESPONSE_COLUMNS = [
    EVENT_ID_COLUMN, "queued_time", "priority", "dispatch_neighborhood",
    "response_time_minutes",
]
SOURCE_COLUMNS = [
    EVENT_ID_COLUMN, TIME_COLUMN, ARRIVAL_TIME_COLUMN, "priority",
    "dispatch_neighborhood",
]


def load_crime_call_support_context(
    output_directory: str | Path = DATA_PROCESSED_DIR,
) -> dict[str, Any]:
    """Keep event-level metrics without loading dispatch details or spatial data.

    Match prepare_call_snapshot's coercion, the crime ranking's first timed
    event selection, and build_response_analysis's independent min/first
    aggregations (including groupby's first non-null values).
    """
    source, metadata = load_spd_call_snapshot(output_directory, columns=SOURCE_COLUMNS)
    for column in (EVENT_ID_COLUMN, "dispatch_neighborhood"):
        source[column] = source[column].astype("string").str.strip().str.lower()
    for column in (TIME_COLUMN, ARRIVAL_TIME_COLUMN):
        source[column] = pd.to_datetime(source[column], errors="coerce")
    source["priority"] = pd.to_numeric(source["priority"], errors="coerce")

    valid_time = (
        source[CALL_VOLUME_COLUMNS]
        .dropna(subset=[EVENT_ID_COLUMN, TIME_COLUMN])
        .sort_values(TIME_COLUMN)
        .drop_duplicates(EVENT_ID_COLUMN)
        .reset_index(drop=True)
    )
    response = (
        source.dropna(subset=[EVENT_ID_COLUMN])
        .sort_values(TIME_COLUMN)
        .groupby(EVENT_ID_COLUMN, as_index=False)
        .agg(
            queued_time=(TIME_COLUMN, "min"),
            first_arrival_time=(ARRIVAL_TIME_COLUMN, "min"),
            priority=("priority", "first"),
            dispatch_neighborhood=("dispatch_neighborhood", "first"),
        )
    )
    response["response_time_minutes"] = (
        response["first_arrival_time"] - response["queued_time"]
    ).dt.total_seconds() / 60
    response = response.loc[
        response["response_time_minutes"].notna()
        & response["response_time_minutes"].between(0, 24 * 60),
        RESPONSE_COLUMNS,
    ].reset_index(drop=True)
    return {"valid_time": valid_time, "response_analysis": response, "metadata": metadata}
