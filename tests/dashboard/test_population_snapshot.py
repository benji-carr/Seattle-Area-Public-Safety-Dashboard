import json

import pandas as pd
import pytest

from dashboard.population_service import build_population_estimates
from dashboard.population_snapshot import (
    METADATA_FILENAME, load_population_snapshot, save_population_snapshot,
)


def test_snapshot_round_trip(population_inputs, tmp_path):
    population, qa = build_population_estimates(*population_inputs, 900)
    paths = save_population_snapshot(population, qa, tmp_path)
    loaded, metadata = load_population_snapshot(tmp_path)
    pd.testing.assert_frame_equal(loaded, population)
    assert [path.name for path in paths] == ["population_estimates.parquet", "population_metadata.json"]
    assert metadata["row_count"] == 3
    assert metadata["acs_year"] == 2024
    assert metadata["population_variable"] == "B01003_001E"
    assert metadata["population_moe_variable"] == "B01003_001M"
    assert metadata["census_block_vintage"] == 2020
    assert metadata["refreshed_at_utc"].endswith("+00:00")
    for key, value in qa.items():
        assert metadata[key] == value


@pytest.mark.parametrize("key,value,match", [
    ("row_count", 99, "Row count mismatch"),
    ("columns", ["wrong"], "Column mismatch"),
    ("mcpp_count", 99, "all MCPP rows"),
    ("city_population", 901, "totals mismatch"),
    ("acs_year", 2023, "ACS year mismatch"),
])
def test_metadata_mismatches_fail(population_inputs, tmp_path, key, value, match):
    population, qa = build_population_estimates(*population_inputs, 900)
    _, path = save_population_snapshot(population, qa, tmp_path)
    metadata = json.loads(path.read_text())
    metadata[key] = value
    path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_population_snapshot(tmp_path)


@pytest.mark.parametrize("malformed,match", [([], "dictionary"), ({}, "missing required keys")])
def test_malformed_metadata_fails(population_inputs, tmp_path, malformed, match):
    population, qa = build_population_estimates(*population_inputs, 900)
    _, path = save_population_snapshot(population, qa, tmp_path)
    path.write_text(json.dumps(malformed), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_population_snapshot(tmp_path)


def test_missing_snapshot_and_metadata(population_inputs, tmp_path):
    with pytest.raises(FileNotFoundError, match="population_estimates.parquet"):
        load_population_snapshot(tmp_path)
    population, qa = build_population_estimates(*population_inputs, 900)
    save_population_snapshot(population, qa, tmp_path)
    (tmp_path / METADATA_FILENAME).unlink()
    with pytest.raises(FileNotFoundError, match="population_metadata.json"):
        load_population_snapshot(tmp_path)


def test_bad_qa_does_not_replace_snapshot(population_inputs, tmp_path):
    population, qa = build_population_estimates(*population_inputs, 900)
    paths = save_population_snapshot(population, qa, tmp_path)
    before = [path.read_bytes() for path in paths]
    with pytest.raises(ValueError, match="totals mismatch"):
        save_population_snapshot(population, {**qa, "city_population": 10}, tmp_path)
    assert [path.read_bytes() for path in paths] == before
