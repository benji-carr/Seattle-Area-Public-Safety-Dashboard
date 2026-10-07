import logging
import time
from typing import Any

import requests

from dashboard.refresh_models import RetryConfig, ScalarTimeoutConfig

from dashboard.crime_query import (
    build_crime_query_params,
)


CRIME_DATA_ENDPOINT = ("https://data.seattle.gov/resource/tazs-3rd5.json")

TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)

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


if __name__ == "__main__":
    records = fetch_crime_page(
        start_date="2025-01-01",
        limit=5,
        date_column="offense_date",
    )

    print(f"Number of records: {len(records)}")

    if records:
        print(records[0])

