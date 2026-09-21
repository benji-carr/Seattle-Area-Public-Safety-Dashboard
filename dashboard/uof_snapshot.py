"""Validated operational UOF snapshot and provenance metadata."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.uof_data import local_occurrence_times
from dashboard.uof_query import TIME_COLUMN, UOF_COLUMNS, UOF_DATASET_ID

UOF_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "data" / "processed" / "uof"
SNAPSHOT_FILENAME = "uof_data.parquet"
METADATA_FILENAME = "uof_metadata.json"
METADATA_KEYS = ["refreshed_at_utc", "source_dataset_id", "source_start_date",
                 "source_end_date", "row_count", "columns"]


def _validate_snapshot(frame: pd.DataFrame, metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("UOF metadata must be a dictionary")
    missing = set(METADATA_KEYS) - set(metadata)
    if missing:
        raise ValueError(f"UOF metadata is missing required keys: {sorted(missing)}")
    if metadata["source_dataset_id"] != UOF_DATASET_ID:
        raise ValueError("UOF metadata source_dataset_id mismatch")
    if len(frame) != metadata["row_count"]:
        raise ValueError("UOF snapshot row count mismatch")
    if list(frame.columns) != metadata["columns"]:
        raise ValueError("UOF snapshot column mismatch with metadata")
    if list(frame.columns) != UOF_COLUMNS:
        raise ValueError("UOF snapshot columns must match the canonical source schema")


def save_uof_snapshot(
    df: pd.DataFrame, output_directory: str | Path = UOF_OUTPUT_DIR,
    *, fetch_metadata: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    if not isinstance(df, pd.DataFrame) or list(df.columns) != UOF_COLUMNS:
        raise ValueError("UOF snapshot requires a DataFrame with canonical source columns")
    timestamps = local_occurrence_times(df[TIME_COLUMN]).dropna()
    metadata = {
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dataset_id": UOF_DATASET_ID,
        # Coverage of retained history, not the incremental query boundary.
        "source_start_date": timestamps.min().isoformat() if not timestamps.empty else None,
        "source_end_date": timestamps.max().isoformat() if not timestamps.empty else None,
        "row_count": len(df), "columns": list(df.columns),
    }
    if fetch_metadata is not None:
        metadata["last_fetch"] = fetch_metadata
    _validate_snapshot(df, metadata)
    serialized = json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_directory / SNAPSHOT_FILENAME
    metadata_path = output_directory / METADATA_FILENAME
    snapshot_temp = snapshot_path.with_suffix(".parquet.tmp")
    metadata_temp = metadata_path.with_suffix(".json.tmp")
    # Like population snapshots, serialize both before replacing either file.
    try:
        df.to_parquet(snapshot_temp, index=False)
        metadata_temp.write_text(serialized, encoding="utf-8")
        snapshot_temp.replace(snapshot_path)
        metadata_temp.replace(metadata_path)
    finally:
        snapshot_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return snapshot_path, metadata_path


def load_uof_snapshot(
    output_directory: str | Path = UOF_OUTPUT_DIR,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_directory = Path(output_directory)
    snapshot_path = output_directory / SNAPSHOT_FILENAME
    metadata_path = output_directory / METADATA_FILENAME
    for path in (snapshot_path, metadata_path):
        if not path.exists():
            raise FileNotFoundError(f"UOF snapshot file not found: {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    frame = pd.read_parquet(snapshot_path)
    _validate_snapshot(frame, metadata)
    return frame, metadata
