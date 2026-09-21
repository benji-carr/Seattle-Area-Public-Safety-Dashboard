import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from dashboard.spd_service import (
    load_spd_call_dataset,
)
from dashboard.spd_snapshot import (
    save_spd_call_snapshot,
    load_spd_call_snapshot,
)
from scripts.dashboard.check_data_freshness import (
    check_spd_calls_freshness,
)
from dashboard.spd_client import (
    fetch_latest_spd_dashboard_record,
)


TIME_COLUMN = "cad_event_original_time_queued"
DEDUPLICATION_KEY = ["call_sign_dispatch_id"]

DEFAULT_PAGE_SIZE = 5000
DEFAULT_MAX_PAGES = None
DEFAULT_TIMEOUT = 60.0
# Timestamp cutoff preserves time of day: 734 is the minimum whole-day
# lookback covering two complete 367-date periods, even after midnight.
DEFAULT_ROLLING_WINDOW_DAYS = 734
DEFAULT_OVERLAP_DAYS = 14
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0

CALL_OUTPUT_DIRECTORY = Path("data/processed")


def get_default_start_date(
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> str:
    """
    Used only when no existing SPD call snapshot exists yet.

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
    if isinstance(timeout, bool) or not isinstance(
        timeout,
        (int, float),
    ):
        raise ValueError(
            "timeout must be an integer or float"
        )

    if timeout <= 0:
        raise ValueError(
            "timeout must be larger than zero"
        )


def incremental_refresh_spd_call_snapshot(
    output_directory: str | Path = CALL_OUTPUT_DIRECTORY,
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS,
    overlap_days: int = DEFAULT_OVERLAP_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    validate_positive_int(
        rolling_window_days,
        "rolling_window_days",
    )
    validate_nonnegative_int(
        overlap_days,
        "overlap_days",
    )
    validate_positive_int(
        page_size,
        "page_size",
    )
    validate_timeout(timeout)

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

    missing_key_columns = [
        column
        for column in DEDUPLICATION_KEY
        if column not in existing_df.columns
    ]

    if missing_key_columns:
        raise ValueError(
            "Existing snapshot is missing deduplication "
            f"columns: {missing_key_columns}"
        )

    if TIME_COLUMN not in existing_df.columns:
        raise ValueError(
            f"Existing snapshot is missing {TIME_COLUMN}"
        )

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
    validate_positive_int(
        page_size,
        "page_size",
    )
    validate_timeout(timeout)

    if max_pages is not None:
        validate_positive_int(
            max_pages,
            "max_pages",
        )

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
    )

    if TIME_COLUMN not in df.columns:
        raise ValueError(
            f"SPD call data is missing {TIME_COLUMN}"
        )

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

    incremental_refresh_spd_call_snapshot()

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