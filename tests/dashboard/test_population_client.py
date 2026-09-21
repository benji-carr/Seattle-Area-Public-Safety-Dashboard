from io import BytesIO
from unittest.mock import MagicMock
from zipfile import ZipFile

import pytest
import requests

from dashboard import population_client as client


@pytest.fixture(autouse=True)
def census_environment(monkeypatch):
    monkeypatch.setattr(client, "load_dotenv", lambda path: None)
    monkeypatch.setenv("CENSUS_API_KEY", "unit-test-secret")


def mock_response(monkeypatch, payload=None, content=None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.json.return_value = payload
    response.iter_content.return_value = [b"", content] if content else []
    get = MagicMock(return_value=response)
    monkeypatch.setattr(client.requests, "get", get)
    return response, get


@pytest.mark.parametrize("fetch,header,row,endpoint,for_clause,in_clause", [
    (client.fetch_acs_block_group_population,
     ["NAME", "B01003_001E", "B01003_001M", "state", "county", "tract", "block group"],
     ["Block group", "1000", "50", "53", "033", "000100", "1"],
     "/2024/acs/acs5", "block group:*", "state:53 county:033 tract:*"),
    (client.fetch_acs_seattle_population,
     ["NAME", "B01003_001E", "B01003_001M", "state", "place"],
     ["Seattle", "754195", "20", "53", "63000"],
     "/2024/acs/acs5", "place:63000", "state:53"),
    (client.fetch_2020_block_population,
     ["NAME", "P1_001N", "state", "county", "tract", "block"],
     ["Block", "30", "53", "033", "000100", "1001"],
     "/2020/dec/pl", "block:*", "state:53 county:033 tract:*"),
])
def test_census_fetches_parse_response_and_geography(
    monkeypatch, fetch, header, row, endpoint, for_clause, in_clause,
):
    _, get = mock_response(monkeypatch, [header, row])
    frame = fetch(timeout=17)
    assert list(frame.columns) == header
    assert frame.iloc[0].tolist() == row
    assert get.call_args.args[0].endswith(endpoint)
    assert get.call_args.kwargs["timeout"] == 17
    params = get.call_args.kwargs["params"]
    assert params["for"] == for_clause
    assert params["in"] == in_clause
    assert params["key"] == "unit-test-secret"


def test_configurable_acs_year(monkeypatch):
    header = ["NAME", "B01003_001E", "B01003_001M", "state", "place"]
    _, get = mock_response(monkeypatch, [header, ["Seattle", "1", "1", "53", "63000"]])
    client.fetch_acs_seattle_population(2023)
    assert "/2023/acs/acs5" in get.call_args.args[0]


@pytest.mark.parametrize("payload", [
    {}, [], [["a"]], [["a", "a"], ["1", "2"]],
    [["a"], ["1", "2"]], [["a"], {"a": "1"}], [["a"], [["nested"]]],
    [["wrong"], ["1"]], [None, ["1"]],
])
def test_malformed_census_response_fails(payload):
    with pytest.raises(ValueError, match="Census API"):
        client.parse_census_response(payload, ["a"])


def test_missing_key_fails_before_http(monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY")
    _, get = mock_response(monkeypatch)
    with pytest.raises(RuntimeError, match=r"CENSUS_API_KEY.*environment.*\.env"):
        client.fetch_2020_block_population()
    get.assert_not_called()


def test_dotenv_preserves_environment(monkeypatch):
    paths = []
    monkeypatch.setattr(client, "load_dotenv", lambda path: paths.append(path))
    assert client.get_census_api_key() == "unit-test-secret"
    assert paths == [client.PROJECT_ROOT / ".env"]


@pytest.mark.parametrize("error", [requests.Timeout, requests.HTTPError])
def test_http_errors_do_not_expose_key(monkeypatch, error):
    response, _ = mock_response(monkeypatch)
    response.raise_for_status.side_effect = error("URL?key=unit-test-secret")
    with pytest.raises(RuntimeError, match="Census population request failed") as caught:
        client.fetch_2020_block_population()
    assert "unit-test-secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_invalid_json_fails_clearly(monkeypatch):
    response, _ = mock_response(monkeypatch)
    response.json.side_effect = ValueError("bad JSON")
    with pytest.raises(ValueError, match="invalid JSON"):
        client.fetch_2020_block_population()


def test_unexpected_city_geography_fails(monkeypatch):
    mock_response(monkeypatch, [
        ["NAME", "B01003_001E", "B01003_001M", "state", "place"],
        ["Other", "1", "1", "53", "99999"],
    ])
    with pytest.raises(ValueError, match="unexpected place"):
        client.fetch_acs_seattle_population()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        client.fetch_2020_block_population(timeout=timeout)


def test_download_county_zip(monkeypatch, tmp_path):
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for suffix in (".shp", ".shx", ".dbf", ".prj"):
            archive.writestr("tl_2020_53033_tabblock20" + suffix, "mock shapefile")
    _, get = mock_response(monkeypatch, content=data.getvalue())
    path = client.download_king_county_2020_block_geometry(tmp_path / "blocks.zip", timeout=31)
    assert path.read_bytes() == data.getvalue()
    get.assert_called_once_with(client.TIGER_BLOCK_URL, stream=True, timeout=31)
    assert not path.with_suffix(".zip.part").exists()


@pytest.mark.parametrize("http_error", [False, True])
def test_download_failure_preserves_existing_file(monkeypatch, tmp_path, http_error):
    path = tmp_path / "blocks.zip"
    path.write_bytes(b"previous archive")
    response, _ = mock_response(monkeypatch, content=b"not a zip")
    if http_error:
        response.raise_for_status.side_effect = requests.HTTPError("503")
    with pytest.raises((ValueError, RuntimeError), match="TIGER"):
        client.download_king_county_2020_block_geometry(path)
    assert path.read_bytes() == b"previous archive"
    assert not path.with_suffix(".zip.part").exists()


def test_zip_cannot_be_saved_in_processed_data():
    with pytest.raises(ValueError, match="temporary directory"):
        client.download_king_county_2020_block_geometry(
            client.PROJECT_ROOT / "data/processed/population/blocks.zip"
        )
