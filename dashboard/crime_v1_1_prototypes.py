"""Temporary layout prototypes from v1_1_figure_workbook.

Crime count consumes every shared crime filter. The category comparison is
dates-only, as in cell 26. CAD components use dates clamped to their own analysis
domain plus their local priority controls (cells 34/49); crime dimensions do not
map to CAD. Qualification remains owned by calls_context['response_analysis'].
"""

import numpy as np
import pandas as pd
from dash import dcc, html

from dashboard.analysis_windows import get_analysis_bounds, get_history_bounds, get_previous_period
from dashboard.crime_classification import CANONICAL_CRIME_TYPES
from dashboard.crime_controls import validate_analysis_dates
from dashboard.crime_filters import filter_crime_records
from dashboard.uof_dashboard_data import count_uof_incidents, count_ois_events

RESPONSE_PRIORITY_OPTIONS = {
    "Priority 1–3": [1, 2, 3], "Priority 1–2": [1, 2], "Priority 1 only": [1],
}


def get_category_counts(crime, state, categories=CANONICAL_CRIME_TYPES):
    return {category: filter_crime_records(
        crime, {**state, "crime_categories": [category]},
    )["offense_id"].nunique() for category in categories}


def prepare_crime_count(crime, state):
    current = filter_crime_records(crime, state)["offense_id"].nunique()
    previous_period = get_previous_period(
        state["start_date"], state["end_date"],
        history_bounds=get_history_bounds(crime["offense_date"]),
    )
    previous = None
    if previous_period is not None:
        start, end = previous_period
        previous = filter_crime_records(crime, {
            **state, "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
        })["offense_id"].nunique()
    return current, previous, previous_period


def get_citywide_crime_rate(crime, state, city_population):
    """Workbook rate: selected-period distinct offenses / direct city population."""
    selected = filter_crime_records(crime, {**state, "neighborhoods": []})
    if city_population is None or pd.isna(city_population) or city_population <= 0:
        return None
    return selected["offense_id"].nunique() / city_population * 100_000


def render_crime_rate(crime_context, state, mode):
    crime = crime_context["valid_time"]
    population = crime_context["city_population"]
    current = get_citywide_crime_rate(crime, state, population)
    previous, period = None, None
    if current is not None:
        period = get_previous_period(state["start_date"], state["end_date"],
                                     history_bounds=get_history_bounds(crime["offense_date"]))
        if period is not None:
            previous = get_citywide_crime_rate(crime, {
                **state, "start_date": period[0].date().isoformat(),
                "end_date": period[1].date().isoformat(),
            }, population)
    change, style, color = _change(current, previous, mode)
    if previous is not None and mode != "percent":
        change = f"{change[0]} {abs(current - previous):,.1f}"
    if current is None:
        note = "City population unavailable"
    elif period is None:
        note = "Previous period unavailable"
    else:
        note = f"Previous: {_period_label(period)} • {previous:,.1f} per 100k"
    return [html.Div(f"{current:,.1f}" if current is not None else "—", className="crime-v11-kpi-number"),
            html.Div(change if previous is not None else "—", className="crime-v11-change", style={"color": color}),
            html.P("Publicly available reported offenses", className="crime-v11-note crime-v11-subtitle"),
            html.P(note, className="crime-v11-note")], style


def prepare_fixed_context_kpis(crime_context, uof_context):
    """Workbook fixed year: unfiltered crime coverage, never UOF's latest date."""
    latest = get_history_bounds(crime_context["valid_time"]["offense_date"])[1]
    start, end = (day.date().isoformat() for day in get_analysis_bounds(latest))
    return {"start_date": start, "end_date": end,
            "uof": count_uof_incidents(uof_context["df"], start, end),
            "ois": count_ois_events(uof_context["ois_events"], start, end)}


def make_fixed_context_cards(crime_context, uof_context):
    values = prepare_fixed_context_kpis(crime_context, uof_context)
    period = _period_label((values["start_date"], values["end_date"]))
    return [html.Section([
        html.H2(title),
        html.Div(f"{values[key]:,}", id=f"crime-v11-{key}-value", className="crime-v11-kpi-number"),
        html.P("Citywide publicly available reports in the last year", className="crime-v11-note"),
        html.P(period, className="crime-v11-note"),
    ], id=f"crime-v11-{key}-card", className="crime-v11-card crime-v11-context-card")
        for key, title in [("uof", "UOF (Use of Force) Incidents"),
                           ("ois", "OIS (Officer Involved Shooting) Events")]]


def make_crime_rate_card():
    return html.Section([
        html.Div([html.H2("Overall Crime Rate / 100k"),
                  dcc.RadioItems(id="crime-v11-rate-mode", options=[{"label": "Raw", "value": "raw"},
                                                                  {"label": "%", "value": "percent"}],
                                 value="raw", inline=True, className="crime-v11-radio")],
                 className="crime-v11-card-header"),
        html.Div(id="crime-v11-rate-body"),
    ], id="crime-v11-rate-card", className="crime-v11-card crime-v11-rate-card")


