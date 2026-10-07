import time
from collections.abc import Callable

import pandas as pd

from dashboard.refresh_models import PaginationConfig

from dashboard.spd_client import fetch_spd_call_page
from dashboard.spd_data import spd_calls_to_dataframe


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


if __name__ == "__main__":
    df = load_spd_call_dataset(
        start_date="2025-01-01",
        page_size=25,
        max_pages=2,
    )

    print(df.head())
    print(f"\nRows: {len(df)}")
    print("\nDtypes:")
    print(df.dtypes)
    print("\nMissing values:")
    print(df.isna().sum())


