"""Fetch just the CAD inputs used by the crime dashboard's response metrics.

Aggregate dispatch rows only when priority and neighborhood are invariant for
an event. Conflicting events are re-read without aggregation so independent
first-non-null selection remains possible. Medians are always computed locally
from event rows, never from averages or medians of neighborhood summaries.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Lock

import pandas as pd
import requests
from pandera.errors import SchemaError, SchemaErrors

from dashboard.call_metric_schemas import AGGREGATE_COUNTS_SCHEMA

from dashboard.crime_call_support_data import (
    LATEST_TIME_COLUMN, SOURCE_COLUMNS, validate_call_metric_source,
)
from dashboard.spd_client import _request_with_retries
from dashboard.spd_config import ARRIVAL_TIME_COLUMN, EVENT_ID_COLUMN, ROW_ID_COLUMN, TIME_COLUMN
from dashboard.spd_query import build_spd_call_query_params

LOGGER = logging.getLogger(__name__)
GROUP_COLUMNS = [EVENT_ID_COLUMN, "priority", "dispatch_neighborhood"]
COUNT_COLUMN = "source_row_count"
NONNULL_ID_COUNT_COLUMN = "dispatch_id_count"


def _sql_literal(value):
    return "'" + value.replace("'", "''") + "'"


def _validate_fallback(raw, expected):
    """Compare original (unnormalized) groups, counts and independent extrema."""
    actual = raw.groupby(GROUP_COLUMNS, dropna=False, as_index=False).agg({
        TIME_COLUMN: "min", ARRIVAL_TIME_COLUMN: "min", LATEST_TIME_COLUMN: "max",
        ROW_ID_COLUMN: "size",
    }).rename(columns={ROW_ID_COLUMN: COUNT_COLUMN})

    def groups(frame):
        columns = GROUP_COLUMNS + [TIME_COLUMN, ARRIVAL_TIME_COLUMN, LATEST_TIME_COLUMN, COUNT_COLUMN]
        # A common null sentinel makes missing priorities/neighborhoods and NaT
        # arrivals comparable without normalizing distinct source group values.
        return {
            tuple(None if pd.isna(v) else v for v in row[:len(GROUP_COLUMNS)]):
            tuple(None if pd.isna(v) else v for v in row[len(GROUP_COLUMNS):])
            for row in frame[columns].itertuples(index=False, name=None)
        }

    if groups(actual) != groups(expected):
        raise ValueError("Fallback groups/counts/timestamps disagree with aggregates; retry calls refresh")


def _reject_ambiguous_first_values(raw):
    """Do not let a change in row order choose a different tied neighborhood.

    The legacy snapshot has no explicit rule for conflicting values at the same
    queued time. Until one is defined, refuse those cases rather than silently
    change the displayed metrics when compacting other events.
    """
    frame = raw.copy()
    frame[EVENT_ID_COLUMN] = frame[EVENT_ID_COLUMN].astype("string").str.strip().str.lower()
    frame["dispatch_neighborhood"] = frame["dispatch_neighborhood"].astype("string").str.strip().str.lower()
    frame["priority"] = pd.to_numeric(frame["priority"], errors="coerce")
    for column, skip_nulls in (("dispatch_neighborhood", False),
                               ("dispatch_neighborhood", True), ("priority", True)):
        candidates = frame.dropna(subset=[column]) if skip_nulls else frame
        earliest = candidates.groupby(EVENT_ID_COLUMN)[TIME_COLUMN].transform("min")
        tied = candidates.loc[candidates[TIME_COLUMN].eq(earliest)]
        counts = tied.groupby(EVENT_ID_COLUMN)[column].nunique(dropna=False)
        if counts.gt(1).any():
            raise ValueError(f"Ambiguous first {column} at identical queued time; "
                             "define a tie policy before refreshing calls metrics")


def build_metric_query(start_date, end_date, *, limit=50000, offset=0):
    # Reuse date/pagination validation, but avoid aliases matching their input
    # columns: this endpoint rejects those with aggregate-in-ungrouped-context.
    base = build_spd_call_query_params(start_date, end_date=end_date,
                                      limit=limit, offset=offset)
    return {"$query": (
        f"SELECT {','.join(GROUP_COLUMNS)},"
        f"min({TIME_COLUMN}) AS queued_min,"
        f"min({ARRIVAL_TIME_COLUMN}) AS arrived_min,"
        f"max({TIME_COLUMN}) AS {LATEST_TIME_COLUMN},"
        f"count(*) AS {COUNT_COLUMN},"
        f"count({ROW_ID_COLUMN}) AS {NONNULL_ID_COUNT_COLUMN} "
        f"WHERE {base['$where']} AND {EVENT_ID_COLUMN} IS NOT NULL "
        f"GROUP BY {','.join(GROUP_COLUMNS)} "
        f"ORDER BY {','.join(GROUP_COLUMNS)} LIMIT {limit} OFFSET {offset}"
    )}


def month_windows(start_date, end_date):
    """Half-open calendar partitions keep each server aggregation bounded."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end <= start:
        raise ValueError("end_date must be after start_date")
    while start < end:
        next_month = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        stop = min(next_month, end)
        yield start.isoformat(), stop.isoformat()
        start = stop


