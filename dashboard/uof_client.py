"""Session-based, retrying client for the operational UOF source only."""

import logging
import math
import time
from collections.abc import Callable
from typing import Any

import requests

from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_query import (
    ID_COLUMN, TIME_COLUMN, UOF_DATASET_ID, UOF_ORDER,
    build_uof_query_params, validate_integer,
)

UOF_DATA_ENDPOINT = f"https://data.seattle.gov/resource/{UOF_DATASET_ID}.json"
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


def _request_with_retries(
    *, params: dict[str, str | int], timeout: float, max_retries: int,
    retry_backoff_seconds: float, session: requests.Session | None = None,
    request_callback: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    validate_integer(max_retries, "max_retries", 0)
    for value, name, positive in [(timeout, "timeout", True),
                                  (retry_backoff_seconds, "retry_backoff_seconds", False)]:
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (positive and value == 0)):
            raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
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
