"""Compare dashboard snapshot freshness with Seattle Open Data, without refreshing data."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.crime_client import fetch_latest_crime_dashboard_record
from dashboard.crime_dashboard_data import CRIME_OUTPUT_DIR, EVENT_ID_COLUMN as CRIME_EVENT_ID, TIME_COLUMN as CRIME_TIME_COLUMN
from dashboard.crime_snapshot import load_crime_snapshot
from dashboard.spd_client import fetch_latest_spd_dashboard_record
from dashboard.spd_config import DATA_PROCESSED_DIR, EVENT_ID_COLUMN as SPD_EVENT_ID, TIME_COLUMN as SPD_TIME_COLUMN
from dashboard.spd_snapshot import load_spd_call_snapshot
from dashboard.uof_client import fetch_latest_uof_dashboard_record
from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_query import ID_COLUMN as UOF_EVENT_ID, TIME_COLUMN as UOF_TIME_COLUMN
from dashboard.uof_snapshot import UOF_OUTPUT_DIR, load_uof_snapshot


class StaleDataError(RuntimeError):
    """Raised only when a valid source date differs from a valid snapshot date."""


def _normalized_date(value: Any, *, label: str) -> pd.Timestamp:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError(f"{label} is missing or invalid")
    return pd.Timestamp(timestamp).normalize()


def latest_source_date(record: dict[str, Any], *, time_column: str, event_id_column: str, label: str) -> pd.Timestamp:
    if not isinstance(record, dict):
        raise ValueError(f"{label} source response must contain an object")
    if record.get(event_id_column) is None:
        raise ValueError(f"{label} source record is missing {event_id_column}")
    return _normalized_date(record.get(time_column), label=f"{label} source {time_column}")


def latest_dashboard_date(frame: pd.DataFrame, *, time_column: str, event_id_column: str, label: str) -> pd.Timestamp:
    required = {time_column, event_id_column}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{label} snapshot is missing columns: {sorted(missing)}")
    timestamps = pd.to_datetime(frame[time_column], errors="coerce")
    valid = timestamps.notna() & frame[event_id_column].notna()
    if not valid.any():
        raise ValueError(f"{label} snapshot contains no valid dashboard records")
    return pd.Timestamp(timestamps.loc[valid].max()).normalize()


def assert_fresh(*, label: str, source_date: pd.Timestamp, dashboard_date: pd.Timestamp) -> None:
    source_date = pd.Timestamp(source_date).normalize()
    dashboard_date = pd.Timestamp(dashboard_date).normalize()
    if source_date != dashboard_date:
        raise StaleDataError(
            f"{label} dashboard data is stale: Seattle Open Data latest day="
            f"{source_date.date().isoformat()}; dashboard latest day={dashboard_date.date().isoformat()}"
        )
    print(f"{label} dashboard data is fresh: latest day={source_date.date().isoformat()}")


def check_spd_calls_freshness(
    *,
    snapshot_dir: str | Path = DATA_PROCESSED_DIR,
    fetch_source: Callable[[], dict[str, Any]] = fetch_latest_spd_dashboard_record,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    source_date = latest_source_date(fetch_source(), time_column=SPD_TIME_COLUMN, event_id_column=SPD_EVENT_ID, label="SPD calls")
    snapshot, _ = load_spd_call_snapshot(snapshot_dir)
    dashboard_date = latest_dashboard_date(snapshot, time_column=SPD_TIME_COLUMN, event_id_column=SPD_EVENT_ID, label="SPD calls")
    assert_fresh(label="SPD calls", source_date=source_date, dashboard_date=dashboard_date)
    return source_date, dashboard_date


def check_crime_freshness(
    *,
    snapshot_dir: str | Path = CRIME_OUTPUT_DIR,
    fetch_source: Callable[[], dict[str, Any]] = fetch_latest_crime_dashboard_record,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    source_date = latest_source_date(fetch_source(), time_column=CRIME_TIME_COLUMN, event_id_column=CRIME_EVENT_ID, label="Crime")
    snapshot, _ = load_crime_snapshot(snapshot_dir)
    dashboard_date = latest_dashboard_date(snapshot, time_column=CRIME_TIME_COLUMN, event_id_column=CRIME_EVENT_ID, label="Crime")
    assert_fresh(label="Crime", source_date=source_date, dashboard_date=dashboard_date)
    return source_date, dashboard_date


def check_uof_freshness(
    *, snapshot_dir: str | Path = UOF_OUTPUT_DIR,
    fetch_source: Callable[[], dict[str, Any]] = fetch_latest_uof_dashboard_record,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    record = fetch_source()
    if not isinstance(record, dict):
        raise ValueError("UOF source response must contain an object")
    source = uof_records_to_dataframe([record])
    if source[[UOF_TIME_COLUMN, UOF_EVENT_ID]].iloc[0].isna().any():
        raise ValueError("UOF source record has missing or invalid occured_date_time/uniqueid")
    source_date = latest_dashboard_date(source, time_column=UOF_TIME_COLUMN,
                                       event_id_column=UOF_EVENT_ID, label="UOF source")
    snapshot, _ = load_uof_snapshot(snapshot_dir)
    missing = {UOF_TIME_COLUMN, UOF_EVENT_ID} - set(snapshot.columns)
    if missing:
        raise ValueError(f"UOF snapshot is missing columns: {sorted(missing)}")
    snapshot = uof_records_to_dataframe(snapshot.to_dict("records"))
    dashboard_date = latest_dashboard_date(snapshot, time_column=UOF_TIME_COLUMN,
                                          event_id_column=UOF_EVENT_ID, label="UOF")
    assert_fresh(label="UOF", source_date=source_date, dashboard_date=dashboard_date)
    return source_date, dashboard_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Check dashboard snapshots against Seattle Open Data freshness.")
    parser.add_argument("--dataset", choices=("all", "calls", "crime", "uof"), default="all")
    args = parser.parse_args()
    try:
        stale_errors: list[StaleDataError] = []
        if args.dataset in {"all", "calls"}:
            try:
                check_spd_calls_freshness()
            except StaleDataError as error:
                stale_errors.append(error)
        if args.dataset in {"all", "crime"}:
            try:
                check_crime_freshness()
            except StaleDataError as error:
                stale_errors.append(error)
        if args.dataset in {"all", "uof"}:
            try:
                check_uof_freshness()
            except StaleDataError as error:
                stale_errors.append(error)
        if stale_errors:
            raise StaleDataError("\n".join(str(error) for error in stale_errors))
    except StaleDataError as error:
        print(error, file=sys.stderr)
        raise SystemExit(2) from error
    except Exception as error:
        print(f"Freshness check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
