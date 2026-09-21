from functools import lru_cache
from copy import deepcopy
import json
import os

import pandas as pd
from dash import Dash, ClientsideFunction, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import MissingCallbackContextException, PreventUpdate

from dashboard.analysis_windows import (
    get_history_bounds, get_analysis_bounds, chart_presentation_range,
    chart_range_needs_correction,
)


from dashboard.crime_call_support_data import load_crime_call_support_context

from dashboard.crime_dashboard_data import (
    TIME_COLUMN as CRIME_TIME_COLUMN,
    load_crime_dashboard_context,
)

from dashboard.crime_controls import (
    make_analysis_state, make_analysis_controls, format_analysis_date_input,
    format_analysis_period_annotation, make_neighborhood_options,
    validate_analysis_dates, crime_chart_dates,
)

from dashboard.crime_classification import CANONICAL_CRIME_TYPES as TARGET_CRIME_CATEGORIES
from dashboard import crime_v1_1_prototypes as crime_components
from dashboard.uof_dashboard_data import load_uof_dashboard_context

from dashboard.crime_dashboard_figures import (
    make_daily_figure as make_crime_daily_figure,
    make_map_figure as make_crime_map_figure,
    get_canonical_mcpp_names,
)

APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

PANEL_STYLE = {
    "height": "100%",
    "width": "100%",
    "minHeight": "0",
    "minWidth": "0",
    "border": "1px solid #333333",
    "borderRadius": "8px",
    "overflow": "hidden",
    "backgroundColor": "#111111",
    "boxSizing": "border-box",
}

GRAPH_STYLE = {
    "height": "100%",
    "width": "100%",
}

LOADING_STYLE = {
    "height": "100%",
    "width": "100%",
}


def make_crime_map_mount(figure, layer_mode):
    return html.Div(
        children=[dcc.Graph(
            id="crime-map-figure", figure=figure, className="map-graph",
            config={"responsive": True, "displaylogo": False},
            responsive=True, style=GRAPH_STYLE,
        )],
        key=f"crime-map-mount-{layer_mode}",
        style={"height": "100%", "width": "100%"},
    )


def make_environment_banner():
    if APP_ENV != "staging":
        return None

    return html.Div(
        "STAGING ENVIRONMENT",
        className="staging-banner",
    )


def get_default_map_date_range(
    context: dict,
    time_column: str,
) -> tuple[str, str]:
    valid_time = context["valid_time"].copy()

    valid_time[time_column] = pd.to_datetime(
        valid_time[time_column],
        errors="coerce",
    )

    latest_day = valid_time[time_column].dropna().max().normalize()
    start_day = latest_day - pd.Timedelta(days=6)

    return start_day.date().isoformat(), latest_day.date().isoformat()


def count_map_points(fig) -> int:
    point_count = 0

    for trace in fig.data:
        trace_type = str(getattr(trace, "type", "")).lower()

        if trace_type in ["scattermapbox", "scattermap"]:
            lat_values = getattr(trace, "lat", None)

            if lat_values is not None:
                point_count += len(lat_values)

    return point_count


