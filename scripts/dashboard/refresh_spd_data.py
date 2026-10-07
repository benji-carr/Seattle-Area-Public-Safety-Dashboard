import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

from dashboard.refresh_models import SnapshotRefreshConfig, RollingRefreshConfig
from dashboard.refresh_schemas import CALLS_TIME_SCHEMA, CALLS_INCREMENTAL_SCHEMA, CALLS_DEDUPLICATED_SCHEMA

from dashboard.refresh_models import validate_positive_int, validate_nonnegative_int, validate_timeout

from dashboard.spd_source import (
    fetch_latest_spd_dashboard_record, load_spd_call_dataset,
)
from dashboard.spd_snapshot import (
    save_spd_call_snapshot,
    load_spd_call_snapshot,
)
from scripts.dashboard.check_data_freshness import (
    check_spd_calls_freshness,
)


TIME_COLUMN = "cad_event_original_time_queued"
DEDUPLICATION_KEY = ["call_sign_dispatch_id"]

DEFAULT_PAGE_SIZE = 5000
DEFAULT_MAX_PAGES = None
DEFAULT_TIMEOUT = 120.0
# Timestamp cutoff preserves time of day: 734 is the minimum whole-day
# lookback covering two complete 367-date periods, even after midnight.
DEFAULT_ROLLING_WINDOW_DAYS = 734
DEFAULT_OVERLAP_DAYS = 200
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_BACKOFF_SECONDS = 5.0

CALL_OUTPUT_DIRECTORY = Path("data/processed")


