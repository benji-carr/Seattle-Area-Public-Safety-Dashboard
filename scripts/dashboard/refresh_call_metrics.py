"""Production calls refresh for neighborhood ranking and response-time KPIs."""

import json
import logging
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from dashboard.call_metrics_refresh import fetch_call_metric_source
from dashboard.crime_call_support_data import (
    METRICS_SUBDIRECTORY, METRIC_SCHEMA_VERSION, build_crime_call_support_context,
    validate_call_metric_source,
)
from dashboard.spd_client import fetch_latest_spd_dashboard_record
from dashboard.spd_config import DATA_PROCESSED_DIR, TIME_COLUMN
from dashboard.spd_snapshot import save_spd_call_snapshot
from scripts.dashboard.refresh_spd_data import DEFAULT_ROLLING_WINDOW_DAYS


def refresh_call_metrics(output_directory=DATA_PROCESSED_DIR, *, rolling_window_days=DEFAULT_ROLLING_WINDOW_DAYS):
    if isinstance(rolling_window_days, bool) or not isinstance(rolling_window_days, int) or rolling_window_days < 1:
        raise ValueError("rolling_window_days must be a positive integer")
    latest = pd.Timestamp(fetch_latest_spd_dashboard_record(
        timeout=120, max_retries=5, retry_backoff_seconds=5)[TIME_COLUMN]).date()
    start = (latest - timedelta(days=rolling_window_days)).isoformat()
    end = (latest + timedelta(days=1)).isoformat()
    source, stats = fetch_call_metric_source(start, end)
    source = validate_call_metric_source(source)
    context = build_crime_call_support_context(source, {})
    if context["valid_time"].empty or context["response_analysis"].empty:
        raise ValueError("Calls query produced empty dashboard metric inputs")
    # Stage the complete pair in a sibling directory before replacing published
    # files. Query/validation failures leave the last successful snapshot intact.
    base = Path(output_directory)
    base.mkdir(parents=True, exist_ok=True)
    directory = base / METRICS_SUBDIRECTORY
    with TemporaryDirectory(prefix=".calls_metrics_", dir=base) as staging:
        snapshot, metadata_path = save_spd_call_snapshot(source, staging, start)
        metadata = json.loads(metadata_path.read_text())
        metadata.update(stats, metric_schema_version=METRIC_SCHEMA_VERSION, source_end_date_exclusive=end)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
        directory.mkdir(parents=True, exist_ok=True)
        final_snapshot = directory / snapshot.name
        final_metadata = directory / metadata_path.name
        # These two replacements are not an atomic pair. Do not run local readers
        # during publication; see docs/calls_metric_refresh.md for crash recovery.
        snapshot.replace(final_snapshot)
        metadata_path.replace(final_metadata)
    return final_snapshot, final_metadata


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    paths = refresh_call_metrics()
    logging.info("Saved calls metric snapshot: %s", paths)


if __name__ == "__main__":
    main()
