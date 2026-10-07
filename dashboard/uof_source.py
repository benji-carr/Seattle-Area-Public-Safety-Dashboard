"""Collect and normalize the Seattle SPD UOF source; persistence and UI live separately."""

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
import requests

from dashboard.refresh_models import FiniteRetryConfig, FiniteTimeoutConfig, validate_integer


# Constants and query construction

UOF_DATASET_ID = "ppi5-g2bj"
TIME_COLUMN = "occured_date_time"  # Preserve the source spelling.
ID_COLUMN = "uniqueid"
UOF_COLUMNS = [
    "uniqueid", "incident_num", "incident_type", "occured_date_time",
    "precinct", "sector", "beat", "officer_id", "subject_id",
    "subject_race", "subject_gender",
]
UOF_ORDER = f"{TIME_COLUMN} DESC, {ID_COLUMN} ASC"
UOF_DATA_ENDPOINT = f"https://data.seattle.gov/resource/{UOF_DATASET_ID}.json"
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


def validate_iso_date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be in YYYY-MM-DD format")
    return value


def build_uof_query_params(
    start_date: str | None = None, *, end_date: str | None = None,
    limit: int = 1000, offset: int = 0,
    columns: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str | int]:
    """Use an inclusive start and exclusive end at local midnight."""
    start_date = validate_iso_date(start_date, "start_date")
    end_date = validate_iso_date(end_date, "end_date")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")
    validate_integer(limit, "limit", 1)
    validate_integer(offset, "offset", 0)
    if columns is not None and not isinstance(columns, (list, tuple)):
        raise ValueError("columns must be a nonempty list or tuple of source columns")
    selected = list(columns) if columns is not None else UOF_COLUMNS
    if not selected or any(column not in UOF_COLUMNS for column in selected):
        raise ValueError("columns must contain known UOF source columns")
    params = {"$select": ",".join(selected), "$order": UOF_ORDER,
              "$limit": limit, "$offset": offset}
    filters = []
    if start_date is not None:
        filters.append(f"{TIME_COLUMN} >= '{start_date}T00:00:00.000'")
    if end_date is not None:
        filters.append(f"{TIME_COLUMN} < '{end_date}T00:00:00.000'")
    if filters:
        params["$where"] = " AND ".join(filters)
    return params


# HTTP access


def _request_with_retries(
    *, params: dict[str, str | int], timeout: float, max_retries: int,
    retry_backoff_seconds: float, session: requests.Session | None = None,
    request_callback: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    FiniteRetryConfig(max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)
    FiniteTimeoutConfig(timeout=timeout)
    active_session = session if session is not None else requests.Session()
    try:
        for attempt in range(max_retries + 1):
            try:
                if request_callback is not None:
                    request_callback()
                response = active_session.get(UOF_DATA_ENDPOINT, params=params, timeout=timeout)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
                    raise ValueError("UOF source response must be a list of dictionaries")
                return payload
            except requests.HTTPError as error:
                status = error.response.status_code if error.response is not None else None
                if status not in TRANSIENT_STATUS_CODES or attempt == max_retries:
                    raise
            except (requests.ConnectionError, requests.Timeout):
                if attempt == max_retries:
                    raise
            delay = retry_backoff_seconds * 2 ** attempt
            LOGGER.info("Retrying UOF request after attempt %s in %.2fs", attempt + 1, delay)
            time.sleep(delay)
    finally:
        if session is None:
            active_session.close()
    raise RuntimeError("UOF request retry loop exited unexpectedly")


def fetch_uof_page(
    start_date: str | None = None, *, end_date: str | None = None,
    limit: int = 1000, offset: int = 0, timeout: float = 10.0,
    columns: list[str] | tuple[str, ...] | None = None,
    max_retries: int = 0, retry_backoff_seconds: float = 1.0,
    session: requests.Session | None = None,
    request_callback: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    return _request_with_retries(
        params=build_uof_query_params(start_date, end_date=end_date, limit=limit,
                                      offset=offset, columns=columns),
        timeout=timeout, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds,
        session=session, request_callback=request_callback,
    )


def fetch_latest_uof_dashboard_record(
    *, timeout: float = 10.0, max_retries: int = 0,
    retry_backoff_seconds: float = 1.0, session: requests.Session | None = None,
) -> dict[str, Any]:
    records = _request_with_retries(
        params={"$select": f"{TIME_COLUMN},{ID_COLUMN}",
                "$where": f"{TIME_COLUMN} IS NOT NULL AND {ID_COLUMN} IS NOT NULL AND {ID_COLUMN} != ''",
                "$order": UOF_ORDER, "$limit": 1},
        timeout=timeout, max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds, session=session,
    )
    if not records:
        raise ValueError("UOF source returned no valid dashboard records")
    cleaned = uof_records_to_dataframe(records)
    if cleaned[[TIME_COLUMN, ID_COLUMN]].iloc[0].isna().any():
        raise ValueError("Latest UOF source record has missing or invalid occured_date_time/uniqueid")
    return records[0]


# Record normalization

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


# Dataset assembly

def fetch_uof_dataset(
    start_date: str | None = None, *, end_date: str | None = None,
    page_size: int = 1000, max_pages: int | None = None, timeout: float = 10.0,
    max_retries: int = 0, retry_backoff_seconds: float = 1.0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    build_uof_query_params(start_date, end_date=end_date, limit=page_size)
    if max_pages is not None:
        validate_integer(max_pages, "max_pages", 1)
    records = []
    request_count = 0
    pages_fetched = 0
    started = time.monotonic()

    def count_request() -> None:
        nonlocal request_count
        request_count += 1

    with requests.Session() as session:
        while True:
            page = fetch_uof_page(
                start_date, end_date=end_date, limit=page_size, offset=pages_fetched * page_size,
                timeout=timeout, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds,
                session=session, request_callback=count_request,
            )
            records.extend(page)
            pages_fetched += 1
            if progress_callback is not None:
                progress_callback({"page_number": pages_fetched, "rows_fetched_this_page": len(page),
                                   "cumulative_rows": len(records), "request_count": request_count,
                                   "elapsed_seconds": time.monotonic() - started})
            exhausted = len(page) < page_size
            if exhausted or pages_fetched == max_pages:
                break
    frame = uof_records_to_dataframe(records)
    return {"dataframe": frame, "metadata": {
        "request_count": request_count, "pages_fetched": pages_fetched,
        "row_count": len(frame), "elapsed_seconds": time.monotonic() - started,
        "page_size": page_size, "exhausted": exhausted,
    }}


def load_uof_dataset(
    start_date: str | None = None, *, end_date: str | None = None,
    page_size: int = 1000, max_pages: int | None = None, timeout: float = 10.0,
    max_retries: int = 0, retry_backoff_seconds: float = 1.0,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> pd.DataFrame:
    return fetch_uof_dataset(
        start_date, end_date=end_date, page_size=page_size, max_pages=max_pages,
        timeout=timeout, max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=progress_callback,
    )["dataframe"]
