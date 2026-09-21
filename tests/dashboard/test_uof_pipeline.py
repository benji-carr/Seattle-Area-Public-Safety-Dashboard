import json
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from dashboard import uof_client as client
from dashboard import uof_service as service
from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_query import UOF_COLUMNS, build_uof_query_params
from dashboard.uof_snapshot import METADATA_FILENAME, load_uof_snapshot, save_uof_snapshot


def test_query_columns_order_and_date_bounds():
    params = build_uof_query_params("2015-01-01", end_date="2026-09-01", limit=50, offset=100)
    assert params == {
        "$select": ",".join(UOF_COLUMNS), "$order": "occured_date_time DESC, uniqueid ASC",
        "$limit": 50, "$offset": 100,
        "$where": "occured_date_time >= '2015-01-01T00:00:00.000' AND occured_date_time < '2026-09-01T00:00:00.000'",
    }
    assert "$where" not in build_uof_query_params()
    assert build_uof_query_params(columns=["uniqueid"])["$select"] == "uniqueid"
    assert build_uof_query_params("2024-01-01", end_date="2024-01-01")
    assert build_uof_query_params(end_date="2024-01-01")["$where"].startswith("occured_date_time <")


@pytest.mark.parametrize("kwargs", [
    {"start_date": "20240901"}, {"start_date": "2024-02-30"}, {"start_date": 2024},
    {"end_date": "bad"}, {"start_date": "2024-09-01", "end_date": "2024-08-31"},
    {"limit": 0}, {"limit": True}, {"limit": 1.2}, {"offset": -1}, {"offset": False},
    {"offset": "1"}, {"columns": []}, {"columns": ["wrong"]}, {"columns": "uniqueid"},
])
def test_query_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        build_uof_query_params(**kwargs)


def test_cleaning_schema_identifiers_dates_and_source_labels():
    rows = [{"uniqueid": " 0001 ", "incident_num": " 00123 ", "officer_id": 9007199254740993,
             "subject_id": " 0007 ", "incident_type": " Level 3 - OIS ",
             "occured_date_time": "2024-04-17T23:59:59.123", "extra": "ignored"},
            {"occured_date_time": "bad"}]
    frame = uof_records_to_dataframe(rows)
    assert list(frame.columns) == UOF_COLUMNS
    assert frame.uniqueid.tolist()[0] == "0001"
    assert frame.incident_num.iloc[0] == "00123"
    assert frame.officer_id.iloc[0] == "9007199254740993"
    assert frame.subject_id.iloc[0] == "0007"
    assert frame.incident_type.iloc[0] == "Level 3 - OIS"
    assert frame.occured_date_time.iloc[0] == pd.Timestamp("2024-04-17T23:59:59.123")
    assert pd.isna(frame.occured_date_time.iloc[1])
    assert frame.precinct.isna().all()
    assert frame.index.equals(pd.RangeIndex(2))
    assert list(uof_records_to_dataframe([]).columns) == UOF_COLUMNS
    assert rows[0]["uniqueid"] == " 0001 "


@pytest.mark.parametrize("records", [{}, None, [1], [{}, []]])
def test_cleaning_rejects_malformed_input(records):
    with pytest.raises(ValueError, match="list of dictionaries"):
        uof_records_to_dataframe(records)


def response(payload=None, status=200):
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(response=result)
    return result


def test_client_passes_query_and_preserves_injected_session():
    session = Mock()
    session.get.return_value = response([{"uniqueid": "1"}])
    assert client.fetch_uof_page("2024-01-01", end_date="2025-01-01", limit=2,
                                 offset=4, timeout=15, session=session) == [{"uniqueid": "1"}]
    session.get.assert_called_once_with(
        "https://data.seattle.gov/resource/ppi5-g2bj.json",
        params=build_uof_query_params("2024-01-01", end_date="2025-01-01", limit=2, offset=4), timeout=15,
    )
    session.close.assert_not_called()


