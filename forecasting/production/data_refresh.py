"""Refresh and validate the bounded production target panel."""
from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

from forecasting.production.table_schemas import SOURCE_SCHEMA, SOURCE_TIMES_SCHEMA

from dashboard.refresh_models import RetryConfig, SplitTimeoutConfig, validate_positive_int

from dashboard.spd_source import fetch_spd_call_dataset, fetch_spd_call_page
from forecasting.features.xgboost import prepare_target_panel, validate_daily_panel
from forecasting.paths import TARGET_PANEL_5Y_PATH


LOGGER = logging.getLogger(__name__)

SOURCE_DATASET_ID = "33kz-ixgy"
SOURCE_IDENTIFIER = "Seattle Open Data SPD Calls"
SOURCE_PATH = "https://data.seattle.gov/resource/33kz-ixgy.json"
SOURCE_COLUMNS = {
    "cad_event_number",
    "cad_event_original_time_queued",
    "dispatch_neighborhood",
}
SOURCE_QUERY_COLUMNS = [
    "cad_event_number",
    "cad_event_original_time_queued",
    "dispatch_neighborhood",
]
SEATTLE_TZ = ZoneInfo("America/Los_Angeles")
MIN_FEATURE_HISTORY_DAYS = 28
DEFAULT_FULL_PAGE_SIZE = 50000
DEFAULT_PROGRESS_LOG_EVERY_PAGES = 10
DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
DEFAULT_READ_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
FULL_REFRESH_MODE = "full"
SMOKE_REFRESH_MODE = "smoke"
CONNECTIVITY_CHECK_MODE = "connectivity_check"
EVENT_LEVEL_PAGINATION_STRATEGY = "event_level_pagination"
SERVER_AGGREGATION_UNAVAILABLE_REASON = (
    "Socrata SoQL supports GROUP BY count aggregation, but not an exact "
    "count(distinct cad_event_number) grouped by day and neighborhood in a "
    "single query. Exact target parity therefore requires event-level transfer."
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seattle_today(now: datetime | None = None) -> pd.Timestamp:
    value = now or datetime.now(SEATTLE_TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=SEATTLE_TZ)
    return pd.Timestamp(value.astimezone(SEATTLE_TZ).date())


def default_timeout_config(
    *,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
) -> tuple[float, float]:
    validate_timeout_config(connect_timeout=connect_timeout, read_timeout=read_timeout)
    return float(connect_timeout), float(read_timeout)


def validate_timeout_config(*, connect_timeout: float, read_timeout: float) -> None:
    SplitTimeoutConfig(connect_timeout=connect_timeout, read_timeout=read_timeout)


def validate_retry_config(*, max_retries: int, retry_backoff_seconds: float) -> None:
    RetryConfig(max_retries=max_retries, retry_backoff_seconds=retry_backoff_seconds)


def validate_source_schema(source: pd.DataFrame) -> pd.DataFrame:
    try:
        SOURCE_SCHEMA.validate(source)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"SPD source schema is missing required columns: {error}") from error
    frame = source.copy()
    frame["cad_event_number"] = frame["cad_event_number"].astype("string").str.strip()
    frame["dispatch_neighborhood"] = (
        frame["dispatch_neighborhood"].astype("string").str.strip().str.upper()
    )
    timestamps = frame["cad_event_original_time_queued"].map(_coerce_event_time_to_seattle)
    try:
        SOURCE_TIMES_SCHEMA.validate(timestamps)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"SPD source contains invalid queued timestamps: {error}") from error
    frame["_event_time_local"] = pd.DatetimeIndex(timestamps.tolist())
    frame = frame.loc[
        frame["cad_event_number"].notna()
        & frame["cad_event_number"].ne("")
        & frame["dispatch_neighborhood"].notna()
        & frame["dispatch_neighborhood"].ne("")
    ].copy()
    if frame.empty:
        raise ValueError("SPD source has no valid CAD events with neighborhoods.")
    return frame


def _coerce_event_time_to_seattle(value: object) -> pd.Timestamp | pd.NaT:
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return pd.NaT
    if getattr(timestamp, "tzinfo", None) is None:
        return timestamp
    return timestamp.tz_convert(SEATTLE_TZ).tz_localize(None)


def normalize_expected_neighborhoods(expected_neighborhoods: list[str]) -> list[str]:
    return sorted(
        pd.Series(expected_neighborhoods, dtype="string")
        .str.strip()
        .str.upper()
        .dropna()
        .loc[lambda values: values.ne("")]
        .tolist()
    )


