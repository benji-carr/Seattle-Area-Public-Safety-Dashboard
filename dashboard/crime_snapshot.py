import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.snapshot_models import CrimeSnapshotMetadata


SNAPSHOT_FILENAME = "crime_data.parquet"
METADATA_FILENAME = "crime_data_metadata.json"


def save_crime_snapshot(
    df: pd.DataFrame,
    output_directory: str | Path,
    source_start_date: str,
    source_date_column: str = "offense_date",
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
        "source_date_column": source_date_column,
        "row_count": len(df),
        "columns": list(df.columns),
    }

    with open(metadata_path, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)

    return snapshot_path, metadata_path


def load_crime_snapshot(
    output_directory: str | Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_path = Path(output_directory)
    snapshot_path = output_path / SNAPSHOT_FILENAME
    metadata_path = output_path / METADATA_FILENAME

    if not snapshot_path.exists():
        raise FileNotFoundError(f"Crime snapshot file not found: {snapshot_path}")

    if not metadata_path.exists():
        raise FileNotFoundError(f"Crime metadata file not found: {metadata_path}")

    with open(metadata_path, "r", encoding="utf-8") as file:
        metadata = json.load(file)

    CrimeSnapshotMetadata.model_validate(metadata)

    df = pd.read_parquet(snapshot_path)

    if len(df) != metadata["row_count"]:
        raise ValueError(
            f"Row count mismatch: expected {metadata['row_count']}, got {len(df)}"
        )

    if metadata["columns"] != list(df.columns):
        raise ValueError(
            f"Column mismatch: expected {metadata['columns']}, got {list(df.columns)}"
        )

    return df, metadata


if __name__ == "__main__":
    from dashboard.crime_source import crime_records_to_dataframe, fetch_crime_page

    start_date = "2025-01-01"
    date_column = "offense_date"

    records = fetch_crime_page(
        start_date=start_date,
        limit=100,
        offset=0,
        date_column=date_column,
    )

    df = crime_records_to_dataframe(records)

    snapshot_path, metadata_path = save_crime_snapshot(
        df,
        output_directory="data/processed/crime",
        source_start_date=start_date,
        source_date_column=date_column,
    )

    loaded_df, metadata = load_crime_snapshot(
        "data/processed/crime"
    )

    print(snapshot_path)
    print(metadata_path)
    print(metadata)
    print(loaded_df.head())
    print(loaded_df.dtypes)
