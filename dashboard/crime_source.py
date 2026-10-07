"""Collect and normalize the Seattle SPD crime source; persistence and UI live separately."""

import logging
import time
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd
import requests

from dashboard.refresh_models import (
    CrimeDateConfig, PaginationConfig, QueryPagination, RetryConfig,
    ScalarTimeoutConfig, VALID_CRIME_DATE_COLUMNS,
)


# Constants and query construction

CRIME_COLUMNS = [
    'report_number',
    'report_date_time',
    'offense_id',
    'offense_date',
    'nibrs_group_a_b',
    'nibrs_crime_against_category',
    'offense_sub_category',
    'shooting_type_group',
    'block_address',
    'latitude',
    'longitude',
    'beat',
    'precinct',
    'sector',
    'neighborhood',
    'reporting_area',
    'offense_category',
    'nibrs_offense_code_description',
    'nibrs_offense_code',
    'census_block_2020'
]
CRIME_DATA_ENDPOINT = ("https://data.seattle.gov/resource/tazs-3rd5.json")
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


def build_crime_query_params(
        start_date: str,
        limit: int = 1000,
        offset: int = 0,
        date_column : str = "offense_date",
) -> dict[str, str | int]:
    CrimeDateConfig(date_column=date_column)

    if not isinstance(start_date, str):
            raise ValueError("date must be a string")

    try:
        parsed_date = date.fromisoformat(start_date)
    except ValueError as error:
        raise ValueError("start_date must be a valid date in YYYY-MM-DD format") from error

    if parsed_date.isoformat() != start_date:
        raise ValueError("start_date must be in YYYY-MM-DD format")

    QueryPagination(limit=limit, offset=offset)

    params = {
            "$select": ",".join(CRIME_COLUMNS),
            "$where": (f"{date_column} >= '{start_date}T00:00:00.000'"),
            "$order": f"{date_column} DESC, offense_id ASC",
            "$limit": limit,
            "$offset": offset,
        }
    return params


# HTTP access


def _request_with_retries(
    *,
    params: dict[str, str | int],
    timeout: float,
    max_retries: int,
    retry_backoff_seconds: float,
) -> list[dict[str, Any]]:
    RetryConfig(max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)

    attempts = max_retries + 1

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(
                CRIME_DATA_ENDPOINT,
                params=params,
                timeout=timeout,
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, list):
                raise ValueError("Top-level JSON is not a list")

            if not all(isinstance(item, dict) for item in data):
                raise ValueError(
                    "Not all items in JSON response are dictionaries"
                )

            return data

        except requests.HTTPError as exc:
            status_code = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            retryable = status_code in TRANSIENT_STATUS_CODES

            if attempt >= attempts or not retryable:
                raise

            delay = retry_backoff_seconds * (2 ** (attempt - 1))

            LOGGER.info(
                "Retrying crime request after HTTP %s "
                "on attempt %s/%s in %.2fs",
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
                "Retrying crime request after %s "
                "on attempt %s/%s in %.2fs",
                exc.__class__.__name__,
                attempt,
                attempts,
                delay,
            )

            time.sleep(delay)

    raise RuntimeError("Crime request retry loop exited unexpectedly")


def fetch_crime_page(
    start_date: str,
    limit: int = 1000,
    offset: int = 0,
    timeout: float = 10.0,
    date_column: str = "offense_date",
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
) -> list[dict[str, Any]]:
    ScalarTimeoutConfig(timeout=timeout)

    params = build_crime_query_params(
        start_date=start_date,
        limit=limit,
        offset=offset,
        date_column=date_column,
    )

    return _request_with_retries(
        params=params,
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def fetch_latest_crime_dashboard_record(
    *,
    timeout: float = 10.0,
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    """Fetch the newest source record that can appear in the crime dashboard."""
    ScalarTimeoutConfig(timeout=timeout)

    data = _request_with_retries(
        params={
            "$select": "offense_date,offense_id",
            "$where": (
                "offense_date IS NOT NULL "
                "AND offense_id IS NOT NULL"
            ),
            "$order": "offense_date DESC, offense_id ASC",
            "$limit": 1,
        },
        timeout=timeout,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError("Crime source response must be a list of objects")
    if not data:
        raise ValueError("Crime source returned no valid dashboard records")
    return data[0]


# Record normalization

def crime_records_to_dataframe(
    records: list[dict[str, Any]],
) -> pd.DataFrame:
    if not isinstance(records, list):
        raise ValueError("Top-level JSON is not a list")

    if not all(isinstance(item, dict) for item in records):
        raise ValueError("Not all items in JSON object are dictionaries")

    df = pd.DataFrame.from_records(records)

    for column in CRIME_COLUMNS:
        if column not in df.columns:
            df[column] = pd.NA

    numeric_columns = [
        "latitude",
        "longitude",
    ]

    date_columns = [
        "report_date_time",
        "offense_date",
    ]

    text_columns = [
        "report_number",
        "offense_id",
        "block_address",
        "nibrs_offense_code",
        "census_block_2020",
    ]

    cat_columns = [
        "nibrs_group_a_b",
        "nibrs_crime_against_category",
        "offense_sub_category",
        "shooting_type_group",
        "beat",
        "precinct",
        "sector",
        "neighborhood",
        "reporting_area",
        "offense_category",
        "nibrs_offense_code_description",
    ]

    cleaned_df = df.copy()

    cleaned_df[numeric_columns] = cleaned_df[numeric_columns].apply(
        pd.to_numeric,
        errors="coerce",
    )

    cleaned_df[date_columns] = cleaned_df[date_columns].apply(
        pd.to_datetime,
        errors="coerce",
    )

    cleaned_df[text_columns] = cleaned_df[text_columns].apply(
        lambda column: column.astype("string").str.strip()
    )

    cleaned_df[cat_columns] = cleaned_df[cat_columns].apply(
        lambda column: column.astype("string").str.strip().str.lower()
    )

    cleaned_df = cleaned_df.reset_index(drop=True)
    cleaned_df = cleaned_df.reindex(columns=CRIME_COLUMNS)

    return cleaned_df


# Dataset assembly

def load_crime_dataset(
    start_date: str,
    page_size: int = 1000,
    max_pages: int | None = None,
    timeout: float = 10.0,
    date_column: str = "offense_date",
    max_retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    progress_callback: Callable[[dict], None] | None = None,
) -> pd.DataFrame:
    PaginationConfig(page_size=page_size, max_pages=max_pages)

    all_records: list[dict[str, Any]] = []
    page_number = 0

    started = time.monotonic()

    while True:
        if max_pages is not None and page_number >= max_pages:
            break

        offset = page_number * page_size

        page_started = time.monotonic()

        records = fetch_crime_page(
            start_date=start_date,
            limit=page_size,
            offset=offset,
            timeout=timeout,
            date_column=date_column,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
        )

        all_records.extend(records)

        if progress_callback is not None:
            progress_callback(
                {
                    "page_number": page_number + 1,
                    "rows_fetched_this_page": len(records),
                    "cumulative_rows": len(all_records),
                    "offset": offset,
                    "elapsed_seconds": round(
                        time.monotonic() - started,
                        6,
                    ),
                    "page_elapsed_seconds": round(
                        time.monotonic() - page_started,
                        6,
                    ),
                }
            )

        if len(records) < page_size:
            break

        page_number += 1

    df = crime_records_to_dataframe(all_records)

    return df
