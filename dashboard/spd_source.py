"""Collect and normalize SPD calls; persistence and UI live separately."""

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
import requests

from dashboard.refresh_models import (
    PaginationConfig, QueryPagination, RequestTimeoutConfig, RetryConfig,
)


# Constants and query construction

SPD_CALL_COLUMNS = [
    "cad_event_number",
    "cad_event_original_time_queued",
    "cad_event_arrived_time",
    "cad_event_clearance_description",
    "call_sign_dispatch_id",
    "call_type",
    "priority",
    "initial_call_type",
    "final_call_type",
    "cad_event_response_category",
    "dispatch_precinct",
    "dispatch_sector",
    "dispatch_beat",
    "dispatch_neighborhood",
    "dispatch_latitude",
    "dispatch_longitude",
    "count_of_officers",
    "event_group",
]
SPD_CALL_ENDPOINT = (
    "https://data.seattle.gov/"
    "resource/33kz-ixgy.json"
)
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


def _validate_iso_date(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid date in YYYY-MM-DD format") from error
    if parsed_date.isoformat() != value:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format")
    return value


def build_spd_call_query_params(
    start_date: str | None = None,
    *,
    end_date: str | None = None,
    limit: int = 1000,
    offset: int = 0,
    columns: list[str] | tuple[str, ...] | None = None,
    order: str = "cad_event_original_time_queued DESC",
) -> dict[str, str | int]:
    start_date = _validate_iso_date(start_date, "start_date")
    end_date = _validate_iso_date(end_date, "end_date")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")

    QueryPagination(limit=limit, offset=offset)

    selected_columns = list(columns) if columns is not None else SPD_CALL_COLUMNS
    if not selected_columns:
        raise ValueError("columns cannot be empty")

    filters: list[str] = []
    if start_date is not None:
        filters.append(f"cad_event_original_time_queued >= '{start_date}T00:00:00.000'")
    if end_date is not None:
        filters.append(f"cad_event_original_time_queued < '{end_date}T00:00:00.000'")

    params = {
        "$select": ",".join(selected_columns),
        "$order": order,
        "$limit": limit,
        "$offset": offset,
    }
    if filters:
        params["$where"] = " AND ".join(filters)
    return params


# HTTP access


def _normalize_timeout(timeout: float | tuple[float, float]) -> float | tuple[float, float]:
    value = RequestTimeoutConfig(timeout=timeout).timeout
    return tuple(float(part) for part in value) if isinstance(value, tuple) else float(value)


def _request_with_retries(
    *,
    params: dict[str, str | int],
    timeout: float | tuple[float, float],
    max_retries: int,
    retry_backoff_seconds: float,
    session: requests.Session | None = None,
) -> tuple[list[dict[str, Any]], int]:
    RetryConfig(max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)

    normalized_timeout = _normalize_timeout(timeout)
    active_session = session or requests.Session()
    attempts = max_retries + 1
    try:
        for attempt in range(1, attempts + 1):
            try:
                response = active_session.get(
                    SPD_CALL_ENDPOINT,
                    params=params,
                    timeout=normalized_timeout,
                )
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, list):
                    raise ValueError("Top-level JSON is not a list")
                if not all(isinstance(item, dict) for item in data):
                    raise ValueError("Not all items in JSON object are dictionaries")
                return data, attempt
            except requests.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                retryable = status_code in TRANSIENT_STATUS_CODES
                if attempt >= attempts or not retryable:
                    raise
                delay = retry_backoff_seconds * (2 ** (attempt - 1))
                LOGGER.info(
                    "Retrying SPD request after HTTP %s on attempt %s/%s in %.2fs",
                    status_code,
                    attempt,
                    attempts,
                    delay,
                )
                time.sleep(delay)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= attempts:
                    raise
                delay = retry_backoff_seconds * (2 ** (attempt - 1))
                LOGGER.info(
                    "Retrying SPD request after %s on attempt %s/%s in %.2fs",
                    exc.__class__.__name__,
                    attempt,
                    attempts,
                    delay,
                )
                time.sleep(delay)
    finally:
        if session is None:
            active_session.close()


