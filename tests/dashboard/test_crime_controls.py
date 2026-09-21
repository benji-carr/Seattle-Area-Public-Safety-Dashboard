from datetime import datetime

import pandas as pd
import pytest

from dashboard.crime_controls import (
    crime_chart_dates,
    crime_period_presets,
    format_analysis_date_input,
    format_analysis_period_duration,
    make_analysis_state,
    make_neighborhood_options,
    parse_analysis_date,
)
from dashboard.crime_filters import filter_crime_records
from dashboard.crime_dashboard_figures import prepare_daily_event_data


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2026-02-11", "2026-09-06", "6 months, 3 weeks, and 5 days"),
    ("2024-03-01", "2026-03-01", "2 years"),
    ("2024-02-29", "2025-02-28", "1 year"),
    ("2024-02-28", "2024-03-01", "2 days"),
    ("2026-01-31", "2026-02-28", "1 month"),
    ("2024-01-31", "2024-02-29", "1 month"),
    ("2026-01-31", "2026-03-30", "1 month, 4 weeks, and 2 days"),
    ("2026-01-15", "2026-04-15", "3 months"),
    ("2026-09-01", "2026-09-08", "1 week"),
    ("2026-09-01", "2026-09-22", "3 weeks"),
    ("2026-09-01", "2026-09-02", "1 day"),
    ("2026-09-01", "2026-09-06", "5 days"),
    ("2026-01-01", "2026-02-03", "1 month and 2 days"),
    ("2025-01-01", "2026-03-04", "1 year, 2 months, and 3 days"),
    ("2025-01-01", "2026-01-03", "1 year and 2 days"),
    ("2026-09-06", "2026-09-06", "Same day"),
])
def test_calendar_duration(start, end, expected):
    assert format_analysis_period_duration(start, end) == expected


@pytest.mark.parametrize(("latest", "expected"), [
    ("2026-09-06", "Last 6 months, 3 weeks, and 5 days"),
    ("2026-09-07", "6 months, 3 weeks, and 5 days"),
    (datetime(2026, 9, 6, 23, 59), "Last 6 months, 3 weeks, and 5 days"),
])
def test_duration_uses_latest_dataset_date(latest, expected):
    assert format_analysis_period_duration(
        "2026-02-11 12:30:00", "2026-09-06 01:00:00", latest,
    ) == expected


def test_latest_single_day_and_reversed_dates():
    assert format_analysis_period_duration("2026-09-06", "2026-09-06", "2026-09-06") == "Latest day"
    assert format_analysis_period_duration("2026-09-06", "2026-09-01") == "5 days"


@pytest.mark.parametrize("value", [
    "Feb 11, 2026", "February 11, 2026", "2026-02-11", "2/11/2026",
])
def test_date_text_formats_parse_and_normalize(value):
    assert parse_analysis_date(value) == "2026-02-11"
    assert format_analysis_date_input(value) == "Feb 11, 2026"


@pytest.mark.parametrize("value", [None, "", "not a date", "2026-02-30"])
def test_invalid_date_text_is_rejected(value):
    assert parse_analysis_date(value) is None


def test_native_one_day_chart_range_becomes_latest_single_day():
    dates = crime_chart_dates(
        {"xaxis.range[0]": "2026-09-05", "xaxis.range[1]": "2026-09-06"},
        "2025-09-06",
        "2026-09-06",
    )
    assert dates == ("2026-09-06", "2026-09-06")
    assert format_analysis_period_duration(*dates, "2026-09-06") == "Latest day"


@pytest.mark.parametrize(("start", "end", "expected"), [
    ("2025-09-06", "2026-09-06", "Latest year"),
    ("2026-08-06", "2026-09-06", "Latest month"),
    ("2026-08-30", "2026-09-06", "Latest week"),
    ("2026-09-05", "2026-09-06", "Latest day"),
    ("2024-09-06", "2026-09-06", "Last 2 years"),
    ("2025-09-04", "2026-09-06", "Last 1 year and 2 days"),
])
def test_latest_wording_for_single_units(start, end, expected):
    assert format_analysis_period_duration(start, end, end) == expected


@pytest.mark.parametrize("end", ["2026-03-31", "2024-03-31", "2024-02-29", "2026-05-30", "2026-09-06"])
def test_presets_and_duration_agree_at_calendar_boundaries(end):
    presets = crime_period_presets(end)
    assert (pd.Timestamp(presets["1W"][1]) - pd.Timestamp(presets["1W"][0])).days == 7
    for label, unit in [("1D", "day"), ("1W", "week"), ("1M", "month"), ("1Y", "year")]:
        assert format_analysis_period_duration(*presets[label], end) == f"Latest {unit}"
    if end == "2026-03-31":
        assert presets["1M"] == ["2026-02-28", "2026-03-31"]
    if end == "2024-02-29":
        assert presets["1Y"] == ["2023-02-28", "2024-02-29"]


def test_neighborhood_options_do_not_exclude_citywide_records():
    names = pd.Series(["-", "Unknown", " Unknown ", "uNkNoWn", None, "  - ",
                       "", "Downtown", " downtown "])
    assert make_neighborhood_options(names) == [{"label": "Downtown", "value": "downtown"}]
    records = pd.DataFrame({
        "offense_id": list(range(len(names))), "report_number": list(range(len(names))),
        "offense_date": pd.to_datetime(["2026-09-01"] + ["2026-09-02"] * (len(names) - 1)),
        "mcpp_neighborhood": names,
        "event_importance_bin": ["crimes against property"] * len(names),
    })
    state = make_analysis_state(None, ["crimes against property"], [], [],
                                "2026-09-01", "2026-09-02", ["crimes against property"])
    assert len(filter_crime_records(records, state)) == len(names)
    daily, _ = prepare_daily_event_data({"valid_time": records}, ["crimes against property"], state)
    assert daily.reported_offenses.sum() == len(names)  # Both analysis endpoints are inclusive.
    state["neighborhoods"] = ["downtown"]
    assert filter_crime_records(records, state).offense_id.tolist() == [7, 8]