def fetch_call_metric_source(start_date, end_date, *, page_size=50000,
                             timeout=120, max_retries=5,
                             retry_backoff_seconds=5):
    """Fully reconcile the retained period, including deletions and corrections.

    There is no page cap. A request failure aborts the refresh before publication.
    Keep all priorities and missing arrivals because they still count as calls.
    """
    windows = list(month_windows(start_date, end_date))
    # Validate page_size even if the first response is empty.
    build_metric_query(start_date, end_date, limit=page_size)
    records = []
    request_count = 0
    counter_lock = Lock()

    def read_all(params_builder, *, session, aggregate=False, seen_dispatch_ids=None):
        nonlocal request_count
        result = []
        offset = 0
        seen = set()
        cursor = None
        while True:
            params = params_builder(offset if aggregate else cursor)
            page, _ = _request_with_retries(
                params=params, timeout=timeout, max_retries=max_retries,
                retry_backoff_seconds=retry_backoff_seconds, session=session)
            with counter_lock:
                request_count += 1
                request_number = request_count
            if aggregate:
                keys = [tuple(row.get(column) for column in GROUP_COLUMNS) for row in page]
                if len(set(keys)) != len(keys) or seen.intersection(keys):
                    raise ValueError("Duplicate aggregate groups during pagination; retry calls refresh")
                seen.update(keys)
            else:
                ids = [row.get(ROW_ID_COLUMN) for row in page]
                if any(not isinstance(value, str) or not value.strip() for value in ids):
                    raise ValueError("Missing or invalid fallback dispatch identifier")
                if len(set(ids)) != len(ids) or seen_dispatch_ids.intersection(ids):
                    raise ValueError("Duplicate fallback dispatch identifiers during pagination")
                seen_dispatch_ids.update(ids)
                if ids:
                    cursor = ids[-1]
            result.extend(page)
            LOGGER.info("Calls metric request=%s offset=%s rows=%s",
                        request_number, offset, len(page))
            if len(page) < page_size:
                return result
            offset += page_size

    def fetch_month(window):
        start, end = window
        LOGGER.info("Aggregating calls from %s to %s (exclusive)", start, end)
        # A requests.Session belongs to one worker; retries include 429 backoff.
        with requests.Session() as month_session:
            return read_all(lambda offset: build_metric_query(
                start, end, limit=page_size, offset=offset),
                session=month_session, aggregate=True)

    # Bounded concurrency limits API load and avoids serial round-trip costs.
    # map preserves calendar order; individual partitions paginate serially.
    with ThreadPoolExecutor(max_workers=4) as executor:
        for month_records in executor.map(fetch_month, windows):
            records.extend(month_records)

    with requests.Session() as session:
        columns = SOURCE_COLUMNS + [LATEST_TIME_COLUMN]
        grouped = pd.DataFrame(records).rename(columns={
            "queued_min": TIME_COLUMN, "arrived_min": ARRIVAL_TIME_COLUMN,
        }).reindex(columns=columns + [COUNT_COLUMN, NONNULL_ID_COUNT_COLUMN])
        if grouped.empty:
            raise ValueError("Calls metric query returned no events")
        grouped = validate_call_metric_source(grouped)
        for column in (COUNT_COLUMN, NONNULL_ID_COUNT_COLUMN):
            counts = pd.to_numeric(grouped[column], errors="raise")
            grouped[column] = counts
        try:
            AGGREGATE_COUNTS_SCHEMA.validate(grouped)
        except (SchemaError, SchemaErrors) as error:
            raise ValueError(f"Invalid aggregate dispatch counts: {error}") from error
        grouped[[COUNT_COLUMN, NONNULL_ID_COUNT_COLUMN]] = grouped[[COUNT_COLUMN, NONNULL_ID_COUNT_COLUMN]].astype("int64")

        # First merge groups straddling partitions. Do not normalize before this:
        # source spellings that normalize to the same event need a raw fallback.
        grouped = grouped.groupby(GROUP_COLUMNS, dropna=False, as_index=False).agg({
            TIME_COLUMN: "min", ARRIVAL_TIME_COLUMN: "min", LATEST_TIME_COLUMN: "max",
            COUNT_COLUMN: "sum", NONNULL_ID_COUNT_COLUMN: "sum",
        })
        normalized_ids = grouped[EVENT_ID_COLUMN].astype("string").str.strip().str.lower()
        conflicts = normalized_ids.duplicated(keep=False)
        conflict_ids = grouped.loc[conflicts, EVENT_ID_COLUMN].drop_duplicates().tolist()
        expected = grouped.loc[conflicts]
        if expected[COUNT_COLUMN].ne(expected[NONNULL_ID_COUNT_COLUMN]).any():
            raise ValueError("Fallback source has missing dispatch identifiers")
        raw_records = []
        seen_dispatch_ids = set()
        for index in range(0, len(conflict_ids), 50):
            batch = conflict_ids[index:index + 50]
            literals = ",".join(_sql_literal(value) for value in batch)

            def raw_params(cursor):
                params = build_spd_call_query_params(
                    start_date, end_date=end_date, limit=page_size,
                    columns=SOURCE_COLUMNS + [ROW_ID_COLUMN],
                    order=f"{ROW_ID_COLUMN} ASC")
                params["$where"] += f" AND {EVENT_ID_COLUMN} IN ({literals})"
                if cursor is not None:
                    params["$where"] += f" AND {ROW_ID_COLUMN} > {_sql_literal(cursor)}"
                return params

            raw_records.extend(read_all(raw_params, session=session, seen_dispatch_ids=seen_dispatch_ids))
        raw = pd.DataFrame(raw_records).reindex(columns=SOURCE_COLUMNS + [ROW_ID_COLUMN])
        if conflict_ids:
            raw = validate_call_metric_source(raw, require_latest=False)
            raw[LATEST_TIME_COLUMN] = raw[TIME_COLUMN]
            _validate_fallback(raw, expected)
            _reject_ambiguous_first_values(raw)
        raw[LATEST_TIME_COLUMN] = raw[TIME_COLUMN]
        parts = [part for part in (grouped.loc[~conflicts, columns], raw[columns]) if not part.empty]
        result = pd.concat(parts, ignore_index=True)
    stats = {"query_request_count": request_count,
             "aggregate_rows": len(records), "fallback_events": len(conflict_ids),
             "fallback_dispatch_rows": len(raw), "metric_source_rows": len(result)}
    LOGGER.info("Calls metric refresh statistics: %s", stats)
    return result, stats
