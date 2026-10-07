import logging
from math import ceil

import pandas as pd
import pytest
import requests

from dashboard.spd_client import fetch_spd_call_page
from forecasting.production.data_refresh import (
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_READ_TIMEOUT_SECONDS,
    EVENT_LEVEL_PAGINATION_STRATEGY,
    FULL_REFRESH_MODE,
    SMOKE_REFRESH_MODE,
    build_target_panel,
    refresh_production_data,
    run_connectivity_check,
)


def make_source_frame(days: int = 40, neighborhoods: tuple[str, ...] = ("a", "b", "c")) -> pd.DataFrame:
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day_offset, target_date in enumerate(pd.date_range(start, periods=days, freq="D")):
        for index, neighborhood in enumerate(neighborhoods):
            call_count = (day_offset + index) % 4
            for call_number in range(call_count):
                rows.append(
                    {
                        "cad_event_number": f"{target_date.date()}-{neighborhood}-{call_number}",
                        "cad_event_original_time_queued": f"{target_date.date()}T18:00:00Z",
                        "dispatch_neighborhood": neighborhood,
                        "unused_column": f"noise-{call_number}",
                    }
                )
    rows.extend(
        [
            {
                "cad_event_number": "dup-1",
                "cad_event_original_time_queued": "2024-01-10T18:00:00Z",
                "dispatch_neighborhood": "a",
                "unused_column": "first",
            },
            {
                "cad_event_number": "dup-1",
                "cad_event_original_time_queued": "2024-01-10T18:00:00Z",
                "dispatch_neighborhood": "a",
                "unused_column": "second",
            },
        ]
    )
    return pd.DataFrame(rows)


def fake_fetch_factory(source: pd.DataFrame):
    def _fetch_source(
        *,
        start_date,
        end_date=None,
        page_size,
        max_pages,
        timeout,
        columns,
        order,
        max_retries,
        retry_backoff_seconds,
        progress_callback,
    ):
        frame = source.copy()
        timestamps = pd.to_datetime(frame["cad_event_original_time_queued"], utc=True)
        if start_date is not None:
            frame = frame.loc[timestamps >= pd.Timestamp(f"{start_date}T00:00:00Z")]
            timestamps = timestamps.loc[frame.index]
        if end_date is not None:
            frame = frame.loc[timestamps < pd.Timestamp(f"{end_date}T00:00:00Z")]
        frame = frame.loc[:, list(columns)].reset_index(drop=True)
        request_count = max(1, ceil(len(frame) / page_size)) if len(frame) else 1
        for page_number in range(1, request_count + 1):
            start = (page_number - 1) * page_size
            stop = start + page_size
            page = frame.iloc[start:stop]
            progress_callback(
                {
                    "page_number": page_number,
                    "rows_fetched_this_page": int(len(page)),
                    "cumulative_rows": int(min(stop, len(frame))),
                    "offset": start,
                    "elapsed_seconds": 0.01 * page_number,
                    "page_elapsed_seconds": 0.005,
                }
            )
        return {
            "dataframe": frame,
            "metadata": {
                "request_count": request_count,
                "pages_fetched": request_count,
                "row_count": int(len(frame)),
                "elapsed_seconds": 0.123,
            },
        }

    return _fetch_source


def test_connectivity_mode_does_not_write_production_data(monkeypatch, tmp_path):
    panel_path = tmp_path / "target.parquet"
    monkeypatch.setattr(
        "forecasting.production.data_refresh.fetch_spd_call_page",
        lambda **kwargs: [{"cad_event_number": "1", "cad_event_original_time_queued": "2024-01-01T18:00:00Z", "dispatch_neighborhood": "a"}],
    )
    summary = run_connectivity_check()
    assert summary["refresh_mode"] == "connectivity_check"
    assert summary["target_rows_written"] == 0
    assert not panel_path.exists()


def test_smoke_mode_does_not_overwrite_target_panel(tmp_path):
    source = make_source_frame(days=10, neighborhoods=("a", "b"))
    existing = pd.DataFrame(
        [
            {"target_date": pd.Timestamp("2020-01-01"), "neighborhood": "a", "calls": 99.0},
            {"target_date": pd.Timestamp("2020-01-01"), "neighborhood": "b", "calls": 88.0},
        ]
    )
    panel_path = tmp_path / "target.parquet"
    existing.to_parquet(panel_path, index=False)

    result = refresh_production_data(
        expected_neighborhoods=["A", "B"],
        target_panel_path=panel_path,
        fetch_source=fake_fetch_factory(source),
        refresh_mode=SMOKE_REFRESH_MODE,
        start_date="2024-01-04",
        end_date="2024-01-11",
        page_size=3,
        now=pd.Timestamp("2024-01-12").to_pydatetime(),
    )

    assert result["summary"]["target_panel_written"] is False
    persisted = pd.read_parquet(panel_path)
    pd.testing.assert_frame_equal(persisted, existing)


def test_full_mode_retains_five_year_semantics(tmp_path):
    source = make_source_frame(days=2200, neighborhoods=("a", "b"))
    result = refresh_production_data(
        expected_neighborhoods=["A", "B"],
        target_panel_path=tmp_path / "target.parquet",
        fetch_source=fake_fetch_factory(source),
        refresh_mode=FULL_REFRESH_MODE,
        write_target_panel=False,
        page_size=500,
        now=pd.Timestamp("2029-05-28").to_pydatetime(),
    )

    panel = result["target_panel"]
    assert panel["target_date"].min() == pd.Timestamp("2024-05-27")
    assert panel["target_date"].max() == pd.Timestamp("2029-05-27")
    assert result["summary"]["n_target_dates"] >= 365 * 5