@pytest.mark.parametrize("payload", [{}, None, [1], [{}, "bad"]])
def test_client_rejects_malformed_json(payload):
    session = Mock()
    session.get.return_value = response(payload)
    with pytest.raises(ValueError, match="list of dictionaries"):
        client.fetch_uof_page(session=session)
    assert session.get.call_count == 1


def test_client_retries_transient_http_and_timeout_with_exponential_backoff(monkeypatch):
    session = Mock()
    session.get.side_effect = [response(status=429), requests.Timeout(), response([])]
    sleep = Mock()
    monkeypatch.setattr(client.time, "sleep", sleep)
    callback = Mock()
    assert client.fetch_uof_page(session=session, max_retries=2, retry_backoff_seconds=0.5,
                                 request_callback=callback) == []
    assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]
    assert callback.call_count == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_does_not_retry_nontransient_errors(status):
    session = Mock()
    session.get.return_value = response(status=status)
    with pytest.raises(requests.HTTPError):
        client.fetch_uof_page(session=session, max_retries=3)
    assert session.get.call_count == 1


def test_owned_session_closes_when_retries_exhausted(monkeypatch):
    session = Mock()
    session.get.side_effect = requests.ConnectionError()
    monkeypatch.setattr(client.requests, "Session", lambda: session)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    with pytest.raises(requests.ConnectionError):
        client.fetch_uof_page(max_retries=1)
    assert session.get.call_count == 2
    session.close.assert_called_once_with()


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"timeout": float("nan")},
                                      {"max_retries": True}, {"max_retries": -1},
                                      {"retry_backoff_seconds": -1}])
def test_client_argument_validation(kwargs):
    with pytest.raises(ValueError):
        client.fetch_uof_page(session=Mock(), **kwargs)


def test_latest_record_query_and_validation():
    session = Mock()
    record = {"uniqueid": "1", "occured_date_time": "2026-09-01T12:00:00"}
    session.get.return_value = response([record])
    assert client.fetch_latest_uof_dashboard_record(session=session) == record
    params = session.get.call_args.kwargs["params"]
    assert params["$select"] == "occured_date_time,uniqueid"
    assert params["$order"] == "occured_date_time DESC, uniqueid ASC"
    assert params["$limit"] == 1
    assert "occured_date_time IS NOT NULL" in params["$where"]
    assert "uniqueid IS NOT NULL" in params["$where"]
    for records in ([], [{}], [{"uniqueid": " "}], [{"uniqueid": "1", "occured_date_time": "bad"}]):
        session.get.return_value = response(records)
        with pytest.raises(ValueError, match="valid|missing"):
            client.fetch_latest_uof_dashboard_record(session=session)


@pytest.mark.parametrize("max_pages,expected_pages,expected_rows,exhausted", [
    (None, 3, 5, True), (1, 1, 2, False), (2, 2, 4, False),
])
def test_service_paginates_and_reports_metadata(monkeypatch, max_pages, expected_pages, expected_rows, exhausted):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = [response([{"uniqueid": str(i)} for i in ids])
                               for ids in ([1, 2], [3, 4], [5])]
    monkeypatch.setattr(service.requests, "Session", lambda: session)
    progress = Mock()
    result = service.fetch_uof_dataset(page_size=2, max_pages=max_pages, progress_callback=progress)
    assert len(result["dataframe"]) == expected_rows
    assert result["metadata"] == {
        "request_count": expected_pages, "pages_fetched": expected_pages,
        "row_count": expected_rows, "elapsed_seconds": result["metadata"]["elapsed_seconds"],
        "page_size": 2, "exhausted": exhausted,
    }
    assert result["metadata"]["elapsed_seconds"] >= 0
    assert [call.kwargs["params"]["$offset"] for call in session.get.call_args_list] == list(range(0, expected_pages * 2, 2))
    assert progress.call_args.args[0]["cumulative_rows"] == expected_rows


