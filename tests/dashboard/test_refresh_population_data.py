from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

from dashboard import population_client
from dashboard.population_snapshot import load_population_snapshot
from scripts.dashboard import refresh_population_data as refresh


@pytest.fixture
def mocked_sources(monkeypatch, population_inputs):
    acs, decennial, geometry, mcpp = population_inputs
    calls = []
    downloaded_paths = []
    monkeypatch.setattr(refresh, "get_census_api_key", lambda: "test-secret")
    monkeypatch.setattr(refresh, "load_mcpp_boundaries", lambda: mcpp)

    def fetch_acs(year, *, timeout):
        calls.append(("acs", year, timeout))
        return acs

    def fetch_city(year, *, timeout):
        calls.append(("city", year, timeout))
        return pd.DataFrame({"B01003_001E": ["900"]})

    def download(destination, *, timeout):
        calls.append(("geometry", timeout))
        downloaded_paths.append(destination)
        destination.write_bytes(b"mock archive")
        return destination

    def read_geometry(path, *, bbox):
        assert path == f"zip://{downloaded_paths[-1]}"
        assert len(bbox) == 4
        return geometry

    monkeypatch.setattr(refresh, "fetch_acs_block_group_population", fetch_acs)
    monkeypatch.setattr(refresh, "fetch_acs_seattle_population", fetch_city)
    monkeypatch.setattr(refresh, "fetch_2020_block_population", lambda **kwargs: decennial)
    monkeypatch.setattr(refresh, "download_king_county_2020_block_geometry", download)
    monkeypatch.setattr(refresh.gpd, "read_file", read_geometry)
    return calls, downloaded_paths


def test_refresh_orchestrates_and_saves(mocked_sources, tmp_path, caplog):
    calls, paths = mocked_sources
    with caplog.at_level("INFO"):
        snapshot, metadata_path = refresh.refresh_population_snapshot(
            acs_year=2023, output_directory=tmp_path, timeout=17, download_timeout=31,
        )
    loaded, metadata = load_population_snapshot(tmp_path)
    assert len(loaded) == 3
    assert metadata["acs_year"] == 2023
    assert metadata["assigned_block_count"] == 2
    assert snapshot.exists() and metadata_path.exists()
    assert calls == [("acs", 2023, 17), ("city", 2023, 17), ("geometry", 31)]
    assert not paths[0].parent.exists()
    assert "raw_reconciliation_percentage=0.0" in caplog.text
    assert "test-secret" not in caplog.text


def test_qa_failure_leaves_previous_snapshot_untouched(mocked_sources, monkeypatch, tmp_path):
    _, paths = mocked_sources
    outputs = refresh.refresh_population_snapshot(output_directory=tmp_path)
    before = [path.read_bytes() for path in outputs]
    monkeypatch.setattr(refresh, "fetch_acs_seattle_population", lambda *a, **k: pd.DataFrame({"B01003_001E": ["1000"]}))
    with pytest.raises(ValueError, match="exceeds 1%"):
        refresh.refresh_population_snapshot(output_directory=tmp_path)
    assert [path.read_bytes() for path in outputs] == before
    assert not paths[-1].parent.exists()


def test_missing_key_fails_before_geography(monkeypatch, tmp_path):
    monkeypatch.setattr(population_client, "load_dotenv", lambda path: None)
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)

    def unexpected_call():
        pytest.fail("Geography should not load without a Census key")

    monkeypatch.setattr(refresh, "load_mcpp_boundaries", unexpected_call)
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
        refresh.refresh_population_snapshot(output_directory=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_main_configurable_year(monkeypatch):
    calls = []
    monkeypatch.setattr(refresh, "refresh_population_snapshot", lambda **kwargs: calls.append(kwargs))
    refresh.main([])
    refresh.main(["--acs-year", "2023"])
    assert calls == [{"acs_year": 2024}, {"acs_year": 2023}]


def test_direct_script_entrypoint_from_another_directory(tmp_path):
    script = Path(refresh.__file__).resolve()
    result = subprocess.run(
        [sys.executable, str(script), "--help"], cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--acs-year" in result.stdout
