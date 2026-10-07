"""Projected SPD snapshot inputs for crime Call Volume and Response Time only."""

from pathlib import Path
from typing import Any

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

from dashboard.snapshot_models import CompactCallsMetadata
from dashboard.call_metric_schemas import (
    SOURCE_COLUMNS, LATEST_TIME_COLUMN, call_metric_source_schema, call_metric_times_schema,
)

from dashboard.spd_config import (
    ARRIVAL_TIME_COLUMN, DATA_PROCESSED_DIR, EVENT_ID_COLUMN, TIME_COLUMN,
)
from dashboard.spd_snapshot import load_spd_call_snapshot


CALL_VOLUME_COLUMNS = [EVENT_ID_COLUMN, TIME_COLUMN, "dispatch_neighborhood"]
RESPONSE_COLUMNS = [
    EVENT_ID_COLUMN, "queued_time", "priority", "dispatch_neighborhood",
    "response_time_minutes",
]
METRICS_SUBDIRECTORY = "calls_metrics"
METRIC_SCHEMA_VERSION = 1


def validate_call_metric_source(source, *, require_latest=True):
    """Validate compact inputs before coercion can silently remove an event."""
    try:
        call_metric_source_schema(require_latest=require_latest).validate(source)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Calls metric source has invalid values or missing columns: {error}") from error
    source = source.copy()
    columns = [TIME_COLUMN, ARRIVAL_TIME_COLUMN]
    if require_latest:
        columns.append(LATEST_TIME_COLUMN)
    for column in columns:
        original = source[column]
        try:
            parsed = pd.to_datetime(original, errors="raise")
        except (ValueError, TypeError) as error:
            raise ValueError(f"Calls metric source has an invalid {column}") from error
        if column == ARRIVAL_TIME_COLUMN and (parsed.isna() & original.notna()).any():
            raise ValueError(f"Calls metric source has an invalid {column}")
        source[column] = parsed
    try:
        call_metric_times_schema(require_latest=require_latest).validate(source)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Calls metric source has invalid times: {error}") from error
    return source


def load_call_metric_source(output_directory=DATA_PROCESSED_DIR, *, columns=None):
    """Prefer the compact refresh; retain compatibility with the raw snapshot.

    A partially written compact snapshot is an error, not a reason to silently
    serve the older raw snapshot.
    """
    directory = Path(output_directory) / METRICS_SUBDIRECTORY
    if directory.exists():
        source, metadata = load_spd_call_snapshot(directory, columns=columns)
        CompactCallsMetadata.model_validate(metadata)
        return source, metadata
    return load_spd_call_snapshot(output_directory, columns=columns)


def load_crime_call_support_context(
    output_directory: str | Path = DATA_PROCESSED_DIR,
) -> dict[str, Any]:
    """Keep event-level metrics without loading dispatch details or spatial data.

    Match prepare_call_snapshot's coercion, the crime ranking's first timed
    event selection, and build_response_analysis's independent min/first
    aggregations (including groupby's first non-null values).
    """
    source, metadata = load_call_metric_source(output_directory, columns=SOURCE_COLUMNS)
    return build_crime_call_support_context(source, metadata)


def build_crime_call_support_context(source, metadata):
    """Apply the existing metric methodology to raw or compact query results."""
    source = source.copy()
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