def test_service_counts_retry_requests_and_stops_on_empty_page(monkeypatch):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = [response(status=503), response([{"uniqueid": "1"}]), response([])]
    monkeypatch.setattr(service.requests, "Session", lambda: session)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    result = service.fetch_uof_dataset(page_size=1, max_retries=1)
    assert result["metadata"]["request_count"] == 3
    assert result["metadata"]["pages_fetched"] == 2
    assert result["metadata"]["exhausted"]


@pytest.mark.parametrize("kwargs", [{"page_size": 0}, {"max_pages": 0}, {"max_pages": True}])
def test_service_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        service.fetch_uof_dataset(**kwargs)


def test_snapshot_round_trip_and_context(tmp_path):
    from dashboard.uof_dashboard_data import load_uof_dashboard_context
    frame = uof_records_to_dataframe([{"uniqueid": "1", "incident_type": "OIS",
                                       "occured_date_time": "2015-08-24", "beat": "K1"}])
    paths = save_uof_snapshot(frame, tmp_path, fetch_metadata={"request_count": 1})
    loaded, metadata = load_uof_snapshot(tmp_path)
    pd.testing.assert_frame_equal(loaded, frame)
    assert [path.name for path in paths] == ["uof_data.parquet", "uof_metadata.json"]
    assert metadata["source_dataset_id"] == "ppi5-g2bj"
    assert metadata["source_start_date"] == metadata["source_end_date"] == "2015-08-24T00:00:00"
    assert metadata["last_fetch"] == {"request_count": 1}
    assert not list(tmp_path.glob("*.tmp"))
    context = load_uof_dashboard_context(tmp_path)
    pd.testing.assert_frame_equal(context["df"], frame)
    assert context["metadata"] == metadata
    assert context["ois_events"].ois_event_key.tolist() == ["2015-08-24|K1"]


@pytest.mark.parametrize("metadata_change,match", [
    ({"row_count": 99}, "row count"), ({"columns": []}, "column mismatch"),
    ({"source_dataset_id": "wrong"}, "source_dataset_id"),
])
def test_snapshot_rejects_inconsistent_metadata(tmp_path, metadata_change, match):
    save_uof_snapshot(uof_records_to_dataframe([{"uniqueid": "1"}]), tmp_path)
    path = tmp_path / METADATA_FILENAME
    metadata = json.loads(path.read_text())
    path.write_text(json.dumps({**metadata, **metadata_change}))
    with pytest.raises(ValueError, match=match):
        load_uof_snapshot(tmp_path)


@pytest.mark.parametrize("metadata,match", [([], "dictionary"), ({}, "required keys")])
def test_snapshot_rejects_malformed_metadata(tmp_path, metadata, match):
    save_uof_snapshot(uof_records_to_dataframe([]), tmp_path)
    (tmp_path / METADATA_FILENAME).write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match=match):
        load_uof_snapshot(tmp_path)


def test_snapshot_rejects_missing_schema_even_when_metadata_agrees(tmp_path):
    frame = uof_records_to_dataframe([{"uniqueid": "1"}])
    snapshot, metadata_path = save_uof_snapshot(frame, tmp_path)
    malformed = frame.drop(columns="incident_num")
    malformed.to_parquet(snapshot, index=False)
    metadata = json.loads(metadata_path.read_text())
    metadata["columns"] = list(malformed.columns)
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="canonical"):
        load_uof_snapshot(tmp_path)


def test_snapshot_failed_serialization_keeps_previous_files(tmp_path, monkeypatch):
    frame = uof_records_to_dataframe([{"uniqueid": "1"}])
    paths = save_uof_snapshot(frame, tmp_path)
    before = [path.read_bytes() for path in paths]
    monkeypatch.setattr(pd.DataFrame, "to_parquet", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        save_uof_snapshot(frame, tmp_path)
    assert [path.read_bytes() for path in paths] == before
    assert not list(tmp_path.glob("*.tmp"))
