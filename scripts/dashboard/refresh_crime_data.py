import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from dashboard.crime_service import (
    load_crime_dataset,
)
from dashboard.crime_snapshot import (
    load_crime_snapshot,
    save_crime_snapshot,
)
from scripts.dashboard.check_data_freshness import check_crime_freshness
from dashboard.crime_client import fetch_latest_crime_dashboard_record


CRIME_OUTPUT_DIR = Path("data/processed/crime")

EVENT_DATE_COLUMN = "offense_date"
REFRESH_DATE_COLUMN = "report_date_time"
DEDUPLICATION_KEY = ["offense_id"]

DEFAULT_PAGE_SIZE = 5000
DEFAULT_MAX_PAGES = None
DEFAULT_TIMEOUT = 60.0
# Timestamp cutoff preserves time of day: 734 is the minimum whole-day
# lookback covering two complete 367-date periods, even after midnight.
DEFAULT_ROLLING_WINDOW_DAYS = 734
DEFAULT_OVERLAP_DAYS = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0


def get_default_start_date(
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> str:
    """
    Used only when no existing crime snapshot exists yet.

    Anchors the initial pull to the latest available offense date
    in the source dataset.
    """
    latest_record = fetch_latest_crime_dashboard_record(
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    latest_timestamp = pd.to_datetime(
        latest_record.get(EVENT_DATE_COLUMN),
        errors="coerce",
    )

    if pd.isna(latest_timestamp):
        raise ValueError(
            "Latest crime source record has no valid offense_date"
        )

    return (
        latest_timestamp.date()
        - timedelta(days=rolling_window_days)
    ).isoformat()

def validate_positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")

    if value < 1:
        raise ValueError(f"{name} must be at least 1")


def validate_nonnegative_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} cannot be negative")


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be an integer or float")

    if timeout <= 0:
        raise ValueError("timeout must be larger than zero")


def full_refresh_crime_snapshot(
    start_date: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = DEFAULT_MAX_PAGES,
    timeout: float = DEFAULT_TIMEOUT,
    output_directory: str | Path = CRIME_OUTPUT_DIR,
    date_column: str = EVENT_DATE_COLUMN,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    validate_positive_int(page_size, "page_size")
    validate_timeout(timeout)

    if max_pages is not None:
        validate_positive_int(max_pages, "max_pages")

    logging.info(
        "Starting full SPD Crime snapshot refresh: start_date=%s, date_column=%s",
        start_date,
        date_column,
    )

    df = load_crime_dataset(
       start_date=start_date,
       page_size=page_size,
       max_pages=max_pages,
       timeout=timeout,
       date_column=date_column,
       max_retries=max_retries,
       retry_backoff_seconds=retry_backoff_seconds,
       )

    required_time_columns = [
        EVENT_DATE_COLUMN,
        REFRESH_DATE_COLUMN,
    ]

    missing_time_columns = [
        column
        for column in required_time_columns
        if column not in df.columns
    ]

    if missing_time_columns:
        raise ValueError(
            f"Crime data is missing required time columns: {missing_time_columns}"
        )

    df[EVENT_DATE_COLUMN] = pd.to_datetime(
        df[EVENT_DATE_COLUMN],
        errors="coerce",
    )

    df[REFRESH_DATE_COLUMN] = pd.to_datetime(
        df[REFRESH_DATE_COLUMN],
        errors="coerce",
    )

    df = df.sort_values(
        EVENT_DATE_COLUMN,
        ascending=True,
    ).reset_index(drop=True)

    snapshot_path, metadata_path = save_crime_snapshot(
        df=df,
        output_directory=output_directory,
        source_start_date=start_date,
        source_date_column=date_column,
    )

    logging.info("Saved full SPD Crime snapshot with %s rows", len(df))
    logging.info("Saved SPD Crime snapshot to %s", snapshot_path)
    logging.info("Saved SPD Crime metadata to %s", metadata_path)

    return snapshot_path, metadata_path


def incremental_refresh_crime_snapshot(
    output_directory: str | Path = CRIME_OUTPUT_DIR,
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    validate_positive_int(rolling_window_days, "rolling_window_days")
    validate_nonnegative_int(overlap_days, "overlap_days")
    validate_positive_int(page_size, "page_size")
    validate_timeout(timeout)

    output_directory = Path(output_directory)

    try:
        existing_df, metadata = load_crime_snapshot(output_directory)
    except FileNotFoundError:
        start_date = get_default_start_date(
            rolling_window_days=rolling_window_days,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        logging.info(
            "No existing crime snapshot found. Running initial full refresh from %s",
            start_date,
        )

        return full_refresh_crime_snapshot(
            start_date=start_date,
            page_size=page_size,
            max_pages=None,
            timeout=timeout,
            output_directory=output_directory,
            date_column=EVENT_DATE_COLUMN,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    missing_key_columns = [
        column
        for column in DEDUPLICATION_KEY
        if column not in existing_df.columns
    ]

    if missing_key_columns:
        raise ValueError(
            f"Existing snapshot is missing deduplication columns: {missing_key_columns}"
        )

    required_time_columns = [
        EVENT_DATE_COLUMN,
        REFRESH_DATE_COLUMN,
    ]

    missing_time_columns = [
        column
        for column in required_time_columns
        if column not in existing_df.columns
    ]

    if missing_time_columns:
        raise ValueError(
            f"Existing snapshot is missing required time columns: {missing_time_columns}"
        )

    existing_df = existing_df.copy()

    existing_df[EVENT_DATE_COLUMN] = pd.to_datetime(
        existing_df[EVENT_DATE_COLUMN],
        errors="coerce",
    )

    existing_df[REFRESH_DATE_COLUMN] = pd.to_datetime(
        existing_df[REFRESH_DATE_COLUMN],
        errors="coerce",
    )

    latest_existing_report_timestamp = existing_df[REFRESH_DATE_COLUMN].max()

    if pd.isna(latest_existing_report_timestamp):
        raise ValueError("Existing snapshot has no valid report_date_time values")

    fetch_start_date = (
        latest_existing_report_timestamp.date() - timedelta(days=overlap_days)
    ).isoformat()

    logging.info(
        "Starting incremental SPD Crime refresh from %s using %s with overlap_days=%s",
        fetch_start_date,
        REFRESH_DATE_COLUMN,
        overlap_days,
    )

    new_df = load_crime_dataset(
        start_date=fetch_start_date,
        page_size=page_size,
        max_pages=None,
        timeout=timeout,
        date_column=REFRESH_DATE_COLUMN,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    logging.info("Fetched %s recent SPD Crime rows", len(new_df))

    combined_df = pd.concat(
        [existing_df, new_df],
        ignore_index=True,
    )

    combined_df[EVENT_DATE_COLUMN] = pd.to_datetime(
        combined_df[EVENT_DATE_COLUMN],
        errors="coerce",
    )

    combined_df[REFRESH_DATE_COLUMN] = pd.to_datetime(
        combined_df[REFRESH_DATE_COLUMN],
        errors="coerce",
    )

    before_deduplication = len(combined_df)

    combined_df = combined_df.drop_duplicates(
        subset=DEDUPLICATION_KEY,
        keep="last",
    )

    logging.info(
        "Removed %s duplicate rows",
        before_deduplication - len(combined_df),
    )

    latest_combined_offense_timestamp = combined_df[EVENT_DATE_COLUMN].max()

    if pd.isna(latest_combined_offense_timestamp):
        raise ValueError("Combined snapshot has no valid offense_date values")

    cutoff_timestamp = latest_combined_offense_timestamp - timedelta(
        days=rolling_window_days
    )

    combined_df = combined_df[
        combined_df[EVENT_DATE_COLUMN] >= cutoff_timestamp
    ].copy()

    combined_df = combined_df.sort_values(
        EVENT_DATE_COLUMN,
        ascending=True,
    ).reset_index(drop=True)

    logging.info(
        "Final rolling crime snapshot has %s rows from %s to %s by %s",
        len(combined_df),
        combined_df[EVENT_DATE_COLUMN].min(),
        combined_df[EVENT_DATE_COLUMN].max(),
        EVENT_DATE_COLUMN,
    )

    snapshot_path, metadata_path = save_crime_snapshot(
        df=combined_df,
        output_directory=output_directory,
        source_start_date=cutoff_timestamp.date().isoformat(),
        source_date_column=EVENT_DATE_COLUMN,
    )

    logging.info("Saved SPD Crime snapshot to %s", snapshot_path)
    logging.info("Saved SPD Crime metadata to %s", metadata_path)

    return snapshot_path, metadata_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    incremental_refresh_crime_snapshot()

    check_crime_freshness(
        fetch_source=lambda: fetch_latest_crime_dashboard_record(
            timeout=DEFAULT_TIMEOUT,
            max_retries=DEFAULT_MAX_RETRIES,
            retry_backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        )
    )


if __name__ == "__main__":
    main()