def prepare_category_comparison(crime, state):
    # Cell 26 deliberately compares every category across the city, dates only.
    dates_only = {"start_date": state["start_date"], "end_date": state["end_date"],
                  "crime_categories": [], "crime_subcategories": [], "neighborhoods": []}
    period = get_previous_period(
        state["start_date"], state["end_date"],
        history_bounds=get_history_bounds(crime["offense_date"]),
    )
    if period is None:
        return None, None, None
    start, end = period
    previous = {**dates_only, "start_date": start.date().isoformat(), "end_date": end.date().isoformat()}
    return get_category_counts(crime, dates_only), get_category_counts(crime, previous), period


def get_response_kpi_values(response, start_date, end_date, priorities, history_start, history_end):
    """Workbook cell 32: medians of qualified event rows, with equal prior period."""
    response = response.copy()
    response["queued_time"] = pd.to_datetime(response["queued_time"], errors="coerce")
    current = response.loc[
        response["queued_time"].dt.normalize().between(pd.to_datetime(start_date), pd.to_datetime(end_date))
        & response["priority"].isin(priorities)
    ]
    period = get_previous_period(start_date, end_date, history_bounds=(history_start, history_end))
    previous = response.iloc[:0]
    if period is not None:
        previous = response.loc[
            response["queued_time"].dt.normalize().between(*period)
            & response["priority"].isin(priorities)
        ]
    return {"current_median": current["response_time_minutes"].median(), "current_events": len(current),
            "previous_median": previous["response_time_minutes"].median() if len(previous) else np.nan,
            "previous_events": len(previous), "previous_start": period[0] if period else None,
            "previous_end": period[1] if period else None}


def build_neighborhood_response_ranking(response, start_date, end_date, priorities, min_events=1, top_n=10):
    """Workbook cell 44: slowest medians first, then sample size, then name."""
    selected = response.loc[
        response["queued_time"].dt.normalize().between(pd.to_datetime(start_date), pd.to_datetime(end_date))
        & response["priority"].isin(priorities) & response["dispatch_neighborhood"].notna()
    ]
    ranking = selected.groupby("dispatch_neighborhood", as_index=False).agg(
        median_response_minutes=("response_time_minutes", "median"),
        qualified_events=("response_time_minutes", "size"),
    )
    ranking = ranking.loc[ranking["qualified_events"] >= min_events].sort_values(
        ["median_response_minutes", "qualified_events", "dispatch_neighborhood"],
        ascending=[False, False, True],
    ).head(top_n).reset_index(drop=True)
    ranking["rank"] = ranking.index + 1
    return ranking


def prepare_response_period(calls_context, state):
    """Preserve the workbook's independent CAD bounds and expose effective dates."""
    response = calls_context["response_analysis"].copy()
    response["queued_time"] = pd.to_datetime(response["queued_time"], errors="coerce")
    if response["queued_time"].dropna().empty:
        return response, None, None
    history = get_history_bounds(response["queued_time"])
    bounds = get_analysis_bounds(history[1])
    period = validate_analysis_dates(state["start_date"], state["end_date"], *bounds, clamp=True)
    return response, period, history


def _period_label(period):
    start, end = map(pd.Timestamp, period)
    return start.strftime("%b %d, %Y") if start == end else f"{start:%b %d, %Y} – {end:%b %d, %Y}"


def _change(current, previous, mode, *, response=False):
    if previous is None or pd.isna(current) or pd.isna(previous):
        return "→ —", {"borderColor": "#333333"}, "#bbbbbb"
    change = current - previous
    arrow = "↗" if change > 0 else "↘" if change < 0 else "→"
    positive = change < 0 if response else change > 0
    color = "#bbbbbb" if change == 0 else "#22c55e" if positive else "#f97316"
    if mode == "percent":
        number = f"{abs(change / previous * 100):.1f}%" if previous != 0 else "—"
    else:
        number = f"{abs(change):.1f} min" if response else f"{abs(change):,}"
    background = "#181818" if change == 0 else "rgba(34,197,94,0.08)" if positive else "rgba(249,115,22,0.08)"
    return f"{arrow} {number}", {"borderColor": color if change else "#333333", "background": background}, color


def render_crime_count(crime, state, mode):
    current, previous, period = prepare_crime_count(crime, state)
    change, style, color = _change(current, previous, mode)
    note = f"Previous: {_period_label(period)} • {previous:,} offenses" if period else "Previous period unavailable"
    return [html.Div(f"{current:,}", className="crime-v11-kpi-number"),
            html.Div(change if period else "—", className="crime-v11-change", style={"color": color}),
            html.P(note, className="crime-v11-note")], style


def render_category_comparison(crime, state, mode):
    current, previous, period = prepare_category_comparison(crime, state)
    if period is None:
        return html.P("Previous period unavailable", className="crime-v11-note")
    rows = []
    for label in ["Current", "Previous", "Change"]:
        values = []
        for category in CANONICAL_CRIME_TYPES:
            if label == "Change":
                text, _, color = _change(current[category], previous[category], mode)
            else:
                text = f"{(current if label == 'Current' else previous)[category]:,}"
                color = "#ffffff" if label == "Current" else "#888888"
            values.append(html.Td(text, style={"color": color}))
        rows.append(html.Tr([html.Th(label, scope="row"), *values]))
    return [html.Table([
        html.Thead(html.Tr([html.Th("")] + [html.Th(c.title(), scope="col") for c in CANONICAL_CRIME_TYPES])),
        html.Tbody(rows),
    ], className="crime-v11-table crime-v11-category-table"),
        html.P(f"Previous period: {_period_label(period)}", className="crime-v11-note")]


