import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


SNAPSHOT_FILENAME = "spd_calls.parquet"
METADATA_FILENAME = "spd_calls_metadata.json"


def save_spd_call_snapshot(
    df: pd.DataFrame,
    output_directory: str | Path,
    source_start_date: str,
) -> tuple[Path, Path]:
    if not isinstance(df, pd.DataFrame):
        raise ValueError("df must be a pandas DataFrame")
    output_path = Path(output_directory) 
    output_path.mkdir(parents=True, exist_ok=True)

    snapshot_path = output_path / SNAPSHOT_FILENAME
    metadata_path = output_path / METADATA_FILENAME
    df.to_parquet(snapshot_path, index=False)

    metadata = {
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_start_date": source_start_date,
        "row_count": len(df),
        "columns": list(df.columns),
    }

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)

    return snapshot_path, metadata_path


def load_spd_call_snapshot(
    output_directory: str | Path,
    *,
    columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_path = Path(output_directory)
    snapshot_path = output_path / SNAPSHOT_FILENAME
    metadata_path = output_path / METADATA_FILENAME

    with open(metadata_path, "r", encoding="utf-8") as file    :
        metadata = json.load(file)

    if not isinstance(metadata, dict):
        raise ValueError(f"Expected a dictionary for metadata, got {type(metadata).__name__}")
    
    df = pd.read_parquet(snapshot_path) if columns is None else pd.read_parquet(snapshot_path, columns=columns)

    if len(df) != metadata["row_count"]:
        raise ValueError(
            f"Row count mismatch: expected {metadata['row_count']}, got {len(df)}"
        ) 
    
    metadata_required_cols = [
        "refreshed_at_utc", 
        "source_start_date",
        "row_count",
        "columns",
    ]

    if not all (col in metadata for col in metadata_required_cols):
        raise ValueError(
            f"Metadata is missing required keys. Expected keys: {metadata_required_cols}, got {list(metadata.keys())}"
        ) 
    
    expected_columns = metadata["columns"] if columns is None else columns
    if columns is not None and not set(columns).issubset(metadata["columns"]):
        raise ValueError(f"Projected columns are missing from snapshot metadata: {columns}")
    if expected_columns != list(df.columns):
        raise ValueError(
            f"Column mismatch: expected {expected_columns}, got {list(df.columns)}"
        ) 

    return df, metadata


if __name__ == "__main__":
    from dashboard.spd_service import (
        load_spd_call_dataset,
    )

    df = load_spd_call_dataset(
        start_date="2025-01-01",
        page_size=100,
        max_pages=2,
    )

    snapshot_path, metadata_path = save_spd_call_snapshot(
        df,
        output_directory="data/processed",
        source_start_date="2025-01-01",
    )

    loaded_df, metadata = load_spd_call_snapshot(
        "data/processed"
    )

    print(snapshot_path)
    print(metadata_path)
    print(metadata)
    print(loaded_df.head())
    print(loaded_df.dtypes)