def create_app() -> Dash:
    app = Dash(
        __name__,
        suppress_callback_exceptions=True,
    )

    calls_context = load_crime_call_support_context()
    crime_context = load_crime_dashboard_context()
    crime_history_start, crime_history_end = get_history_bounds(crime_context["valid_time"][CRIME_TIME_COLUMN])

    def make_options_from_series(series: pd.Series) -> list[dict[str, str]]:
        values = (
            series
            .dropna()
            .astype("string")
            .str.strip()
            .sort_values()
            .unique()
            .tolist()
        )

        return [
            {
                "label": value.title(),
                "value": value,
            }
            for value in values
            if value not in ["", "nan", "none"]
        ]


    crime_subcategory_options = make_options_from_series(
        crime_context["valid_time"]["offense_sub_category"]
    )

    crime_neighborhood_options = make_neighborhood_options(
        pd.Series(sorted(get_canonical_mcpp_names(crime_context)))
    )


    crime_category_options = [
        {"label": category.title(), "value": category}
        for category in TARGET_CRIME_CATEGORIES
    ]
    default_crime_category_value = list(TARGET_CRIME_CATEGORIES)

    default_crime_start, default_crime_end = get_default_map_date_range(
        crime_context,
        CRIME_TIME_COLUMN,
    )

    crime_analysis_start, crime_analysis_end = (
        day.date().isoformat() for day in get_analysis_bounds(crime_history_end)
    )


    default_crime_analysis_state = make_analysis_state(
        None, default_crime_category_value, [], [],
        default_crime_start, default_crime_end, TARGET_CRIME_CATEGORIES,
    )

    def bounded_crime_state(state):
        state = state or default_crime_analysis_state
        dates = validate_analysis_dates(
            state.get("start_date"), state.get("end_date"),
            crime_analysis_start, crime_analysis_end, clamp=True,
        ) or (default_crime_start, default_crime_end)
        return {**state, "start_date": dates[0], "end_date": dates[1]}

    @lru_cache(maxsize=64)
    def cached_crime_daily_figure(analysis_key, show_legend):
        analysis_state = bounded_crime_state(json.loads(analysis_key))
        fig = make_crime_daily_figure(
            context=crime_context,
            selected_bins=analysis_state["crime_categories"],
            analysis_state=analysis_state,
        )
        start, end = analysis_state["start_date"], analysis_state["end_date"]
        presentation_range = chart_presentation_range(start, end, crime_analysis_start)
        if start == end:
            end += " 23:59:59.999"
        fig.update_layout(showlegend=show_legend, autosize=True,
                          uirevision=f"crime-analysis-{start}-{end}",
                          xaxis_range=presentation_range)
        return fig

    def build_crime_map_figure(analysis_state, show_colorbar, text_filter, metric_mode, layer_mode):
        analysis_state = bounded_crime_state(analysis_state)
        fig = make_crime_map_figure(
            context=crime_context,
            selected_bins=analysis_state["crime_categories"],
            point_start_date=analysis_state["start_date"],
            point_end_date=analysis_state["end_date"],
            show_colorbar=show_colorbar,
            point_filters={"text": text_filter or ""},
            analysis_state=analysis_state,
            metric_mode=metric_mode,
            layer_mode=layer_mode,
        )
        fig.update_layout(autosize=True)
        return fig, count_map_points(fig)


    def make_crime_map_panel():
        map_graph = html.Div(
            make_crime_map_mount(figure={}, layer_mode="choropleth"),
            id="crime-map-mount-host", style=GRAPH_STYLE,
        )
        return html.Div(
            children=[
                html.Button(
                    "↗",
                    id="crime-expand-map-button",
                    className="expand-button",
                    title="Expand crime map",
                ),

                html.Div([
                    html.Div("Crime Geography", className="crime-map-heading"),
                    dcc.RadioItems(
                        id="crime-map-metric",
                        options=[{"label": "Raw", "value": "raw"},
                                 {"label": "Rate /100k", "value": "rate"}],
                        value="raw", inline=True,
                        className="crime-map-radio",
                    ),
                    dcc.RadioItems(
                        id="crime-map-layer",
                        options=[{"label": "Neighborhoods", "value": "choropleth"},
                                 {"label": "Points", "value": "points"},
                                 {"label": "Both", "value": "both"}],
                        value="choropleth", inline=True,
                        className="crime-map-radio",
                    ),
                ], className="crime-map-controls"),
                html.Div(
                    dcc.Loading(
                        map_graph,
                        type="default", style=LOADING_STYLE,
                        parent_style=LOADING_STYLE,
                    ),
                    id="crime-map-graph-container",
                    className="crime-map-graph-container",
                ),
            ],
            className="map-panel dashboard-panel crime-map-panel",
            style={
                **PANEL_STYLE,
                "gridColumn": "1",
                "gridRow": "1",
            },
        )

    def make_crime_daily_panel():
        return html.Div(
            children=[
                html.Button(
                    "↗",
                    id="crime-expand-daily-button",
                    className="expand-button",
                    title="Expand crime daily chart",
                ),

                dcc.Loading(
                    children=[
                        dcc.Graph(
                            id="crime-daily-figure",
                            config={"responsive": True},
                            style=GRAPH_STYLE,
                        )
                    ],
                    type="default",
                    style=LOADING_STYLE,
                    parent_style=LOADING_STYLE,
                ),
            ],
            className="daily-panel dashboard-panel",
            style={
                **PANEL_STYLE,
                "gridColumn": "2",
                "gridRow": "1",
            },
        )

    def make_crime_display_controls():
        return html.Details(
            children=[
                html.Summary("Controls"),

                html.Div(
                    children=[
                        html.P(
                            "Map point legend is always visible.",
                            style={
                                "margin": "0 0 8px 0",
                                "fontSize": "11px",
                                "lineHeight": "14px",
                                "color": "#bbbbbb",
                            },
                        ),

                        dcc.Checklist(
                            id="crime-legend-toggle",
                            options=[
                                {
                                    "label": " Map color scale",
                                    "value": "map_colorbar",
                                },
                                {
                                    "label": " Daily legend",
                                    "value": "daily",
                                },
                            ],
                            value=[],
                            className="control-sidebar",
                            style={
                                "fontSize": "12px",
                                "lineHeight": "1.8",
                                "marginBottom": "10px",
                            },
                        ),

                        html.Hr(
                            style={
                                "borderColor": "rgba(255,255,255,0.18)",
                                "margin": "8px 0",
                            },
                        ),

                        html.P(
                            "Point filters",
                            style={
                                "margin": "8px 0 6px 0",
                                "fontSize": "12px",
                                "fontWeight": "bold",
                                "color": "#dddddd",
                            },
                        ),

                        html.Label(
                            "Text search",
                            style={
                                "fontSize": "11px",
                                "color": "#bbbbbb",
                            },
                        ),

                        dcc.Input(
                            id="crime-point-text-filter",
                            type="text",
                            debounce=True,
                            placeholder="Search ID, report #, block...",
                            style={
                                "width": "100%",
                                "fontSize": "12px",
                                "padding": "5px",
                                "boxSizing": "border-box",
                            },
                        ),
                    ],
                    className="control-sidebar",
                ),
            ],
            className="floating-control-panel",
            style={
                "width": "330px",
            },
        )

    def make_crime_fullscreen_overlay():
        return html.Div(
            children=[
                html.Div(
                    children=[
                        html.Div(
                            id="crime-fullscreen-title",
                            className="fullscreen-title",
                        ),
                        html.Button(
                            "×",
                            id="crime-close-fullscreen-button",
                            className="close-fullscreen-button",
                            title="Close fullscreen view",
                        ),
                    ],
                    className="fullscreen-header",
                ),

                dcc.Graph(
                    id="crime-fullscreen-figure",
                    className="fullscreen-graph",
                    responsive=True,
                    config={"responsive": True},
                    style={
                        "height": "100%",
                        "width": "100%",
                    },
                ),
            ],
            id="crime-fullscreen-overlay",
            className="fullscreen-overlay hidden",
        )

    @lru_cache(maxsize=1)
    def crime_fixed_context_cards():
        # Fixed citywide context is independent of the selected filters.
        return crime_components.make_fixed_context_cards(crime_context, load_uof_dashboard_context())

    def make_crime_page():
        # Layer changes remount the map to reset Plotly interaction state.
        map_panel = make_crime_map_panel()
        map_panel.className = "dashboard-panel crime-map-panel crime-v11-card crime-v11-map-card"
        map_panel.style = {}
        daily_panel = make_crime_daily_panel()
        daily_panel.className = "dashboard-panel crime-v11-card crime-v11-daily-card"
        daily_panel.style = {}
        display_controls = make_crime_display_controls()
        display_controls.className = "crime-v11-display-controls"
        display_controls.style = {}
        display_controls.open = False
        count, categories, response, ranking = crime_components.make_prototype_cards()
        return html.Div([
            html.Main([
                dcc.Store(id="crime-daily-relayout-debounced-store", data=None),
                dcc.Store(id="crime-analysis-state-store", data=default_crime_analysis_state),
                dcc.Store(id="crime-fullscreen-figure-store", data=None),
                dcc.Store(id="crime-map-region-toggle", data=None),
                html.Div(id="crime-map-listener-anchor", style={"display": "none"}),
                html.Header([
                    html.H1("Seattle Crime Dashboard"),
                    html.Div(id="crime-map-point-window-label"),
                ], className="crime-v11-header"),
                make_analysis_controls(
                    default_crime_analysis_state, crime_category_options,
                    default_crime_category_value, crime_subcategory_options,
                    crime_neighborhood_options, crime_analysis_start, crime_analysis_end,
                ),
                display_controls,
                html.Div([count, crime_components.make_crime_rate_card(), categories], className="crime-v11-kpi-grid"),
                html.Div([html.Div([map_panel, daily_panel], className="crime-v11-figure-pair"),
                          html.Div([ranking, html.Div([
                              response,
                              html.Div(crime_fixed_context_cards(), className="crime-v11-context-grid"),
                          ], className="crime-v11-response-kpis")], className="crime-v11-response-region")],
                         className="crime-v11-primary-grid"),
                make_crime_fullscreen_overlay(),
            ], className="crime-v11-content"),
        ], className="crime-v11-page")

    app.layout = html.Div(
        children=[
            dcc.Location(id="url"),
            make_environment_banner(),
            html.Div(id="page-content"),
        ],
        className="site-shell",
    )

    @app.callback(
        Output("page-content", "children"),
        Input("url", "pathname"),
    )
    def display_page(pathname: str):
        # /, /crime-v1-1, and /calls share the canonical /crime implementation.
        return make_crime_page()

    app.clientside_callback(
        ClientsideFunction(namespace="range_debounce", function_name="crime"),
        Output("crime-daily-relayout-debounced-store", "data"),
        Input("crime-daily-figure", "relayoutData"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("crime-analysis-start-date-input", "value"),
        Output("crime-analysis-end-date-input", "value"),
        Input("crime-daily-relayout-debounced-store", "data"),
        Input("crime-analysis-start-date-input", "n_submit"),
        Input("crime-analysis-start-date-input", "n_blur"),
        Input("crime-analysis-end-date-input", "n_submit"),
        Input("crime-analysis-end-date-input", "n_blur"),
        State("crime-analysis-start-date-input", "value"),
        State("crime-analysis-end-date-input", "value"),
        State("crime-analysis-state-store", "data"),
        prevent_initial_call=True,
    )
    def update_crime_date_inputs(
        relayout_data, start_submit, start_blur, end_submit, end_blur,
        current_start, current_end, analysis_state=None,
    ):
        try:
            triggered_id = ctx.triggered_id
        except MissingCallbackContextException:
            # Direct callback unit tests do not have Dash callback context.
            triggered_id = "crime-daily-relayout-debounced-store" if relayout_data else "crime-analysis-start-date-input"

        if triggered_id == "crime-daily-relayout-debounced-store":
            dates = crime_chart_dates(relayout_data, crime_analysis_start, crime_analysis_end)
            current = validate_analysis_dates(
                current_start, current_end, crime_analysis_start, crime_analysis_end,
            )
            # Ignore figure redraws that echo the current selection.
            if dates is None or dates == current:
                raise PreventUpdate
            return tuple(format_analysis_date_input(value) for value in dates)

        previous = analysis_state or default_crime_analysis_state
        previous_dates = validate_analysis_dates(
            previous.get("start_date"), previous.get("end_date"),
            crime_analysis_start, crime_analysis_end,
        ) or (default_crime_start, default_crime_end)

        if triggered_id == "crime-analysis-start-date-input":
            dates = validate_analysis_dates(
                current_start, previous_dates[1], crime_analysis_start, crime_analysis_end,
            )
            normalized = dates[0] if dates else previous_dates[0]
            return format_analysis_date_input(normalized), no_update

        if triggered_id == "crime-analysis-end-date-input":
            dates = validate_analysis_dates(
                previous_dates[0], current_end, crime_analysis_start, crime_analysis_end,
            )
            normalized = dates[1] if dates else previous_dates[1]
            return no_update, format_analysis_date_input(normalized)

        raise PreventUpdate

    @app.callback(
        Output("crime-analysis-state-store", "data"),
        Input("crime-analysis-start-date-input", "value"),
        Input("crime-analysis-end-date-input", "value"),
        Input("crime-category-filter", "value"),
        Input("crime-subcategory-filter", "value"),
        Input("crime-neighborhood-filter", "value"),
    )
    def update_crime_analysis_state(start_date, end_date, categories, subcategories, neighborhoods):
        dates = validate_analysis_dates(
            start_date, end_date, crime_analysis_start, crime_analysis_end,
        )
        if dates is None:
            raise PreventUpdate
        return make_analysis_state(
            {"start": dates[0], "end": dates[1]}, categories, subcategories, neighborhoods,
            default_crime_start, default_crime_end, TARGET_CRIME_CATEGORIES,
        )

    @app.callback(
        Output("crime-analysis-period-duration", "children"),
        Input("crime-analysis-state-store", "data"),
    )
    def update_crime_analysis_period(analysis_state):
        state = analysis_state or default_crime_analysis_state
        return format_analysis_period_annotation(state, crime_analysis_end)

    @app.callback(
        Output("crime-daily-figure", "figure"),
        Input("crime-analysis-state-store", "data"),
        Input("crime-legend-toggle", "value"),
        Input("crime-daily-relayout-debounced-store", "data"),
    )
    def update_crime_daily_figure(analysis_state, legend_values, relayout_data=None):
        fig = cached_crime_daily_figure(
            json.dumps(analysis_state or default_crime_analysis_state, sort_keys=True),
            "daily" in (legend_values or []),
        )
        if chart_range_needs_correction(relayout_data, crime_analysis_start, crime_analysis_end):
            fig = deepcopy(fig)
            fig.update_layout(uirevision=None)
        return fig

    @app.callback(
        Output("crime-map-mount-host", "children"),
        Output("crime-map-point-window-label", "children"),
        Input("crime-analysis-state-store", "data"),
        Input("crime-legend-toggle", "value"),
        Input("crime-point-text-filter", "value"),
        Input("crime-map-metric", "value"),
        Input("crime-map-layer", "value"),
    )
    def update_crime_map_mount(analysis_state, legend_values, text_filter,
                               metric_mode="raw", layer_mode="choropleth"):
        analysis_state = bounded_crime_state(analysis_state)
        point_start_date = analysis_state["start_date"]
        point_end_date = analysis_state["end_date"]
        show_colorbar = "map_colorbar" in (legend_values or [])
        fig, visible_point_count = build_crime_map_figure(
            analysis_state, show_colorbar, text_filter, metric_mode, layer_mode,
        )
        label = (
            f"Map points: {point_start_date} to {point_end_date}"
            f" | visible points: {visible_point_count:,}"
        )

        return make_crime_map_mount(figure=fig, layer_mode=layer_mode), label


    app.clientside_callback(
        ClientsideFunction(namespace="crime_map", function_name="bind_region_toggle"),
        Output("crime-map-listener-anchor", "children"),
        Input("crime-map-figure", "figure"),
        Input("crime-fullscreen-figure", "figure"),
    )

    @app.callback(
        Output("crime-neighborhood-filter", "value"),
        Input("crime-map-region-toggle", "data"),
        State("crime-neighborhood-filter", "value"),
        State("crime-neighborhood-filter", "options"),
        prevent_initial_call=True,
    )
    def toggle_neighborhood_from_map(toggle_data, current_value, neighborhood_options):
        if not isinstance(toggle_data, dict):
            raise PreventUpdate
        neighborhood = toggle_data.get("neighborhood")
        available = [option["value"] for option in (neighborhood_options or [])]
        if not isinstance(neighborhood, str) or neighborhood not in available:
            raise PreventUpdate
        available_set = set(available)
        enabled = set(current_value) & available_set if current_value else available_set.copy()
        enabled.symmetric_difference_update({neighborhood})
        if not enabled:
            raise PreventUpdate
        if enabled == available_set:
            return []
        return [name for name in available if name in enabled]

    @app.callback(
        Output("crime-fullscreen-figure-store", "data"),
        Input("crime-expand-map-button", "n_clicks"),
        Input("crime-expand-daily-button", "n_clicks"),
        Input("crime-close-fullscreen-button", "n_clicks"),
        prevent_initial_call=True,
    )
    def update_crime_fullscreen_store(
        map_clicks,
        daily_clicks,
        close_clicks,
    ):
        triggered_id = ctx.triggered_id

        if triggered_id == "crime-close-fullscreen-button":
            return None

        if triggered_id == "crime-expand-map-button":
            return "map"

        if triggered_id == "crime-expand-daily-button":
            return "daily"

        raise PreventUpdate


    @app.callback(
        Output("crime-fullscreen-overlay", "className"),
        Output("crime-fullscreen-title", "children"),
        Output("crime-fullscreen-figure", "figure"),
        Input("crime-fullscreen-figure-store", "data"),
        Input("crime-analysis-state-store", "data"),
        Input("crime-legend-toggle", "value"),
        Input("crime-point-text-filter", "value"),
        Input("crime-map-metric", "value"),
        Input("crime-map-layer", "value"),
    )
    def update_crime_fullscreen_overlay(
        fullscreen_target, analysis_state, legend_values, text_filter,
        metric_mode="raw", layer_mode="choropleth",
    ):
        analysis_state = bounded_crime_state(analysis_state)
        legend_values = legend_values or []
        if fullscreen_target is None:
            return "fullscreen-overlay hidden", "", {}
        if fullscreen_target == "map":
            fig, visible_point_count = build_crime_map_figure(
                analysis_state, "map_colorbar" in legend_values, text_filter, metric_mode, layer_mode,
            )
            title = (
                f"Map view | {analysis_state['start_date']} to {analysis_state['end_date']}"
                f" | {visible_point_count:,} visible points"
            )
            return "fullscreen-overlay", title, fig
        if fullscreen_target == "daily":
            fig = cached_crime_daily_figure(
                json.dumps(analysis_state, sort_keys=True), "daily" in legend_values,
            )
            return "fullscreen-overlay", "Daily crime events", fig

        raise PreventUpdate

    @app.callback(
        Output("crime-v11-count-body", "children"), Output("crime-v11-count-card", "style"),
        Input("crime-analysis-state-store", "data"), Input("crime-v11-count-mode", "value"),
    )
    def update_crime_count(state, mode):
        return crime_components.render_crime_count(crime_context["valid_time"], bounded_crime_state(state), mode)

    @app.callback(
        Output("crime-v11-rate-body", "children"), Output("crime-v11-rate-card", "style"),
        Input("crime-analysis-state-store", "data"), Input("crime-v11-rate-mode", "value"),
    )
    def update_crime_rate(state, mode):
        return crime_components.render_crime_rate(crime_context, bounded_crime_state(state), mode)

    @app.callback(
        Output("crime-v11-category-body", "children"),
        Input("crime-analysis-state-store", "data"), Input("crime-v11-category-mode", "value"),
    )
    def update_crime_categories(state, mode):
        return crime_components.render_category_comparison(crime_context["valid_time"], bounded_crime_state(state), mode)

    @app.callback(
        Output("crime-v11-response-body", "children"), Output("crime-v11-response-card", "style"),
        Input("crime-analysis-state-store", "data"), Input("crime-v11-response-priority", "value"),
        Input("crime-v11-response-mode", "value"),
    )
    def update_crime_response(state, priority, mode):
        return crime_components.render_response_kpi(calls_context, bounded_crime_state(state), priority, mode)

    @lru_cache(maxsize=1)
    def crime_ranking_sources():
        return crime_components.prepare_multimetric_sources(calls_context, crime_context)

    @app.callback(
        Output("crime-v11-ranking-panel", "className"),
        Output("crime-v11-ranking-expand", "children"),
        Output("crime-v11-ranking-expand", "className"),
        Output("crime-v11-ranking-expand", "title"),
        Input("crime-v11-ranking-expand", "n_clicks"),
        State("crime-v11-ranking-panel", "className"),
        prevent_initial_call=True,
    )
    def toggle_crime_ranking_fullscreen(n_clicks, panel_class):
        return crime_components.ranking_fullscreen_presentation("fullscreen-overlay" not in (panel_class or ""))

    @app.callback(
        Output("crime-v11-ranking-body", "children"),
        Input("crime-analysis-start-date-input", "value"), Input("crime-analysis-end-date-input", "value"),
        Input("crime-v11-ranking-metric", "value"), Input("crime-v11-ranking-priority", "value"),
        Input("crime-v11-ranking-min-events", "value"), Input("crime-v11-ranking-panel", "className"),
    )
    def update_crime_ranking(start_date, end_date, metric, priority, min_events, panel_class):
        return crime_components.render_multimetric_ranking(
            crime_ranking_sources(), start_date, end_date, metric, priority, min_events,
            fullscreen="fullscreen-overlay" in (panel_class or ""),
        )

    return app


dashboard = create_app()
server = dashboard.server


@server.route("/healthz")
def health_check():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    dashboard.run(debug=True)