def fetch_spd_call_page(
    start_date: str | None = None,
    *,
    end_date: str | None = None,
    limit: int = 1000,
    offset: int = 0,
    timeout: float | tuple[float, float] = 10.0,
    columns: list[str] | tuple[str, ...] | None = None,
    order: str = "cad_event_original_time_queued DESC",
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    params = build_spd_call_query_params(
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
        columns=columns,
        order=order,
    )
    data, _ = _request_with_retries(
        params=params,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        session=session,
    )
    return data


def fetch_latest_spd_dashboard_record(
    *,
    timeout: float | tuple[float, float] = 10.0,
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Fetch the newest source record that can appear in the calls dashboard."""
    data, _ = _request_with_retries(
        params={
            "$select": "cad_event_original_time_queued,cad_event_number",
            "$where": "cad_event_original_time_queued IS NOT NULL AND cad_event_number IS NOT NULL",
            "$order": "cad_event_original_time_queued DESC",
            "$limit": 1,
        },
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        session=session,
    )
    if not data:
        raise ValueError("SPD Calls source returned no valid dashboard records")
    return data[0]


# Record normalization

def spd_calls_to_dataframe(
    records: list[dict[str, Any]],
) -> pd.DataFrame:
    if not isinstance(records, list):
        raise ValueError("Top-level JSON is not a list")
    if not all(isinstance(item, dict) for item in records):
        raise ValueError("Not all items in JSON object are dictionaries")

    df = pd.DataFrame.from_records(records)

    for column in SPD_CALL_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    numeric_columns = [
        "priority",
        "dispatch_latitude",
        "dispatch_longitude",
        "count_of_officers",
    ]

    date_columns = [
        "cad_event_original_time_queued",
        "cad_event_arrived_time",
    ]

    text_columns = [
        "cad_event_number",
        "cad_event_clearance_description",
        "call_sign_dispatch_id",
        "call_type",
        "initial_call_type",
        "final_call_type",
        "cad_event_response_category",
        "dispatch_precinct",
        "dispatch_sector",
        "dispatch_beat",
        "dispatch_neighborhood",
        "event_group",
    ]

    cat_columns = [
        "cad_event_clearance_description",
        "call_type",
        "initial_call_type",
        "final_call_type",
        "cad_event_response_category",
        "dispatch_precinct",
        "dispatch_sector",
        "dispatch_beat",
        "dispatch_neighborhood",
        "event_group",
    ]

    cleaned_df = df.copy()
    cleaned_df[numeric_columns] = cleaned_df[numeric_columns].apply(pd.to_numeric, errors='coerce')
    cleaned_df[date_columns] = cleaned_df[date_columns].apply(pd.to_datetime, errors='coerce')

    cleaned_df[text_columns] = cleaned_df[text_columns].apply(
        lambda column: column.str.strip())
    cleaned_df[cat_columns] = cleaned_df[cat_columns].apply(
        lambda column: column.str.lower()
    )

    cleaned_df = cleaned_df.reset_index(drop=True)
    cleaned_df = cleaned_df.reindex(columns=SPD_CALL_COLUMNS)
    return cleaned_df


# Dataset assembly

def fetch_spd_call_dataset(
    start_date: str | None = None,
    *,
    end_date: str | None = None,
    page_size: int = 1000,
    max_pages: int | None = 3,
    timeout: float | tuple[float, float] = 10.0,
    columns: list[str] | tuple[str, ...] | None = None,
    order: str = "cad_event_original_time_queued DESC",
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    progress_callback: Callable[[dict], None] | None = None,
) -> dict:
    PaginationConfig(page_size=page_size, max_pages=max_pages)

    all_records = []
    offset = 0
    pages_fetched = 0
    request_count = 0
    started = time.monotonic()

    while True:
        page_started = time.monotonic()
        page = fetch_spd_call_page(
            start_date=start_date,
            end_date=end_date,
            limit=page_size,
            offset=offset,
            timeout=timeout,
            columns=columns,
            order=order,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        request_count += 1
        all_records.extend(page)
        pages_fetched += 1

        if progress_callback is not None:
            progress_callback(
                {
                    "page_number": pages_fetched,
                    "rows_fetched_this_page": len(page),
                    "cumulative_rows": len(all_records),
                    "offset": offset,
                    "elapsed_seconds": round(time.monotonic() - started, 6),
                    "page_elapsed_seconds": round(time.monotonic() - page_started, 6),
                }
            )

        if len(page) < page_size:
            break
        if pages_fetched == max_pages:
            break

        offset += page_size

    frame = spd_calls_to_dataframe(all_records)
    if columns is not None:
        frame = frame.loc[:, list(columns)].copy()

    return {
        "dataframe": frame,
        "metadata": {
            "request_count": request_count,
            "pages_fetched": pages_fetched,
            "row_count": int(len(frame)),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "page_size": page_size,
        },
    }


def load_spd_call_dataset(
    start_date: str | None,
    page_size: int = 1000,
    max_pages: int | None = 3,
    timeout: float | tuple[float, float] = 10.0,
    *,
    end_date: str | None = None,
    columns: list[str] | tuple[str, ...] | None = None,
    order: str = "cad_event_original_time_queued DESC",
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    progress_callback: Callable[[dict], None] | None = None,
) -> pd.DataFrame:
    result = fetch_spd_call_dataset(
        start_date=start_date,
        end_date=end_date,
        page_size=page_size,
        max_pages=max_pages,
        timeout=timeout,
        columns=columns,
        order=order,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=progress_callback,
    )
    return result["dataframe"]
