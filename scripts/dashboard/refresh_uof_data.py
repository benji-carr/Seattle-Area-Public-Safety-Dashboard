"""Refresh the complete operational UOF history, retaining all historical rows."""

import logging
from datetime import timedelta
from pathlib import Path

import pandas as pd

from dashboard.uof_client import fetch_latest_uof_dashboard_record
from dashboard.uof_data import local_occurrence_times, uof_records_to_dataframe
from dashboard.uof_query import ID_COLUMN, TIME_COLUMN, validate_integer
from dashboard.uof_service import fetch_uof_dataset
from dashboard.uof_snapshot import (
    METADATA_FILENAME, SNAPSHOT_FILENAME, UOF_OUTPUT_DIR, load_uof_snapshot, save_uof_snapshot,
)
from scripts.dashboard.check_data_freshness import check_uof_freshness

DEFAULT_PAGE_SIZE = 5000
DEFAULT_TIMEOUT = 60.0
DEFAULT_OVERLAP_DAYS = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
LOGGER = logging.getLogger(__name__)


def _prepare_snapshot(frame: pd.DataFrame) -> pd.DataFrame:
    missing = {ID_COLUMN, TIME_COLUMN} - set(frame.columns)
    if missing:
        raise ValueError(f"UOF snapshot is missing required columns: {sorted(missing)}")
    cleaned = uof_records_to_dataframe(frame.to_dict("records"))
    if cleaned[ID_COLUMN].isna().any():
        raise ValueError("UOF records have missing uniqueid; cannot safely deduplicate")
    return (cleaned.drop_duplicates(ID_COLUMN, keep="last")
            .sort_values([TIME_COLUMN, ID_COLUMN], kind="stable", na_position="last")
            .reset_index(drop=True))


def incremental_refresh_uof_snapshot(
    output_directory: str | Path = UOF_OUTPUT_DIR, *, overlap_days: int = DEFAULT_OVERLAP_DAYS,
    page_size: int = DEFAULT_PAGE_SIZE, timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> tuple[Path, Path]:
    validate_integer(overlap_days, "overlap_days", 0)
    output_directory = Path(output_directory)
    start_date = None
    existing = None
    # A partial/corrupt snapshot must fail validation, not silently start over.
    if any((output_directory / name).exists() for name in (SNAPSHOT_FILENAME, METADATA_FILENAME)):
        existing, _ = load_uof_snapshot(output_directory)
        existing = _prepare_snapshot(existing)
        latest = local_occurrence_times(existing[TIME_COLUMN]).max()
        if pd.isna(latest):
            raise ValueError("Existing UOF snapshot has no valid occured_date_time")
        start_date = (latest.date() - timedelta(days=overlap_days)).isoformat()
        LOGGER.info("Incremental UOF refresh from %s; retaining %s historical rows", start_date, len(existing))
    else:
        LOGGER.info("Initial UOF refresh: fetching complete available history")
    result = fetch_uof_dataset(
        start_date=start_date, page_size=page_size, max_pages=None, timeout=timeout,
        max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=lambda progress: LOGGER.info("UOF fetch: %s", progress),
    )
    fetched = result["dataframe"]
    if not result["metadata"]["exhausted"]:
        raise ValueError("UOF fetch was truncated; refusing to save an incomplete refresh")
    if fetched.empty and existing is None:
        raise ValueError("Initial UOF fetch returned no records")
    combined = fetched if existing is None else pd.concat([existing, fetched], ignore_index=True)
    final = _prepare_snapshot(combined)
    paths = save_uof_snapshot(final, output_directory, fetch_metadata=result["metadata"])
    LOGGER.info("Saved %s UOF rows (%s duplicates removed) to %s", len(final), len(combined) - len(final), paths[0])
    return paths


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    incremental_refresh_uof_snapshot()
    check_uof_freshness(fetch_source=lambda: fetch_latest_uof_dashboard_record(
        timeout=DEFAULT_TIMEOUT, max_retries=DEFAULT_MAX_RETRIES,
        retry_backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
    ))


if __name__ == "__main__":
    main()
