"""Production UOF incident and OIS event units, independent of figures."""

from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.uof_data import local_occurrence_times
from dashboard.uof_query import TIME_COLUMN, validate_iso_date
from dashboard.uof_snapshot import UOF_OUTPUT_DIR, load_uof_snapshot

OUTSIDE_OR_UNKNOWN = "OUTSIDE_OR_UNKNOWN"
UNKNOWN_BEATS = {"", "-", "OOJ", "99", "NAN", "NONE", "<NA>", "NA", "N/A"}


def is_ois_record(incident_type: pd.Series) -> pd.Series:
    return incident_type.astype("string").str.contains(r"\bOIS\b", case=False, regex=True, na=False)


def normalize_ois_beat(beat: pd.Series) -> pd.Series:
    """This normalization is for OIS event grouping only."""
    normalized = beat.astype("string").str.strip().str.upper()
    return normalized.mask(normalized.isna() | normalized.isin(UNKNOWN_BEATS), OUTSIDE_OR_UNKNOWN)


def derive_ois_events(frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame.loc[is_ois_record(frame["incident_type"])].copy()
    rows[TIME_COLUMN] = local_occurrence_times(rows[TIME_COLUMN])
    # Keep invalid-date records in the source snapshot, but they cannot identify an event day.
    rows = rows.loc[rows[TIME_COLUMN].notna()].copy()
    rows["event_date"] = rows[TIME_COLUMN].dt.normalize()
    rows["normalized_beat"] = normalize_ois_beat(rows["beat"])
    for column in ("incident_num", "officer_id", "subject_id"):
        rows[column] = rows[column].astype("string").str.strip().replace("", pd.NA)
    events = rows.groupby(["event_date", "normalized_beat"], as_index=False, sort=True).agg(
        first_occurrence=(TIME_COLUMN, "min"), last_occurrence=(TIME_COLUMN, "max"),
        uof_rows=(TIME_COLUMN, "size"), force_incidents=("incident_num", "nunique"),
        unique_officers=("officer_id", "nunique"), unique_subjects=("subject_id", "nunique"),
    )
    events.insert(0, "ois_event_key", events["event_date"].dt.strftime("%Y-%m-%d")
                  + "|" + events["normalized_beat"])
    return events.reset_index(drop=True)


def _inclusive_period(values: pd.Series, start_date: str, end_date: str) -> pd.Series:
    start = validate_iso_date(start_date, "start_date")
    end = validate_iso_date(end_date, "end_date")
    if start is None or end is None or end < start:
        raise ValueError("A valid inclusive start_date <= end_date is required")
    return local_occurrence_times(values).dt.normalize().between(pd.Timestamp(start), pd.Timestamp(end))


def count_uof_incidents(frame: pd.DataFrame, start_date: str, end_date: str) -> int:
    selected = _inclusive_period(frame[TIME_COLUMN], start_date, end_date)
    identifiers = frame.loc[selected, "incident_num"].astype("string").str.strip().replace("", pd.NA)
    return int(identifiers.nunique())


def count_ois_events(ois_events: pd.DataFrame, start_date: str, end_date: str) -> int:
    selected = _inclusive_period(ois_events["event_date"], start_date, end_date)
    return int(ois_events.loc[selected, "ois_event_key"].nunique())


def load_uof_dashboard_context(output_directory: str | Path = UOF_OUTPUT_DIR) -> dict[str, Any]:
    frame, metadata = load_uof_snapshot(output_directory)
    ois_rows = frame.loc[is_ois_record(frame["incident_type"])].copy()
    timestamps = local_occurrence_times(frame[TIME_COLUMN])
    return {"df": frame, "metadata": metadata, "ois_events": derive_ois_events(frame),
            "ois_rows": ois_rows, "latest_available_date": timestamps.max().normalize()
            if timestamps.notna().any() else pd.NaT,
            "ois_rows_missing_event_date": int(local_occurrence_times(ois_rows[TIME_COLUMN]).isna().sum())}
