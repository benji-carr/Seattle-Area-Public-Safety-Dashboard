"""Parquet population snapshot and its reproducibility/QA metadata."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.population_client import (
    CENSUS_BLOCK_VINTAGE, POPULATION_MOE_VARIABLE, POPULATION_VARIABLE, PROJECT_ROOT,
)
from dashboard.population_service import POPULATION_COLUMNS


POPULATION_OUTPUT_DIR = PROJECT_ROOT / "data" / "processed" / "population"
SNAPSHOT_FILENAME = "population_estimates.parquet"
METADATA_FILENAME = "population_metadata.json"
QA_KEYS = [
    "city_population", "raw_mcpp_total", "calibrated_mcpp_total", "calibration_factor",
    "mcpp_count", "mcpps_represented", "assigned_2020_population", "assigned_block_count",
    "unmatched_acs_blocks", "zero_weight_block_groups", "raw_reconciliation_difference",
    "raw_reconciliation_percentage",
]
METADATA_KEYS = [
    "refreshed_at_utc", "acs_year", "row_count", "columns", *QA_KEYS,
    "population_variable", "population_moe_variable", "census_block_vintage",
]


def _validate_snapshot(df: pd.DataFrame, metadata: dict[str, Any]) -> None:
    if not isinstance(metadata, dict):
        raise ValueError("Population metadata must be a dictionary")
    missing = set(METADATA_KEYS) - set(metadata)
    if missing:
        raise ValueError(f"Population metadata is missing required keys: {sorted(missing)}")
    if len(df) != metadata["row_count"]:
        raise ValueError(f"Row count mismatch: expected {metadata['row_count']}, got {len(df)}")
    if list(df.columns) != metadata["columns"] or list(df.columns) != POPULATION_COLUMNS:
        raise ValueError("Column mismatch between population snapshot, schema, and metadata")
    mcpp = df.loc[df["geography_type"] == "mcpp"]
    city = df.loc[df["geography_type"] == "city"]
    if (len(city) != 1 or city.iloc[0]["geography_name"] != "seattle"
            or len(mcpp) != metadata["mcpp_count"] or len(df) != len(mcpp) + 1):
        raise ValueError("Population snapshot must contain all MCPP rows and one Seattle city row")
    if df.duplicated(["geography_type", "geography_name"]).any():
        raise ValueError("Duplicate population snapshot geographies")
    if (city.iloc[0]["population"] != metadata["city_population"]
            or city.iloc[0]["population_raw"] != metadata["city_population"]
            or mcpp["population_raw"].sum() != metadata["raw_mcpp_total"]
            or mcpp["population"].sum() != metadata["calibrated_mcpp_total"]):
        raise ValueError("Population totals mismatch between snapshot and metadata")
    if not df["population_year"].eq(metadata["acs_year"]).all():
        raise ValueError("ACS year mismatch between snapshot and metadata")


def save_population_snapshot(
    df: pd.DataFrame, qa: dict[str, Any],
    output_directory: str | Path = POPULATION_OUTPUT_DIR,
) -> tuple[Path, Path]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("Population snapshot must be a nonempty pandas DataFrame")
    if list(df.columns) != POPULATION_COLUMNS:
        raise ValueError("Column mismatch with population snapshot schema")
    missing = set(QA_KEYS) - set(qa)
    if missing:
        raise ValueError(f"Population QA is missing required keys: {sorted(missing)}")
    metadata = {
        **{key: qa[key] for key in QA_KEYS},
        "refreshed_at_utc": datetime.now(timezone.utc).isoformat(),
        "acs_year": int(df["population_year"].iloc[0]),
        "row_count": len(df), "columns": list(df.columns),
        "population_variable": POPULATION_VARIABLE,
        "population_moe_variable": POPULATION_MOE_VARIABLE,
        "census_block_vintage": CENSUS_BLOCK_VINTAGE,
    }
    _validate_snapshot(df, metadata)
    serialized = json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    snapshot_path = output_directory / SNAPSHOT_FILENAME
    metadata_path = output_directory / METADATA_FILENAME
    # Finish serialization before replacing either previous output file.
    snapshot_temp = snapshot_path.with_suffix(".parquet.tmp")
    metadata_temp = metadata_path.with_suffix(".json.tmp")
    try:
        df.to_parquet(snapshot_temp, index=False)
        metadata_temp.write_text(serialized, encoding="utf-8")
        snapshot_temp.replace(snapshot_path)
        metadata_temp.replace(metadata_path)
    finally:
        snapshot_temp.unlink(missing_ok=True)
        metadata_temp.unlink(missing_ok=True)
    return snapshot_path, metadata_path


def load_population_snapshot(
    output_directory: str | Path = POPULATION_OUTPUT_DIR,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    output_directory = Path(output_directory)
    snapshot_path = output_directory / SNAPSHOT_FILENAME
    metadata_path = output_directory / METADATA_FILENAME
    for path in (snapshot_path, metadata_path):
        if not path.exists():
            raise FileNotFoundError(f"Population snapshot file not found: {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    df = pd.read_parquet(snapshot_path)
    _validate_snapshot(df, metadata)
    return df, metadata
