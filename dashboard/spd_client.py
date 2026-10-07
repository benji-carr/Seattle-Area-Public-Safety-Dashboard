import logging
import time
from typing import Any

import requests

from dashboard.refresh_models import RetryConfig, RequestTimeoutConfig

from dashboard.spd_query import build_spd_call_query_params


SPD_CALL_ENDPOINT = (
    "https://data.seattle.gov/"
    "resource/33kz-ixgy.json"
)
TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
LOGGER = logging.getLogger(__name__)


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

if __name__ == "__main__":
    records = fetch_spd_call_page(
        start_date="2025-01-01",
        limit=5,
    )

    print(f"Number of records: {len(records)}")

    if records:
        print(records[0])

