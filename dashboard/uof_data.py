"""Clean source records without deriving analytical incidents or OIS events."""

from typing import Any

import pandas as pd

from dashboard.uof_query import TIME_COLUMN, UOF_COLUMNS


def local_occurrence_times(values: pd.Series) -> pd.Series:
    """Socrata floating timestamps are Seattle wall time; convert aware inputs."""
    def local_time(value: Any) -> pd.Timestamp:
        timestamp = pd.to_datetime(value, errors="coerce")
        if pd.isna(timestamp):
            return pd.NaT
        if timestamp.tzinfo is not None:
            return timestamp.tz_convert("America/Los_Angeles").tz_localize(None)
        return timestamp

    return pd.to_datetime(values.map(local_time), errors="coerce")


def uof_records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("UOF records must be a list of dictionaries")
    # Object construction avoids coercing integer identifiers through floating point.
    frame = pd.DataFrame(records, dtype=object).reindex(columns=UOF_COLUMNS)
    for column in UOF_COLUMNS:
        if column != TIME_COLUMN:
            frame[column] = frame[column].astype("string").str.strip().replace("", pd.NA)
    frame[TIME_COLUMN] = local_occurrence_times(frame[TIME_COLUMN])
    return frame.reset_index(drop=True)
