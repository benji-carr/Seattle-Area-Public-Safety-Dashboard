"""Crime analysis state and its shared control bar (presentation stays separate)."""

from calendar import monthrange
from datetime import date, timedelta

from dash import dcc, html
import pandas as pd
from dashboard.analysis_windows import bound_analysis_dates, get_analysis_bounds


def make_analysis_state(range_data, categories, subcategories, neighborhoods,
                        default_start, default_end, default_categories):
    range_data = range_data or {}
    start = date.fromisoformat((range_data.get("start") or default_start)[:10])
    end = date.fromisoformat((range_data.get("end") or default_end)[:10])
    start, end = sorted((start, end))
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "crime_categories": list(categories or default_categories),
        "crime_subcategories": list(subcategories or []),
        "neighborhoods": list(neighborhoods or []),
    }


def _calendar_date(value):
    """Normalize ISO dates/timestamps without comparing time of day."""
    return date.fromisoformat(str(value)[:10])


def parse_analysis_date(value):
    """Parse a user-entered date and return an ISO calendar date."""
    if value is None or not str(value).strip():
        return None
    try:
        parsed = pd.to_datetime(str(value).strip(), errors="raise")
    except (TypeError, ValueError, pd.errors.ParserError):
        return None
    if pd.isna(parsed) or not isinstance(parsed, pd.Timestamp):
        return None
    return parsed.date().isoformat()


def format_analysis_date_input(value):
    """Return the compact human-readable text used by the date inputs."""
    parsed = parse_analysis_date(value)
    if parsed is None:
        return ""
    return date.fromisoformat(parsed).strftime("%b %d, %Y")


def validate_analysis_dates(start, end, earliest, latest, *, clamp=False):
    """Return bounded ISO dates, or None while an edit is incomplete/invalid."""
    return bound_analysis_dates(
        parse_analysis_date(start), parse_analysis_date(end), earliest, latest, clamp=clamp,
    )


def crime_chart_dates(relayout, earliest, latest):
    """Translate chart date interactions; ignore presentation-only events."""
    if not relayout:
        return None
    if relayout.get("xaxis.autorange") is True:
        return validate_analysis_dates(earliest, latest, earliest, latest)
    values = relayout.get("xaxis.range")
    uses_native_range_keys = False
    if isinstance(values, (list, tuple)) and len(values) >= 2:
        start, end = values[:2]
    else:
        start, end = relayout.get("xaxis.range[0]"), relayout.get("xaxis.range[1]")
        uses_native_range_keys = start is not None and end is not None
    dates = validate_analysis_dates(start, end, earliest, latest, clamp=True)
    if dates is None:
        return None

    # Plotly's native 1D button emits a 24-hour viewport ending at the latest
    # midnight. Analytical ranges are inclusive calendar dates, so representing
    # both boundaries would incorrectly mean two selected days.
    try:
        start_timestamp = pd.Timestamp(start)
        end_timestamp = pd.Timestamp(end)
    except (TypeError, ValueError):
        return None
    if uses_native_range_keys and end_timestamp - start_timestamp == pd.Timedelta(days=1):
        return dates[1], dates[1]
    return dates


def _add_months(value, months):
    year, month_index = divmod(value.year * 12 + value.month - 1 + months, 12)
    month = month_index + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def crime_period_presets(latest_date):
    """Exact preset viewports ending at the latest available calendar date."""
    end = _calendar_date(latest_date)
    return {
        "1D": [end.isoformat(), end.isoformat() + " 23:59:59.999"],
        "1W": [(end - timedelta(days=7)).isoformat(), end.isoformat()],
        "1M": [_add_months(end, -1).isoformat(), end.isoformat()],
        "1Y": [day.date().isoformat() for day in get_analysis_bounds(end)],
    }