def test_reduced_column_refresh_matches_trusted_target_construction(tmp_path):
    source = make_source_frame(days=45, neighborhoods=("a", "b"))
    expected = build_target_panel(
        source,
        expected_neighborhoods=["A", "B"],
        selected_complete_through_date="2024-02-14",
        start_date="2024-01-10",
    )
    result = refresh_production_data(
        expected_neighborhoods=["A", "B"],
        target_panel_path=tmp_path / "target.parquet",
        fetch_source=fake_fetch_factory(source),
        refresh_mode=SMOKE_REFRESH_MODE,
        start_date="2024-01-10",
        end_date="2024-02-15",
        page_size=4,
        now=pd.Timestamp("2024-02-16").to_pydatetime(),
    )
    pd.testing.assert_frame_equal(result["target_panel"], expected)


def test_duplicate_cad_event_counts_once_and_zero_grid_rows_preserved(tmp_path):
    source = make_source_frame(days=14, neighborhoods=("a", "b"))
    result = refresh_production_data(
        expected_neighborhoods=["A", "B", "C"],
        target_panel_path=tmp_path / "target.parquet",
        fetch_source=fake_fetch_factory(source),
        refresh_mode=SMOKE_REFRESH_MODE,
        start_date="2024-01-08",
        end_date="2024-01-15",
        page_size=10,
        now=pd.Timestamp("2024-01-16").to_pydatetime(),
    )
    panel = result["target_panel"]
    duplicate_day = panel.loc[
        (panel["target_date"] == pd.Timestamp("2024-01-10"))
        & (panel["neighborhood"] == "A"),
        "calls",
    ].iloc[0]
    zero_row = panel.loc[
        (panel["target_date"] == pd.Timestamp("2024-01-08"))
        & (panel["neighborhood"] == "C"),
        "calls",
    ].iloc[0]
    assert duplicate_day == 2.0
    assert zero_row == 0.0


def test_refresh_records_timeout_strategy_and_progress_logs(tmp_path, caplog):
    source = make_source_frame(days=12, neighborhoods=("a", "b"))
    with caplog.at_level(logging.INFO):
        result = refresh_production_data(
            expected_neighborhoods=["A", "B"],
            target_panel_path=tmp_path / "target.parquet",
            fetch_source=fake_fetch_factory(source),
            refresh_mode=SMOKE_REFRESH_MODE,
            start_date="2024-01-03",
            end_date="2024-01-10",
            page_size=2,
            connect_timeout=7.0,
            read_timeout=13.0,
            max_retries=4,
            retry_backoff_seconds=0.25,
            now=pd.Timestamp("2024-01-11").to_pydatetime(),
        )

    summary = result["summary"]
    assert summary["request_timeout_seconds"] == {"connect_timeout": 7.0, "read_timeout": 13.0}
    assert summary["retry_policy"] == {"max_retries": 4, "retry_backoff_seconds": 0.25}
    assert summary["api_elapsed_seconds"] >= 0.0
    assert summary["aggregation_elapsed_seconds"] >= 0.0
    assert summary["validation_elapsed_seconds"] >= 0.0
    assert summary["refresh_strategy"] == EVENT_LEVEL_PAGINATION_STRATEGY
    assert summary["excluded_source_neighborhoods"] == []
    assert any("SPD refresh fetch alive" in message for message in caplog.messages)


def test_fetch_spd_call_page_retries_are_bounded():
    attempts = []

    class FakeSession:
        def get(self, *args, **kwargs):
            attempts.append(kwargs["params"]["$offset"])
            raise requests.Timeout("timed out")

    with pytest.raises(requests.Timeout):
        fetch_spd_call_page(
            start_date="2024-01-01",
            limit=5,
            offset=0,
            timeout=(DEFAULT_CONNECT_TIMEOUT_SECONDS, DEFAULT_READ_TIMEOUT_SECONDS),
            max_retries=2,
            retry_backoff_seconds=0.0,
            session=FakeSession(),
        )

    assert len(attempts) == 3


def test_source_validation_preserves_duplicates_extra_columns_and_filters_invalid_ids():
    from forecasting.production.data_refresh import validate_source_schema

    source = pd.DataFrame({
        "cad_event_number": ["a", "a", None],
        "cad_event_original_time_queued": ["2026-01-01"] * 3,
        "dispatch_neighborhood": [" north "] * 3,
        "extra": [1, 2, 3],
    })
    result = validate_source_schema(source)
    assert result.cad_event_number.tolist() == ["a", "a"]
    assert result.dispatch_neighborhood.tolist() == ["NORTH", "NORTH"]
    assert result.extra.tolist() == [1, 2]
    with pytest.raises(ValueError, match="required columns"):
        validate_source_schema(source.drop(columns="cad_event_number"))
    source.loc[2, "cad_event_original_time_queued"] = "bad"
    with pytest.raises(ValueError, match="invalid queued timestamps"):
        validate_source_schema(source)  # Dates were checked even on rows later filtered out.