def summarize_neighborhood_filtering(source: pd.DataFrame, expected_neighborhoods: list[str]) -> dict:
    frame = validate_source_schema(source)
    expected = set(normalize_expected_neighborhoods(expected_neighborhoods))
    excluded = frame.loc[~frame["dispatch_neighborhood"].isin(expected)].copy()
    return {
        "validated_source_rows": int(len(frame)),
        "source_rows_after_neighborhood_filter": int(len(frame) - len(excluded)),
        "excluded_source_rows_due_to_unmodeled_neighborhoods": int(len(excluded)),
        "excluded_source_neighborhoods": sorted(excluded["dispatch_neighborhood"].astype(str).unique().tolist()),
    }


def complete_through_date(source: pd.DataFrame, now: datetime | None = None) -> dict:
    validated = validate_source_schema(source)
    latest_source_date = validated["_event_time_local"].dt.normalize().max()
    today = seattle_today(now)
    allowed = today - pd.Timedelta(days=1)
    return {
        "latest_source_date": latest_source_date,
        "seattle_today": today,
        "latest_allowed_complete_date": allowed,
        "selected_complete_through_date": min(latest_source_date, allowed),
    }


def build_target_panel(
    source: pd.DataFrame,
    *,
    expected_neighborhoods: list[str],
    selected_complete_through_date: str | pd.Timestamp,
    rolling_years: int = 5,
    start_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if not expected_neighborhoods:
        raise ValueError("Expected neighborhood set is empty.")
    frame = validate_source_schema(source)
    selected = pd.Timestamp(selected_complete_through_date).normalize()
    window_start = (
        pd.Timestamp(start_date).normalize()
        if start_date is not None
        else (selected - pd.DateOffset(years=rolling_years)) + pd.Timedelta(days=1)
    )
    if window_start > selected:
        raise ValueError("start_date cannot be later than selected_complete_through_date")
    frame["target_date"] = frame["_event_time_local"].dt.normalize()
    frame = frame.loc[(frame["target_date"] >= window_start) & (frame["target_date"] <= selected)].copy()
    # A CAD event is the target unit. Duplicate source rows never inflate calls.
    frame = frame.drop_duplicates(
        ["target_date", "dispatch_neighborhood", "cad_event_number"],
        keep="last",
    )
    expected = normalize_expected_neighborhoods(expected_neighborhoods)
    frame = frame.loc[frame["dispatch_neighborhood"].isin(expected)].copy()
    dates = pd.date_range(window_start, selected, freq="D")
    grid = pd.MultiIndex.from_product(
        [dates, expected],
        names=["target_date", "neighborhood"],
    ).to_frame(index=False)
    counts = (
        frame.groupby(["target_date", "dispatch_neighborhood"])["cad_event_number"]
        .nunique()
        .rename("calls")
        .reset_index()
        .rename(columns={"dispatch_neighborhood": "neighborhood"})
    )
    panel = grid.merge(counts, on=["target_date", "neighborhood"], how="left")
    panel["calls"] = panel["calls"].fillna(0.0).astype(float)
    return prepare_target_panel(panel)


def target_panel_sha256(panel: pd.DataFrame) -> str:
    canonical = prepare_target_panel(panel)[["target_date", "neighborhood", "calls"]].copy()
    canonical["target_date"] = canonical["target_date"].dt.strftime("%Y-%m-%d")
    canonical["neighborhood"] = canonical["neighborhood"].astype(str)
    canonical = canonical.sort_values(["target_date", "neighborhood"]).reset_index(drop=True)
    digest = hashlib.sha256("target_date|neighborhood|calls".encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(canonical, index=False).to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()


def validate_target_panel_for_artifact(
    panel: pd.DataFrame,
    expected_neighborhoods: list[str],
    complete_date: str | pd.Timestamp,
    *,
    require_min_history_days: bool = True,
) -> pd.DataFrame:
    validated = prepare_target_panel(panel)
    if validated.empty or not np.isfinite(validated["calls"]).all():
        raise ValueError("Target panel is empty or contains non-finite calls.")
    actual = set(
        validated["neighborhood"].astype("string").str.strip().str.upper().tolist()
    )
    expected = set(normalize_expected_neighborhoods(expected_neighborhoods))
    if actual != expected:
        raise ValueError(
            "Target panel entity set differs from artifact; "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    validate_daily_panel(validated)
    complete = pd.Timestamp(complete_date).normalize()
    if validated["target_date"].max() != complete or (validated["target_date"] > complete).any():
        raise ValueError("Target panel does not end exactly at the selected complete-through date.")
    if require_min_history_days and validated["target_date"].nunique() <= MIN_FEATURE_HISTORY_DAYS:
        raise ValueError("Target panel lacks the 28 days required for production features.")
    return validated


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.staging")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def _resolve_refresh_window(
    *,
    refresh_mode: str,
    now: datetime | None,
    start_date: str | None,
    end_date: str | None,
    rolling_years: int,
) -> tuple[str, str | None]:
    if refresh_mode not in {FULL_REFRESH_MODE, SMOKE_REFRESH_MODE}:
        raise ValueError(f"Unsupported refresh_mode: {refresh_mode}")

    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")

    if refresh_mode == FULL_REFRESH_MODE and (start_date is None) != (end_date is None):
        raise ValueError("Full refresh requires both start_date and end_date together, or neither.")

    if refresh_mode == FULL_REFRESH_MODE and start_date is None:
        today = seattle_today(now)
        full_start = ((today - pd.DateOffset(years=rolling_years)) - pd.Timedelta(days=1)).date().isoformat()
        return full_start, None

    if refresh_mode == SMOKE_REFRESH_MODE and start_date is None and end_date is None:
        today = seattle_today(now)
        smoke_end = today.date().isoformat()
        smoke_start = (today - pd.Timedelta(days=7)).date().isoformat()
        return smoke_start, smoke_end

    if start_date is None:
        raise ValueError("start_date is required when end_date is supplied")
    return start_date, end_date


def _log_event_fetch_progress(
    *,
    query_start_date: str,
    query_end_date: str | None,
    page_size: int,
    started: float,
    progress_log_every_pages: int,
    ) -> Callable[[dict], None]:
    def _callback(payload: dict) -> None:
        page_number = payload["page_number"]
        rows_fetched_this_page = payload["rows_fetched_this_page"]
        should_log = (
            page_number == 1
            or rows_fetched_this_page < page_size
            or page_number % progress_log_every_pages == 0
        )
        if should_log:
            LOGGER.info(
                "SPD refresh fetch alive | strategy=%s | window=%s..%s | page=%s | rows_this_page=%s | cumulative_rows=%s | elapsed=%.2fs",
                EVENT_LEVEL_PAGINATION_STRATEGY,
                query_start_date,
                query_end_date or "open",
                page_number,
                rows_fetched_this_page,
                payload["cumulative_rows"],
                time.monotonic() - started,
            )

    return _callback


def get_refresh_strategy_decision() -> dict:
    return {
        "selected_strategy": EVENT_LEVEL_PAGINATION_STRATEGY,
        "server_side_aggregation_supported": False,
        "server_side_aggregation_reason": SERVER_AGGREGATION_UNAVAILABLE_REASON,
    }


def run_connectivity_check(
    *,
    timeout: tuple[float, float] | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    limit: int = 1,
) -> dict:
    timeout = timeout or default_timeout_config(
        connect_timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout=10.0,
    )
    validate_retry_config(
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    started = time.monotonic()
    rows = fetch_spd_call_page(
        start_date=None,
        limit=limit,
        offset=0,
        timeout=timeout,
        columns=SOURCE_QUERY_COLUMNS,
        order="cad_event_original_time_queued DESC",
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    elapsed = round(time.monotonic() - started, 6)
    LOGGER.info(
        "SPD connectivity check complete | dataset=%s | rows=%s | latency=%.2fs",
        SOURCE_DATASET_ID,
        len(rows),
        elapsed,
    )
    return {
        "refresh_mode": CONNECTIVITY_CHECK_MODE,
        "refresh_strategy": EVENT_LEVEL_PAGINATION_STRATEGY,
        "refresh_method": EVENT_LEVEL_PAGINATION_STRATEGY,
        "source_dataset_id": SOURCE_DATASET_ID,
        "source_identifier": SOURCE_IDENTIFIER,
        "source_path": SOURCE_PATH,
        "api_request_count": 1,
        "source_rows_received": int(len(rows)),
        "aggregated_rows_received": 0,
        "target_rows_written": 0,
        "api_elapsed_seconds": elapsed,
        "aggregation_elapsed_seconds": 0.0,
        "validation_elapsed_seconds": 0.0,
        "total_refresh_elapsed_seconds": elapsed,
        "request_timeout_seconds": {
            "connect_timeout": timeout[0],
            "read_timeout": timeout[1],
        },
        "retry_policy": {
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
        },
        "server_side_aggregation_supported": False,
        "server_side_aggregation_reason": SERVER_AGGREGATION_UNAVAILABLE_REASON,
    }


def refresh_production_data(
    *,
    expected_neighborhoods: list[str],
    target_panel_path: str | Path = TARGET_PANEL_5Y_PATH,
    fetch_source=fetch_spd_call_dataset,
    now: datetime | None = None,
    page_size: int = DEFAULT_FULL_PAGE_SIZE,
    timeout: tuple[float, float] | None = None,
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = DEFAULT_READ_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
    refresh_mode: str = FULL_REFRESH_MODE,
    start_date: str | None = None,
    end_date: str | None = None,
    write_target_panel: bool | None = None,
    progress_log_every_pages: int = DEFAULT_PROGRESS_LOG_EVERY_PAGES,
    rolling_years: int = 5,
) -> dict:
    started = time.monotonic()
    start_utc = _utc_now()
    timeout = timeout or default_timeout_config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )
    validate_retry_config(
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    validate_positive_int(progress_log_every_pages, "progress_log_every_pages")

    if write_target_panel is None:
        write_target_panel = refresh_mode == FULL_REFRESH_MODE and start_date is None and end_date is None

    query_start_date, query_end_date = _resolve_refresh_window(
        refresh_mode=refresh_mode,
        now=now,
        start_date=start_date,
        end_date=end_date,
        rolling_years=rolling_years,
    )
    LOGGER.info(
        "Starting SPD refresh | mode=%s | strategy=%s | window=%s..%s | page_size=%s",
        refresh_mode,
        EVENT_LEVEL_PAGINATION_STRATEGY,
        query_start_date,
        query_end_date or "open",
        page_size,
    )

    api_started = time.monotonic()
    fetch_result = fetch_source(
        start_date=query_start_date,
        end_date=query_end_date,
        page_size=page_size,
        max_pages=None,
        timeout=timeout,
        columns=SOURCE_QUERY_COLUMNS,
        order="cad_event_original_time_queued ASC, cad_event_number ASC, dispatch_neighborhood ASC",
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        progress_callback=_log_event_fetch_progress(
            query_start_date=query_start_date,
            query_end_date=query_end_date,
            page_size=page_size,
            started=started,
            progress_log_every_pages=progress_log_every_pages,
        ),
    )
    api_elapsed = round(time.monotonic() - api_started, 6)

    if isinstance(fetch_result, dict) and "dataframe" in fetch_result:
        source = fetch_result["dataframe"]
        fetch_metadata = dict(fetch_result.get("metadata", {}))
    else:
        source = fetch_result
        fetch_metadata = {}
    source_rows_received = int(fetch_metadata.get("row_count", len(source)))
    api_request_count = int(fetch_metadata.get("request_count", 0))
    neighborhood_filtering = summarize_neighborhood_filtering(source, expected_neighborhoods)
    if neighborhood_filtering["excluded_source_rows_due_to_unmodeled_neighborhoods"] > 0:
        LOGGER.info(
            "Excluding non-modeled SPD neighborhoods | labels=%s | rows=%s",
            neighborhood_filtering["excluded_source_neighborhoods"],
            neighborhood_filtering["excluded_source_rows_due_to_unmodeled_neighborhoods"],
        )

    aggregation_started = time.monotonic()
    dates = complete_through_date(source, now)
    selected_complete_date = pd.Timestamp(dates["selected_complete_through_date"]).normalize()
    requested_start = pd.Timestamp(query_start_date).normalize()
    panel = build_target_panel(
        source,
        expected_neighborhoods=expected_neighborhoods,
        selected_complete_through_date=selected_complete_date,
        rolling_years=rolling_years,
        start_date=requested_start,
    )
    aggregation_elapsed = round(time.monotonic() - aggregation_started, 6)

    validation_started = time.monotonic()
    panel = validate_target_panel_for_artifact(
        panel,
        expected_neighborhoods,
        selected_complete_date,
        require_min_history_days=refresh_mode == FULL_REFRESH_MODE,
    )
    validation_elapsed = round(time.monotonic() - validation_started, 6)

    output = Path(target_panel_path)
    target_rows_written = int(len(panel)) if write_target_panel else 0
    if write_target_panel:
        _atomic_parquet(panel, output)

    summary = {
        "refresh_started_at_utc": start_utc,
        "refresh_completed_at_utc": _utc_now(),
        "refresh_elapsed_seconds": round(time.monotonic() - started, 6),
        "total_refresh_elapsed_seconds": round(time.monotonic() - started, 6),
        "refresh_mode": refresh_mode,
        "refresh_strategy": EVENT_LEVEL_PAGINATION_STRATEGY,
        "refresh_method": EVENT_LEVEL_PAGINATION_STRATEGY,
        "source_identifier": SOURCE_IDENTIFIER,
        "source_dataset_id": SOURCE_DATASET_ID,
        "source_path": SOURCE_PATH,
        "source_query_columns": SOURCE_QUERY_COLUMNS,
        "source_query_start_date": query_start_date,
        "source_query_end_date": query_end_date,
        "source_page_size": page_size,
        "api_request_count": api_request_count,
        "source_rows_received": source_rows_received,
        "validated_source_rows": neighborhood_filtering["validated_source_rows"],
        "source_rows_after_neighborhood_filter": neighborhood_filtering["source_rows_after_neighborhood_filter"],
        "excluded_source_rows_due_to_unmodeled_neighborhoods": neighborhood_filtering["excluded_source_rows_due_to_unmodeled_neighborhoods"],
        "excluded_source_neighborhoods": neighborhood_filtering["excluded_source_neighborhoods"],
        "aggregated_rows_received": 0,
        "target_rows_written": target_rows_written,
        "api_elapsed_seconds": api_elapsed,
        "aggregation_elapsed_seconds": aggregation_elapsed,
        "validation_elapsed_seconds": validation_elapsed,
        "request_timeout_seconds": {
            "connect_timeout": timeout[0],
            "read_timeout": timeout[1],
        },
        "retry_policy": {
            "max_retries": max_retries,
            "retry_backoff_seconds": retry_backoff_seconds,
        },
        "server_side_aggregation_supported": False,
        "server_side_aggregation_reason": SERVER_AGGREGATION_UNAVAILABLE_REASON,
        "latest_source_date": dates["latest_source_date"].date().isoformat(),
        "seattle_today": dates["seattle_today"].date().isoformat(),
        "latest_allowed_complete_date": dates["latest_allowed_complete_date"].date().isoformat(),
        "selected_complete_through_date": selected_complete_date.date().isoformat(),
        "target_panel_start": panel["target_date"].min().date().isoformat(),
        "target_panel_end": panel["target_date"].max().date().isoformat(),
        "n_target_dates": int(panel["target_date"].nunique()),
        "n_neighborhoods": int(panel["neighborhood"].nunique()),
        "n_target_rows": int(len(panel)),
        "target_calls": {
            "mean": float(panel.calls.mean()),
            "std": float(panel.calls.std(ddof=0)),
            "min": float(panel.calls.min()),
            "max": float(panel.calls.max()),
            "sum": float(panel.calls.sum()),
        },
        "target_panel_sha256": target_panel_sha256(panel),
        "target_panel_path": str(output),
        "target_panel_written": bool(write_target_panel),
    }
    summary.update(
        {
            key: value
            for key, value in fetch_metadata.items()
            if key not in {"row_count", "request_count"}
        }
    )
    return {"source": source, "target_panel": panel, "summary": summary}


__all__ = [
    "CONNECTIVITY_CHECK_MODE",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_PROGRESS_LOG_EVERY_PAGES",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    "DEFAULT_FULL_PAGE_SIZE",
    "EVENT_LEVEL_PAGINATION_STRATEGY",
    "FULL_REFRESH_MODE",
    "MIN_FEATURE_HISTORY_DAYS",
    "SEATTLE_TZ",
    "SERVER_AGGREGATION_UNAVAILABLE_REASON",
    "SMOKE_REFRESH_MODE",
    "SOURCE_COLUMNS",
    "SOURCE_DATASET_ID",
    "SOURCE_IDENTIFIER",
    "SOURCE_PATH",
    "SOURCE_QUERY_COLUMNS",
    "build_target_panel",
    "complete_through_date",
    "default_timeout_config",
    "get_refresh_strategy_decision",
    "refresh_production_data",
    "run_connectivity_check",
    "seattle_today",
    "summarize_neighborhood_filtering",
    "target_panel_sha256",
    "validate_retry_config",
    "validate_source_schema",
    "validate_target_panel_for_artifact",
    "validate_timeout_config",
]