def format_analysis_period_duration(start_date, end_date, latest_date=None):
    """Elapsed calendar years, then months, then weeks/days; not inclusive days.

    Calendar additions clamp the day to the destination month's last day.
    """
    start, end = sorted((_calendar_date(start_date), _calendar_date(end_date)))
    is_latest = latest_date is not None and end == _calendar_date(latest_date)
    if start == end:
        return "Latest day" if is_latest else "Same day"

    years = end.year - start.year
    if _add_months(start, years * 12) > end:
        years -= 1
    cursor = _add_months(start, years * 12)
    months = (end.year - cursor.year) * 12 + end.month - cursor.month
    if _add_months(cursor, months) > end:
        months -= 1
    cursor = _add_months(cursor, months)
    weeks, days = divmod((end - cursor).days, 7)
    # Calendar subtraction is not invertible at short month ends. Recognize
    # exact end-anchored calendar periods as well (e.g. Feb 28 to Mar 31).
    whole_months = (end.year - start.year) * 12 + end.month - start.month
    if whole_months > 0 and _add_months(end, -whole_months) == start:
        years, months = divmod(whole_months, 12)
        weeks = days = 0
    parts = [
        f"{count} {unit}{'s' if count != 1 else ''}"
        for count, unit in [(years, "year"), (months, "month"),
                            (weeks, "week"), (days, "day")]
        if count
    ]
    if len(parts) == 1:
        duration = parts[0]
    elif len(parts) == 2:
        duration = " and ".join(parts)
    else:
        duration = ", ".join(parts[:-1]) + ", and " + parts[-1]
    if is_latest and len(parts) == 1 and parts[0].startswith("1 "):
        return "Latest " + parts[0][2:]
    return ("Last " if is_latest else "") + duration


def format_analysis_period_annotation(state, latest_date):
    duration = format_analysis_period_duration(
        state["start_date"], state["end_date"], latest_date,
    )
    return f"({duration})"


def make_neighborhood_options(values):
    """Remove unavailable choices only; never filter the analytical records."""
    names = sorted({str(value).strip().lower() for value in values.dropna()})
    return [
        {"label": name.title(), "value": name}
        for name in names if name not in {"", "-", "unknown", "nan", "none"}
    ]


def format_analysis_period(state):
    start = date.fromisoformat(state["start_date"]).strftime("%b %d, %Y")
    end = date.fromisoformat(state["end_date"]).strftime("%b %d, %Y")
    return f"{start} to {end}"


def make_analysis_controls(state, category_options, category_value,
                           subcategory_options, neighborhood_options, earliest_date, latest_date):
    def dropdown(label, component_id, options, value, **kwargs):
        return html.Div([
            html.Label(label, htmlFor=component_id),
            dcc.Dropdown(id=component_id, options=options, value=value,
                         className="type-dropdown", **kwargs),
        ], className="crime-analysis-control")

    return html.Div([
        html.Div([
            html.Label("Period of Analysis", id="crime-analysis-period-heading"),
            html.Div([
                html.Span(
                    [
                        html.Span("Start:", className="crime-analysis-date-caption"),
                        dcc.Input(
                            id="crime-analysis-start-date-input", type="text",
                            value=format_analysis_date_input(state["start_date"]),
                            debounce=True, autoComplete="off",
                            className="crime-analysis-date-input",
                        ),
                        html.Span("End:", className="crime-analysis-date-caption"),
                        dcc.Input(
                            id="crime-analysis-end-date-input", type="text",
                            value=format_analysis_date_input(state["end_date"]),
                            debounce=True, autoComplete="off",
                            className="crime-analysis-date-input",
                        ),
                    ],
                    id="crime-analysis-period-label",
                ),
                html.Span(format_analysis_period_annotation(state, latest_date),
                          id="crime-analysis-period-duration"),
            ], className="crime-analysis-period-values"),
        ], className="crime-analysis-period"),
        dropdown("Crime Type", "crime-category-filter", category_options,
                 category_value, multi=True, placeholder="All crime types"),
        dropdown("Crime Subcategory", "crime-subcategory-filter", subcategory_options,
                 [], multi=True, placeholder="All subcategories"),
        dropdown("Neighborhood", "crime-neighborhood-filter", neighborhood_options,
                 [], multi=True, placeholder="All neighborhoods"),
    ], className="crime-analysis-controls")
