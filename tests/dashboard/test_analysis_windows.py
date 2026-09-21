import pandas as pd
import pytest

from dashboard.analysis_windows import get_analysis_bounds, get_history_bounds, get_previous_period
from scripts.dashboard import refresh_crime_data, refresh_spd_data

@pytest.mark.parametrize("latest,start,days", [
    ("2026-09-06 23:59", "2025-09-06", 366),
    ("2024-02-29", "2023-02-28", 367),
    ("2024-09-06", "2023-09-06", 367),
])
def test_native_year_and_equal_adjacent_periods(latest, start, days):
    current_start, current_end = get_analysis_bounds(latest)
    assert current_start == pd.Timestamp(start)
    assert current_end == pd.Timestamp(latest).normalize()
    previous_start, previous_end = get_previous_period(current_start, current_end)
    current = pd.date_range(current_start, current_end)
    previous = pd.date_range(previous_start, previous_end)
    assert len(current) == len(previous) == days
    assert previous_end + pd.Timedelta(days=1) == current_start
    assert current.intersection(previous).empty
    assert len(current.union(previous)) == len(pd.date_range(previous_start, current_end))
    assert get_previous_period(current_start, current_end,
                               history_bounds=(previous_start, current_end)) == (previous_start, previous_end)
    assert get_previous_period(current_start, current_end,
                               history_bounds=(previous_start + pd.Timedelta(days=1), current_end)) is None


def test_single_day_invalid_and_empty_history():
    assert get_previous_period("2026-09-06", "2026-09-06") == (pd.Timestamp("2026-09-05"),) * 2
    with pytest.raises(ValueError):
        get_previous_period("2026-09-06", "2026-09-05")
    with pytest.raises(ValueError):
        get_history_bounds(pd.Series([None]))
    assert get_history_bounds(pd.Series([None, "2026-09-06 23:59", "2024-01-01 01:00"])) == (
        pd.Timestamp("2024-01-01"), pd.Timestamp("2026-09-06"))
    assert get_previous_period("2026-09-06", "2026-09-06",
                               history_bounds=("2024-01-01", "2026-09-04")) is None


@pytest.mark.parametrize("module", [refresh_crime_data, refresh_spd_data])
@pytest.mark.parametrize("hour", [0, 23])
@pytest.mark.parametrize("lookback", [732, 733, 734, None])
def test_actual_retention_supports_complete_leap_comparison(monkeypatch, tmp_path, module, hour, lookback):
    """Exercise the real incremental filter with no network or generated files.

    Midnight anchoring needs 733; nonmidnight anchoring needs 734 to preserve
    every event on the first previous date. 732 always loses an entire date.
    """
    latest = pd.Timestamp("2024-02-29") + pd.Timedelta(hours=hour)
    current = get_analysis_bounds(latest)
    previous = get_previous_period(*current)
    timestamps = pd.date_range(previous[0] - pd.Timedelta(days=2), latest.normalize()).union(
        pd.DatetimeIndex([latest]))
    crime = module is refresh_crime_data
    time_col = module.EVENT_DATE_COLUMN if crime else module.TIME_COLUMN
    df = pd.DataFrame({time_col: timestamps})
    for key in module.DEDUPLICATION_KEY:
        df[key] = range(len(df))
    if crime:
        df[module.REFRESH_DATE_COLUMN] = timestamps
    prefix = "crime" if crime else "spd_call"
    monkeypatch.setattr(module, f"load_{prefix}_snapshot", lambda *_: (df, {}))
    monkeypatch.setattr(module, f"load_{prefix}_dataset", lambda **_: df.iloc[:0])
    captured = {}
    def save(df, **kwargs):
        captured["df"] = df
        return tmp_path / "snapshot", tmp_path / "metadata"
    monkeypatch.setattr(module, f"save_{prefix}_snapshot", save)
    kwargs = {} if lookback is None else {"rolling_window_days": lookback}
    refresh = getattr(module, f"incremental_refresh_{prefix}_snapshot")
    refresh(output_directory=tmp_path, **kwargs)
    required = df[df[time_col].between(previous[0], current[1])]
    retained = captured["df"]
    complete = set(required[time_col]).issubset(set(retained[time_col]))
    if lookback is None:
        assert complete  # Regression: production default must support both anchors.
    else:
        assert complete == (lookback >= (733 if hour == 0 else 734))
