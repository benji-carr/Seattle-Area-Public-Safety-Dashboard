from typing import Any

import pandas as pd

from collections.abc import Callable

import time

from dashboard.crime_client import (
    fetch_crime_page,
)
from dashboard.crime_data import (
    crime_records_to_dataframe,
)


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
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise ValueError("page_size must be an integer")

    if page_size < 1:
        raise ValueError("page_size must be at least 1")

    if max_pages is not None:
        if isinstance(max_pages, bool) or not isinstance(max_pages, int):
            raise ValueError("max_pages must be an integer or None")

        if max_pages < 1:
            raise ValueError("max_pages must be at least 1 or None")

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


if __name__ == "__main__":
    df = load_crime_dataset(
        start_date="2025-01-01",
        page_size=1000,
        max_pages=2,
        date_column="offense_date",
    )

    print(df.shape)
    print(df.head())
    print(df.dtypes)
