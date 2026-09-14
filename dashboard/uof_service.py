"""Paginate the UOF stream to exhaustion by default."""

import time
from collections.abc import Callable
from typing import Any

import pandas as pd
import requests

from dashboard.uof_client import fetch_uof_page
from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_query import build_uof_query_params, validate_integer


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