def response_scope_note(period, history):
    if period is None:
        return "No qualified response events available."
    return (f"CAD period: {_period_label(period)}. Available analysis: "
            f"{_period_label(get_analysis_bounds(history[1]))}. Dates are clamped to CAD coverage. "
            "Crime type, subcategory, and neighborhood selections do not filter this component.")


def render_response_kpi(calls_context, state, priority_scope, mode):
    response, period, history = prepare_response_period(calls_context, state)
    if period is None:
        return html.P(response_scope_note(period, history)), {}
    values = get_response_kpi_values(response, *period, RESPONSE_PRIORITY_OPTIONS[priority_scope], *history)
    current, previous = values["current_median"], values["previous_median"]
    change, style, color = _change(current, previous, mode, response=True)
    text = f"{current:.1f} min" if pd.notna(current) else "—"
    if values["previous_start"] is None:
        note = "Previous period unavailable"
    else:
        previous_text = f"{previous:.1f} min" if pd.notna(previous) else "—"
        note = f"Previous: {_period_label((values['previous_start'], values['previous_end']))} • {previous_text}"
    return [html.Div(text, className="crime-v11-kpi-number"),
            html.Div(change, className="crime-v11-change", style={"color": color}),
            html.P(note, className="crime-v11-note"),
            html.P(response_scope_note(period, history), className="crime-v11-note")], style


def render_response_ranking(calls_context, state, priority_scope, min_events):
    response, period, history = prepare_response_period(calls_context, state)
    if period is None:
        return html.P(response_scope_note(period, history))
    minimum = max(1, int(min_events)) if min_events is not None else 1
    ranking = build_neighborhood_response_ranking(
        response, *period, RESPONSE_PRIORITY_OPTIONS[priority_scope], min_events=minimum, top_n=10,
    )
    note = html.P(response_scope_note(period, history), className="crime-v11-note")
    if ranking.empty:
        return [html.P("No qualified response events for this period.", className="crime-v11-note"), note]
    rows = [html.Tr([html.Td(str(r.rank)), html.Td(str(r.dispatch_neighborhood).title()),
                    html.Td(f"{r.median_response_minutes:.1f} min"), html.Td(f"{r.qualified_events:,}")])
            for r in ranking.itertuples()]
    return [html.Table([
        html.Thead(html.Tr([html.Th(s, scope="col") for s in
                           ["Rank", "Neighborhood", "Median Response", "Qualified Events"]])),
        html.Tbody(rows),
    ], className="crime-v11-table"), note]


def make_prototype_cards():
    """Layout-only shells; callbacks fill them from the shared analysis state."""
    def modes(identifier):
        return dcc.RadioItems(id=identifier, options=[{"label": "Raw", "value": "raw"},
                                                     {"label": "%", "value": "percent"}],
                              value="raw", inline=True, className="crime-v11-radio")

    def priorities(identifier):
        return dcc.RadioItems(id=identifier, options=list(RESPONSE_PRIORITY_OPTIONS),
                              value="Priority 1–3", inline=True, className="crime-v11-radio")

    count = html.Section([
        html.Div([html.H2("Overall Crime Count"), modes("crime-v11-count-mode")], className="crime-v11-card-header"),
        html.Div(id="crime-v11-count-body"),
    ], id="crime-v11-count-card", className="crime-v11-card crime-v11-count-card")
    categories = html.Section([
        html.Div([html.H2("Top-Level Crime Categories"), modes("crime-v11-category-mode")], className="crime-v11-card-header"),
        html.P("Citywide comparison • dates only; all crime types, subcategories, and neighborhoods.", className="crime-v11-note"),
        html.Div(id="crime-v11-category-body"),
    ], className="crime-v11-card crime-v11-category-card")
    response = html.Section([
        html.Div([html.H2("Median Qualified Response Time"), modes("crime-v11-response-mode")], className="crime-v11-card-header"),
        html.Label("Qualified Priority Filter"), priorities("crime-v11-response-priority"),
        html.Div(id="crime-v11-response-body"),
    ], id="crime-v11-response-card", className="crime-v11-card crime-v11-response-card")
    ranking = html.Section([
        html.Div([html.H2("Neighborhood Response-Time Ranking")], className="crime-v11-card-header"),
        html.Div([priorities("crime-v11-ranking-priority"), html.Label([
            "Min events ", dcc.Input(id="crime-v11-ranking-min-events", type="number", min=1,
                                      step=1, value=5, debounce=True),
        ])], className="crime-v11-ranking-controls"),
        html.Div(id="crime-v11-ranking-body"),
    ], className="crime-v11-card crime-v11-table-card")
    return count, categories, response, ranking