def get_default_start_date(
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> str:
    """
    Used when full refresh is necessary and no start date is provided.

    Anchors the initial pull to the latest available call date
    in the source dataset.
    """
    latest_record = fetch_latest_spd_dashboard_record(
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    latest_timestamp = pd.to_datetime(
        latest_record.get(TIME_COLUMN),
        errors="coerce",
    )

    if pd.isna(latest_timestamp):
        raise ValueError(
            f"Latest SPD source record has no valid {TIME_COLUMN}"
        )

    return (
        latest_timestamp.date()
        - timedelta(days=rolling_window_days)
    ).isoformat()


def incremental_refresh_spd_call_snapshot(
    output_directory: str | Path = CALL_OUTPUT_DIRECTORY,
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    RollingRefreshConfig(rolling_window_days=rolling_window_days, overlap_days=overlap_days,
                         page_size=page_size, timeout=timeout)

    output_directory = Path(output_directory)

    try:
        existing_df, metadata = load_spd_call_snapshot(
            output_directory
        )
    except FileNotFoundError:
        start_date = get_default_start_date(
            rolling_window_days=rolling_window_days,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        logging.info(
            "No existing SPD call snapshot found. "
            "Running initial full refresh from %s",
            start_date,
        )

        return full_refresh_spd_call_snapshot(
            start_date=start_date,
            page_size=page_size,
            max_pages=None,
            timeout=timeout,
            output_directory=output_directory,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    try:
        CALLS_INCREMENTAL_SCHEMA.validate(existing_df)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Existing snapshot is missing deduplication or required time columns: {error}") from error

    existing_df = existing_df.copy()

    existing_df[TIME_COLUMN] = pd.to_datetime(
        existing_df[TIME_COLUMN],
        errors="coerce",
    )

    latest_existing_timestamp = (
        existing_df[TIME_COLUMN].max()
    )

    if pd.isna(latest_existing_timestamp):
        raise ValueError(
            "Existing snapshot has no valid timestamps"
        )

    fetch_start_date = (
        latest_existing_timestamp.date()
        - timedelta(days=overlap_days)
    ).isoformat()

    logging.info(
        "Starting incremental SPD refresh from %s "
        "with overlap_days=%s",
        fetch_start_date,
        overlap_days,
    )

    new_df = load_spd_call_dataset(
        start_date=fetch_start_date,
        page_size=page_size,
        max_pages=None,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=lambda progress: logging.info(
            "SPD fetch page=%s rows=%s cumulative=%s "
            "page_elapsed=%.2fs elapsed=%.2fs",
            progress["page_number"],
            progress["rows_fetched_this_page"],
            progress["cumulative_rows"],
            progress["page_elapsed_seconds"],
            progress["elapsed_seconds"],
        ),
    )

    logging.info(
        "Fetched %s recent SPD rows",
        len(new_df),
    )

    combined_df = pd.concat(
        [existing_df, new_df],
        ignore_index=True,
    )

    combined_df[TIME_COLUMN] = pd.to_datetime(
        combined_df[TIME_COLUMN],
        errors="coerce",
    )

    before_deduplication = len(combined_df)

    combined_df = combined_df.drop_duplicates(
        subset=DEDUPLICATION_KEY,
        keep="last",
    )

    try:
        CALLS_DEDUPLICATED_SCHEMA.validate(combined_df)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Invalid deduplicated snapshot: {error}") from error

    logging.info(
        "Removed %s duplicate rows",
        before_deduplication - len(combined_df),
    )

    latest_combined_timestamp = (
        combined_df[TIME_COLUMN].max()
    )

    if pd.isna(latest_combined_timestamp):
        raise ValueError(
            "Combined snapshot has no valid timestamps"
        )

    cutoff_timestamp = (
        latest_combined_timestamp
        - timedelta(days=rolling_window_days)
    )

    combined_df = combined_df[
        combined_df[TIME_COLUMN] >= cutoff_timestamp
    ].copy()

    combined_df = combined_df.sort_values(
        TIME_COLUMN,
        ascending=True,
    ).reset_index(drop=True)

    logging.info(
        "Final rolling snapshot has %s rows from %s to %s",
        len(combined_df),
        combined_df[TIME_COLUMN].min(),
        combined_df[TIME_COLUMN].max(),
    )

    snapshot_path, metadata_path = save_spd_call_snapshot(
        combined_df,
        output_directory=output_directory,
        source_start_date=cutoff_timestamp.date().isoformat(),
    )

    logging.info(
        "Saved SPD call snapshot to %s",
        snapshot_path,
    )
    logging.info(
        "Saved SPD call metadata to %s",
        metadata_path,
    )

    return snapshot_path, metadata_path


def full_refresh_spd_call_snapshot(
    start_date: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int | None = DEFAULT_MAX_PAGES,
    timeout: float = DEFAULT_TIMEOUT,
    output_directory: str | Path = CALL_OUTPUT_DIRECTORY,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    SnapshotRefreshConfig(page_size=page_size, max_pages=max_pages, timeout=timeout)

    if not start_date:
        start_date = get_default_start_date(
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    logging.info(
        "Starting full SPD call snapshot refresh: "
        "start_date=%s",
        start_date,
    )

    df = load_spd_call_dataset(
        start_date=start_date,
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=lambda progress: logging.info(
            "SPD fetch page=%s rows=%s cumulative=%s "
            "page_elapsed=%.2fs elapsed=%.2fs",
            progress["page_number"],
            progress["rows_fetched_this_page"],
            progress["cumulative_rows"],
            progress["page_elapsed_seconds"],
            progress["elapsed_seconds"],
        ),
    )

    try:
        CALLS_TIME_SCHEMA.validate(df)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"SPD data is missing required time column: {error}") from error

    df[TIME_COLUMN] = pd.to_datetime(
        df[TIME_COLUMN],
        errors="coerce",
    )

    df = df.sort_values(
        TIME_COLUMN,
        ascending=True,
    ).reset_index(drop=True)

    snapshot_path, metadata_path = save_spd_call_snapshot(
        df,
        output_directory=output_directory,
        source_start_date=start_date,
    )

    logging.info(
        "Saved full SPD snapshot with %s rows",
        len(df),
    )
    logging.info(
        "Saved SPD call snapshot to %s",
        snapshot_path,
    )
    logging.info(
        "Saved SPD call metadata to %s",
        metadata_path,
    )

    return snapshot_path, metadata_path


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    start_date = get_default_start_date(
        rolling_window_days=DEFAULT_ROLLING_WINDOW_DAYS,
        timeout=DEFAULT_TIMEOUT,
        max_retries=DEFAULT_MAX_RETRIES,
        retry_backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
    )

    incremental_refresh_spd_call_snapshot(
        output_directory=CALL_OUTPUT_DIRECTORY,
        rolling_window_days=DEFAULT_ROLLING_WINDOW_DAYS,
        overlap_days=DEFAULT_OVERLAP_DAYS,
        page_size=DEFAULT_PAGE_SIZE,
        timeout=DEFAULT_TIMEOUT,
        max_retries=DEFAULT_MAX_RETRIES,
        retry_backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
    )

    check_spd_calls_freshness(
        fetch_source=lambda: fetch_latest_spd_dashboard_record(
            timeout=DEFAULT_TIMEOUT,
            max_retries=DEFAULT_MAX_RETRIES,
            retry_backoff_seconds=(
                DEFAULT_RETRY_BACKOFF_SECONDS
            ),
        )
    )


if __name__ == "__main__":
    main()
