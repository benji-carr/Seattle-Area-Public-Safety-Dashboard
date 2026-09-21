"""Feature acceptance using real figure preparation and synthetic full history."""
import pandas as pd
import pytest

from dashboard import crime_dashboard_figures as crime, spd_dashboard_figures as calls
from dashboard.analysis_windows import get_analysis_bounds, get_history_bounds, get_previous_period
from dashboard.crime_filters import filter_crime_records


@pytest.mark.parametrize("module,category,metric", [
    (crime, "crimes against property", "reported_offenses"),
    (calls, "property/nonviolent", "unique_call_events"),
])
def test_native_year_hides_history_without_losing_equal_previous_period(module, category, metric):
    start, end = get_analysis_bounds("2024-02-29")
    previous_start, previous_end = get_previous_period(start, end)
    dates = pd.date_range(previous_start, end)
    records = pd.DataFrame({
        module.TIME_COLUMN: dates,
        module.EVENT_ID_COLUMN: range(len(dates)), module.ROW_ID_COLUMN: range(len(dates)),
        "event_importance_bin": category, "offense_sub_category": "theft",
        "mcpp_neighborhood": "downtown",
    })
    # Identical categorical filters must exclude these records in BOTH periods.
    excluded = records.assign(event_importance_bin="excluded", **{module.EVENT_ID_COLUMN: range(1000, 1000 + len(dates))})
    records = pd.concat([records, excluded], ignore_index=True)
    original = records.copy(deep=True)
    context = {"valid_time": records}
    daily, window = module.prepare_daily_event_data(context, [category])
    assert len(daily) == 367
    assert daily[metric].sum() == 367
    assert daily.date.min() == start and daily.date.max() == end
    assert window["earliest_available_day"] == previous_start
    figure = module.make_daily_figure(context, [category])
    for trace in figure.data:
        assert min(trace.x) == start and max(trace.x) == end
    axis = figure.layout.xaxis
    button = axis.rangeselector.buttons[-1]
    assert (button.count, button.step, button.stepmode) == (1, "year", "backward")
    assert tuple(axis.rangeslider.range) == (start, end)
    assert axis.rangeslider.autorange is False
    assert (axis.minallowed, axis.maxallowed) == (start, end)
    assert (axis.autorangeoptions.minallowed, axis.autorangeoptions.maxallowed) == (start, end)
    history = get_history_bounds(context["valid_time"][module.TIME_COLUMN])
    assert get_previous_period(start, end, history_bounds=history) == (previous_start, previous_end)
    assert get_previous_period(start, end, history_bounds=(start, end)) is None
    if module is crime:
        state = {"crime_categories": [category], "crime_subcategories": ["theft"], "neighborhoods": ["downtown"]}
        def select(a, b):
            return filter_crime_records(records, {**state, "start_date": a, "end_date": b})
    else:
        def select(a, b):
            return records[records[module.TIME_COLUMN].between(a, b) & records.event_importance_bin.isin([category])]
    current, previous = select(start, end), select(previous_start, previous_end)
    assert len(current) == len(previous) == 367
    assert set(current[module.EVENT_ID_COLUMN]).isdisjoint(previous[module.EVENT_ID_COLUMN])
    pd.testing.assert_frame_equal(context["valid_time"], original)


def test_calls_scatter_uses_analysis_year_and_keeps_response_history():
    records = pd.DataFrame({
        "cad_event_number": [1, 2, 3],
        "queued_time": pd.to_datetime(["2024-01-01", "2025-09-06", "2026-09-06"]),
        "dispatch_neighborhood": "downtown", "event_importance_bin": "property/nonviolent",
        "response_time_minutes": [1000, 10, 20],
    })
    context = {
        "valid_time": records.rename(columns={"queued_time": calls.TIME_COLUMN}),
        "response_analysis": records,
        "neighborhood_population": pd.DataFrame({"dispatch_neighborhood": ["downtown"], "population": [1000]}),
        "years_observed": 2.0,
    }
    result = calls.prepare_volume_response_scatter_data(context, ["property/nonviolent"], min_events=1)
    assert result.unique_call_events.tolist() == [2]
    assert result.median_response_minutes.tolist() == [15]
    assert len(context["response_analysis"]) == 3
