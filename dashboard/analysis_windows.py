"""Calendar-date analysis domains, separate from retained comparison history.

Bounds describe coverage, not event frequency: a date with no events is valid.
Callers must supply unfiltered history bounds when checking comparisons.
"""

import pandas as pd


def calendar_day(value):
    """Preserve the source's local calendar date, discarding time and timezone."""
    day = pd.Timestamp(value)
    if pd.isna(day):
        raise ValueError("A valid calendar date is required")
    return day.tz_localize(None).normalize()


def get_history_bounds(dates):
    """Return inclusive day bounds of the full retained, unfiltered dataset."""
    dates = pd.to_datetime(dates, errors="coerce").dropna()
    if len(dates) == 0:
        raise ValueError("No valid dates available")
    return calendar_day(dates.min()), calendar_day(dates.max())


def get_analysis_bounds(latest_date):
    """Native Plotly 1Y: subtract one calendar year, with both ends inclusive."""
    end = calendar_day(latest_date)
    return end - pd.DateOffset(years=1), end


def get_previous_period(current_start, current_end, *, history_bounds=None):
    """Return equal-length adjacent inclusive bounds, or None if unavailable.

    Omit history_bounds only to derive required dates, not to assert coverage.
    When supplied, history_bounds must cover the whole previous period; no
    truncation is permitted. Known acquisition gaps must be checked by callers.
    """
    start, end = calendar_day(current_start), calendar_day(current_end)
    if start > end:
        raise ValueError("current_start must be <= current_end")
    days = (end - start).days + 1
    previous = start - pd.Timedelta(days=days), start - pd.Timedelta(days=1)
    if history_bounds is not None:
        history_start, history_end = map(calendar_day, history_bounds)
        if history_start > previous[0] or history_end < previous[1]:
            return None
    return previous


def bound_analysis_dates(start, end, earliest, latest, *, clamp=False):
    """Return ISO date bounds or None; manual edits reject, chart ranges clamp."""
    try:
        start, end, earliest, latest = map(calendar_day, (start, end, earliest, latest))
    except (TypeError, ValueError):
        return None
    if start > end or earliest > latest:
        return None
    if clamp:
        start = min(max(start, earliest), latest)
        end = min(max(end, earliest), latest)
    if not earliest <= start <= end <= latest:
        return None
    return start.date().isoformat(), end.date().isoformat()


def daily_chart_window(data):
    """Shared presentation domain; never exposes comparison-only history."""
    history_start, history_end = get_history_bounds(data["date"])
    start, end = get_analysis_bounds(history_end)
    return {
        "earliest_available_day": history_start,
        "latest_available_day": history_end,
        "earliest_analysis_day": start,
        "plot_start_day": start,
        "plot_end_day": end,
        "initial_view_start": end - pd.Timedelta(days=1),
    }


def chart_range_needs_correction(relayout, analysis_start, analysis_end):
    """Detect explicit relayout ranges that cannot be used as analysis dates.

    Plotly's native axis limits constrain gestures, but callers of its public
    relayout API can still supply an explicit range beyond those limits.
    """
    relayout = relayout or {}
    values = relayout.get("xaxis.range")
    if not isinstance(values, (list, tuple)) or len(values) < 2:
        values = [relayout.get("xaxis.range[0]"), relayout.get("xaxis.range[1]")]
    if any(value is None for value in values[:2]):
        return False
    return bound_analysis_dates(*values[:2], analysis_start, analysis_end) is None


def chart_presentation_range(start, end, analysis_start):
    """Give single-day selections a nonzero viewport within the allowed domain."""
    start, end = calendar_day(start), calendar_day(end)
    if start == end:
        if start == calendar_day(analysis_start):
            return [start, end + pd.Timedelta(hours=23, minutes=59, seconds=59)]
        return [start - pd.Timedelta(days=1), end]
    return [start.date().isoformat(), end.date().isoformat()]
