import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dashboard.analysis_windows import daily_chart_window, get_analysis_bounds, get_history_bounds

from dashboard.spd_config import (
    ARRIVAL_TIME_COLUMN,
    EVENT_ID_COLUMN,
    LAT_COL,
    LON_COL,
    PAPER_BG,
    PLOTLY_MAP_STYLE,
    PLOTLY_SEATTLE_CENTER,
    PLOTLY_TEMPLATE,
    PLOT_BG,
    ROW_ID_COLUMN,
    TARGET_IMPORTANCE_BINS,
    TIME_COLUMN,
)
from dashboard.spd_event_bins import (
    make_bin_combo_label,
)


BIN_COLOR_MAP = {
    "drug-related": "#2F80ED",             # blue
    "property/nonviolent": "#27AE60",      # green
    "violent/person crime": "#EB5757",     # red
}


def get_bin_color(bin_name: str) -> str:
    return BIN_COLOR_MAP.get(bin_name, "#bbbbbb")


def get_combo_color(selected_bins: list[str]) -> str:
    if len(selected_bins) == 1:
        return get_bin_color(selected_bins[0])

    return "#dddddd"


# Compatibility alias; both dashboards use the same analysis-domain calculation.
get_dataset_relative_daily_window = daily_chart_window


def prepare_daily_event_data(
    context: dict,
    selected_bins: list[str],
) -> tuple[pd.DataFrame, dict]:
    valid_time = context["valid_time"].copy()

    required_columns = [
        TIME_COLUMN,
        EVENT_ID_COLUMN,
        ROW_ID_COLUMN,
        "event_importance_bin",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in valid_time.columns
    ]

    if missing_columns:
        raise ValueError(
            f"valid_time is missing required columns: {missing_columns}"
        )

    valid_time[TIME_COLUMN] = pd.to_datetime(
        valid_time[TIME_COLUMN],
        errors="coerce",
    )

    valid_time = valid_time[
        valid_time[TIME_COLUMN].notna()
        & valid_time[EVENT_ID_COLUMN].notna()
    ].copy()

    valid_time["date"] = valid_time[TIME_COLUMN].dt.normalize()

    window = get_dataset_relative_daily_window(valid_time)

    plot_start_day = window["plot_start_day"]
    plot_end_day = window["plot_end_day"]

    filtered = valid_time[
        valid_time["date"].between(
            plot_start_day,
            plot_end_day,
        )
        & valid_time["event_importance_bin"].isin(selected_bins)
    ].copy()

    date_index = pd.date_range(
        start=plot_start_day,
        end=plot_end_day,
        freq="D",
    )

    daily_volume = (
        filtered
        .groupby("date", as_index=False)
        .agg(
            unique_call_events=(EVENT_ID_COLUMN, "nunique"),
            dispatch_records=(ROW_ID_COLUMN, "nunique"),
        )
        .set_index("date")
        .reindex(date_index)
        .fillna(0)
        .rename_axis("date")
        .reset_index()
    )

    daily_volume["unique_call_events"] = (
        daily_volume["unique_call_events"]
        .astype(int)
    )

    daily_volume["dispatch_records"] = (
        daily_volume["dispatch_records"]
        .astype(int)
    )

    daily_volume["rolling_7_day_avg"] = (
        daily_volume["unique_call_events"]
        .rolling(
            window=7,
            min_periods=7,
        )
        .mean()
    )

    return daily_volume, window


def make_daily_figure(
    context: dict,
    selected_bins: list[str],
) -> go.Figure:
    combo_label = make_bin_combo_label(selected_bins)
    combo_color = get_combo_color(selected_bins)

    daily_volume, window = prepare_daily_event_data(
        context=context,
        selected_bins=selected_bins,
    )

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=daily_volume["date"],
            y=daily_volume["unique_call_events"],
            mode="lines",
            name="Daily unique CAD events",
            line=dict(
                width=1.4,
                color=combo_color,
            ),
            opacity=0.45,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "Daily unique CAD events: %{y:,}"
                "<extra></extra>"
            ),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=daily_volume["date"],
            y=daily_volume["rolling_7_day_avg"],
            mode="lines",
            name="7-day average",
            line=dict(
                width=3,
                color=combo_color,
            ),
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "7-day average: %{y:,.1f}"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=dict(  # Figure title: shortened main title with Type of Crime moved into subtitle
            text=f"Daily CAD Events<br><sup>Type of Call: {combo_label}</sup>",
            x=0.01,
            xanchor="left",
        ),
        template=PLOTLY_TEMPLATE,
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=PAPER_BG,
        xaxis=dict(
            title=None,
            range=[
                window["initial_view_start"],
                window["plot_end_day"],
            ],
            rangeselector=dict(
                x=0.01,
                xanchor="left",
                y=1,
                yanchor="top",
                bgcolor="rgba(17, 17, 17, 0.85)",
                activecolor="rgba(255,255,255,0.18)",
                bordercolor="rgba(255,255,255,0.15)",
                borderwidth=1,
                font=dict(size=10),
                buttons=[
                    dict(
                        count=1,
                        label="1D",
                        step="day",
                        stepmode="backward",
                    ),
                    dict(
                        count=6,
                        label="1W",
                        step="day",
                        stepmode="backward",
                    ),
                    dict(
                        count=29,
                        label="1M",
                        step="day",
                        stepmode="backward",
                    ),
                    dict(
                        count=1,
                        label="1Y",
                        step="year",
                        stepmode="backward",
                    ),
                ],
            ),
            minallowed=window["plot_start_day"],
            maxallowed=window["plot_end_day"],
            autorangeoptions=dict(minallowed=window["plot_start_day"], maxallowed=window["plot_end_day"]),
            rangeslider=dict(
                range=[window["plot_start_day"], window["plot_end_day"]],
                autorange=False,
                visible=True,
                thickness=0.08,
            ),
            automargin=True,
        ),
        yaxis=dict(
            title=dict(
                text="Unique CAD events",
                standoff=12,
            ),
            automargin=True,
        ),
        margin={
            "l": 70,
            "r": 25,
            "t": 84,
            "b": 45,
        },
        hovermode="x unified",
        legend_title_text="Metric",
        showlegend=False,
    )

    return fig


def add_concern_score(data: pd.DataFrame) -> pd.DataFrame:
    out = data.copy()

    out["volume_rank"] = (
        out["annualized_events_per_1000"]
        .rank(pct=True)
    )

    out["response_rank"] = (
        out["median_response_minutes"]
        .rank(pct=True)
    )

    out["concern_score"] = (
        out["volume_rank"]
        * out["response_rank"]
    )

    out["marker_size"] = (
        10 + 40 * out["concern_score"]
    )

    return out


def prepare_volume_response_scatter_data(
    context: dict,
    selected_bins: list[str],
    min_events: int = 100,
) -> pd.DataFrame:
    response_analysis = context["response_analysis"].copy()
    neighborhood_population = context["neighborhood_population"].copy()
    _, latest = get_history_bounds(context["valid_time"][TIME_COLUMN])
    analysis_start, analysis_end = get_analysis_bounds(latest)
    response_analysis = response_analysis[
        pd.to_datetime(response_analysis["queued_time"]).dt.normalize().between(analysis_start, analysis_end)
    ].copy()
    from dashboard.spd_dashboard_data import calculate_years_observed
    years_observed = calculate_years_observed(response_analysis)

    if years_observed <= 0:
        years_observed = 1.0

    required_response_columns = [
        EVENT_ID_COLUMN,
        "dispatch_neighborhood",
        "event_importance_bin",
        "response_time_minutes",
    ]

    missing_response_columns = [
        column
        for column in required_response_columns
        if column not in response_analysis.columns
    ]

    if missing_response_columns:
        raise ValueError(
            f"response_analysis is missing required columns: {missing_response_columns}"
        )

    required_population_columns = [
        "dispatch_neighborhood",
        "population",
    ]

    missing_population_columns = [
        column
        for column in required_population_columns
        if column not in neighborhood_population.columns
    ]

    if missing_population_columns:
        raise ValueError(
            f"neighborhood_population is missing required columns: {missing_population_columns}"
        )

    scatter_response = response_analysis[
        response_analysis["event_importance_bin"].isin(selected_bins)
    ].copy()

    scatter_response = scatter_response[
        ~scatter_response["dispatch_neighborhood"].isin(
            ["unknown", "-", "", "nan"]
        )
    ].copy()

    volume_response_scatter = (
        scatter_response
        .groupby(["dispatch_neighborhood", "event_importance_bin"], as_index=False)
        .agg(
            unique_call_events=(EVENT_ID_COLUMN, "nunique"),
            median_response_minutes=("response_time_minutes", "median"),
            mean_response_minutes=("response_time_minutes", "mean"),
        )
    )

    volume_response_scatter = volume_response_scatter[
        volume_response_scatter["unique_call_events"] >= min_events
    ].copy()

    volume_response_scatter = volume_response_scatter.merge(
        neighborhood_population,
        on="dispatch_neighborhood",
        how="left",
    )

    volume_response_scatter = volume_response_scatter[
        volume_response_scatter["population"].notna()
        & (volume_response_scatter["population"] > 0)
    ].copy()

    volume_response_scatter["annualized_events_per_1000"] = (
        volume_response_scatter["unique_call_events"]
        / years_observed
        / volume_response_scatter["population"]
        * 1000
    )

    volume_response_scatter = volume_response_scatter[
        volume_response_scatter["annualized_events_per_1000"] > 0
    ].copy()

    return volume_response_scatter.reset_index(drop=True)


def make_volume_response_scatter(
    context: dict,
    selected_bins: list[str],
    min_events: int = 100,
) -> go.Figure:
    combo_label = make_bin_combo_label(selected_bins)

    plot_df = prepare_volume_response_scatter_data(
        context=context,
        selected_bins=selected_bins,
        min_events=min_events,
    )

    fig = go.Figure()

    if plot_df.empty:
        fig.update_layout(
            title=dict(  # Figure title: shortened main title with Type of Crime moved into subtitle
                text=(
                    "Call Volume (Per 1,000 Residents) vs. Median Response Time"
                    f"<br><sup>Type of Crime: {combo_label}</sup>"
                ),
                x=0.01,
                xanchor="left",
            ),
            template=PLOTLY_TEMPLATE,
            plot_bgcolor=PLOT_BG,
            paper_bgcolor=PAPER_BG,
            xaxis_title="Annualized unique CAD events per 1,000 residents",
            yaxis_title="Median response time minutes",
            showlegend=False,
        )

        fig.add_annotation(
            text="No neighborhoods meet the minimum event threshold for this selection.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
        )

        return fig

    plot_df = add_concern_score(plot_df)

    median_event_rate = plot_df["annualized_events_per_1000"].median()
    median_response = plot_df["median_response_minutes"].median()

    for bin_name in selected_bins:
        bin_df = plot_df[
            plot_df["event_importance_bin"] == bin_name
        ].copy()

        if bin_df.empty:
            continue

        customdata = np.stack(
            [
                bin_df["dispatch_neighborhood"].astype("string").str.title(),
                bin_df["event_importance_bin"],
                bin_df["population"],
                bin_df["unique_call_events"],
                bin_df["annualized_events_per_1000"],
                bin_df["median_response_minutes"],
                bin_df["mean_response_minutes"],
                bin_df["volume_rank"],
                bin_df["response_rank"],
                bin_df["concern_score"],
            ],
            axis=-1,
        )

        fig.add_trace(
            go.Scatter(
                x=bin_df["annualized_events_per_1000"],
                y=bin_df["median_response_minutes"],
                mode="markers",
                name=bin_name,
                legendgroup=bin_name,
                marker=dict(
                    size=bin_df["marker_size"],
                    sizemode="diameter",
                    opacity=0.75,
                    color=get_bin_color(bin_name),
                    line=dict(
                        width=0.8,
                        color="white",
                    ),
                ),
                customdata=customdata,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    "Type of Crime: %{customdata[1]}<br>"
                    "Population: %{customdata[2]:,.0f}<br>"
                    "Unique CAD events in dashboard window: %{customdata[3]:,}<br>"
                    "Annualized events per 1,000 residents: %{customdata[4]:.1f}<br>"
                    "Median response time: %{customdata[5]:.1f} min<br>"
                    "Mean response time: %{customdata[6]:.1f} min<br>"
                    "<br>"
                    "Volume percentile rank: %{customdata[7]:.2f}<br>"
                    "Response percentile rank: %{customdata[8]:.2f}<br>"
                    "Concern score: %{customdata[9]:.2f}"
                    "<extra></extra>"
                ),
            )
        )

    fig.add_trace(
        go.Scatter(
            x=[
                median_event_rate,
                median_event_rate,
            ],
            y=[
                plot_df["median_response_minutes"].min(),
                plot_df["median_response_minutes"].max(),
            ],
            mode="lines",
            name=f"Median rate: {median_event_rate:.1f}",
            showlegend=False,
            line=dict(
                dash="dash",
                width=2,
                color="white",
            ),
            hovertemplate=(
                f"Median annualized event rate: {median_event_rate:.1f} "
                "per 1,000 residents"
                "<extra></extra>"
            ),
        )
    )

    fig.add_trace(
        go.Scatter(
            x=[
                plot_df["annualized_events_per_1000"].min(),
                plot_df["annualized_events_per_1000"].max(),
            ],
            y=[
                median_response,
                median_response,
            ],
            mode="lines",
            name=f"Median response: {median_response:.1f} min",
            showlegend=False,
            line=dict(
                dash="dash",
                width=2,
                color="white",
            ),
            hovertemplate=(
                f"Median response time: {median_response:.1f} min"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title=dict(  # Figure title: shortened main title with Type of Crime moved into subtitle
            text=f"Call Volume (Per 1,000 Residents) vs. Median Response Time<br><sup>Type of Crime: {combo_label}</sup>",
            x=0.01,
            xanchor="left",
        ),
        template=PLOTLY_TEMPLATE,
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=PAPER_BG,
        xaxis=dict(
            title=dict(
                text="Annualized unique CAD events per 1,000 residents, log scale",
                standoff=18,
            ),
            type="log",
            automargin=True,
        ),
        yaxis=dict(
            title=dict(
                text="Median response time minutes",
                standoff=12,
            ),
            automargin=True,
        ),
        legend_title_text="Type of Crime",
        margin={
            "l": 90,
            "r": 35,
            "t": 58,
            "b": 65,
        },
        showlegend=False,
    )

    return fig


def make_map_figure(
    context: dict,
    selected_bins: list[str],
    point_start_date: str | None = None,
    point_end_date: str | None = None,
    show_colorbar: bool = False,
) -> go.Figure:
    event_mcpp = context["event_mcpp"].copy()
    mcpp_boundaries = context["mcpp_boundaries"].copy()
    neighborhood_population = context["neighborhood_population"].copy()

    combo_label = make_bin_combo_label(selected_bins)

    required_event_columns = [
        EVENT_ID_COLUMN,
        TIME_COLUMN,
        ARRIVAL_TIME_COLUMN,
        LAT_COL,
        LON_COL,
        "event_group",
        "event_importance_bin",
        "mcpp_neighborhood",
        "mcpp_precinct",
        "priority",
        "initial_call_type",
        "final_call_type",
    ]

    missing_event_columns = [
        column
        for column in required_event_columns
        if column not in event_mcpp.columns
    ]

    if missing_event_columns:
        raise ValueError(
            f"event_mcpp is missing required columns: {missing_event_columns}"
        )

    required_boundary_columns = [
        "objectid",
        "plot_feature_id",
        "mcpp_neighborhood",
        "mcpp_precinct",
        "geometry",
    ]

    missing_boundary_columns = [
        column
        for column in required_boundary_columns
        if column not in mcpp_boundaries.columns
    ]

    if missing_boundary_columns:
        raise ValueError(
            f"mcpp_boundaries is missing required columns: {missing_boundary_columns}"
        )

    required_population_columns = [
        "dispatch_neighborhood",
        "population",
    ]

    missing_population_columns = [
        column
        for column in required_population_columns
        if column not in neighborhood_population.columns
    ]

    if missing_population_columns:
        raise ValueError(
            f"neighborhood_population is missing required columns: {missing_population_columns}"
        )

    event_mcpp[TIME_COLUMN] = pd.to_datetime(
        event_mcpp[TIME_COLUMN],
        errors="coerce",
    )

    event_mcpp[ARRIVAL_TIME_COLUMN] = pd.to_datetime(
        event_mcpp[ARRIVAL_TIME_COLUMN],
        errors="coerce",
    )

    event_mcpp[LAT_COL] = pd.to_numeric(
        event_mcpp[LAT_COL],
        errors="coerce",
    )

    event_mcpp[LON_COL] = pd.to_numeric(
        event_mcpp[LON_COL],
        errors="coerce",
    )

    event_mcpp = event_mcpp[
        event_mcpp[TIME_COLUMN].notna()
        & event_mcpp[EVENT_ID_COLUMN].notna()
        & event_mcpp[LAT_COL].notna()
        & event_mcpp[LON_COL].notna()
    ].copy()

    if event_mcpp.empty:
        fig = go.Figure()

        fig.update_layout(
            title=dict(  # Figure title: fallback title shown only if no mappable events exist
                text="Map unavailable<br><sup>No mappable events</sup>",
                x=0.01,
                xanchor="left",
            ),
            template=PLOTLY_TEMPLATE,
            paper_bgcolor=PAPER_BG,
            mapbox=dict(
                style=PLOTLY_MAP_STYLE,
                center=PLOTLY_SEATTLE_CENTER,
                zoom=10,
            ),
        )

        return fig

    latest_available_day = event_mcpp[TIME_COLUMN].dt.normalize().max()
    past_year_start = latest_available_day - pd.Timedelta(days=364)

    past_year_events = event_mcpp[
        event_mcpp[TIME_COLUMN]
        .dt.normalize()
        .between(
            past_year_start,
            latest_available_day,
        )
    ].copy()

    if "response_time_minutes" not in past_year_events.columns:
        past_year_events["response_time_minutes"] = (
            past_year_events[ARRIVAL_TIME_COLUMN]
            - past_year_events[TIME_COLUMN]
        ).dt.total_seconds() / 60

    past_year_response_events = past_year_events[
        past_year_events["response_time_minutes"].notna()
        & (past_year_events["response_time_minutes"] >= 0)
        & (past_year_events["response_time_minutes"] <= 24 * 60)
    ].copy()

    population_for_mcpp = neighborhood_population[["dispatch_neighborhood", "population"]].copy()

    population_for_mcpp = population_for_mcpp.rename(
        columns={
            "dispatch_neighborhood": "mcpp_neighborhood",
        }
    )

    population_for_mcpp["mcpp_neighborhood"] = (
        population_for_mcpp["mcpp_neighborhood"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    population_for_mcpp["population"] = pd.to_numeric(
        population_for_mcpp["population"],
        errors="coerce",
    )

    base_gdf = mcpp_boundaries.copy()

    base_gdf["plot_feature_id"] = (
        base_gdf["plot_feature_id"]
        .astype(str)
    )

    base_gdf["mcpp_neighborhood"] = (
        base_gdf["mcpp_neighborhood"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    if "mcpp_neighborhood_display" not in base_gdf.columns:
        base_gdf["mcpp_neighborhood_display"] = (
            base_gdf["mcpp_neighborhood"]
            .astype("string")
            .str.title()
        )

    base_gdf = base_gdf.drop(
        columns=["population"],
        errors="ignore",
    )

    base_gdf = base_gdf.merge(
        population_for_mcpp[
            [
                "mcpp_neighborhood",
                "population",
            ]
        ],
        on="mcpp_neighborhood",
        how="left",
    )

    base_gdf["population"] = pd.to_numeric(
        base_gdf["population"],
        errors="coerce",
    )

    base_geojson = json.loads(base_gdf.to_json())

    selected_events = past_year_events[
        past_year_events["event_importance_bin"].isin(selected_bins)
    ].copy()

    selected_counts = (
        selected_events
        .dropna(subset=["mcpp_neighborhood"])
        .groupby("mcpp_neighborhood", as_index=False)
        .agg(
            past_year_unique_events=(EVENT_ID_COLUMN, "nunique"),
        )
    )

    selected_response_summary = (
        past_year_response_events[
            past_year_response_events["event_importance_bin"].isin(selected_bins)
        ]
        .dropna(subset=["mcpp_neighborhood", "response_time_minutes"])
        .groupby("mcpp_neighborhood", as_index=False)
        .agg(
            selected_median_response_minutes=("response_time_minutes", "median"),
        )
    )

    choropleth_gdf = (
        base_gdf
        .merge(
            selected_counts,
            on="mcpp_neighborhood",
            how="left",
        )
        .merge(
            selected_response_summary,
            on="mcpp_neighborhood",
            how="left",
        )
    )

    choropleth_gdf["past_year_unique_events"] = (
        choropleth_gdf["past_year_unique_events"]
        .fillna(0)
        .astype(int)
    )

    choropleth_gdf["past_year_unique_events_per_1000"] = np.where(
        choropleth_gdf["population"].notna()
        & (choropleth_gdf["population"] > 0),
        (
            choropleth_gdf["past_year_unique_events"]
            / choropleth_gdf["population"]
            * 1000
        ),
        np.nan,
    )

    fig = go.Figure()

    fig.add_trace(
        go.Choroplethmapbox(
            geojson=base_geojson,
            locations=choropleth_gdf["plot_feature_id"],
            z=choropleth_gdf["past_year_unique_events_per_1000"],
            featureidkey="properties.plot_feature_id",
            colorscale="Viridis",
            marker={
                "opacity": 0.68,
                "line": {
                    "width": 0.4,
                    "color": "rgba(255,255,255,0.35)",
                },
            },
            colorbar={
                "title": "Selected call<br>events per 1,000",
                "x": 0.98,
                "y": 0.50,
                "len": 0.62,
            },
            showscale=show_colorbar,
            name="Past-year events per 1,000 residents",
            showlegend=False,
            customdata=choropleth_gdf[
                [
                    "mcpp_neighborhood_display",
                    "population",
                    "past_year_unique_events_per_1000",
                    "past_year_unique_events",
                    "selected_median_response_minutes",
                ]
            ].to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                f"Type of Call: {combo_label}<br>"
                "Population: %{customdata[1]:,.0f}<br>"
                "<br>"
                "Past-year unique CAD events: %{customdata[3]:,}<br>"
                "Past-year events per 1,000 residents: %{customdata[2]:.1f}<br>"
                "Median response time: %{customdata[4]:.1f} min"
                "<extra></extra>"
            ),
        )
    )

    point_events = selected_events.copy()

    if point_start_date is not None and point_end_date is not None:
        point_start = pd.to_datetime(point_start_date, errors="coerce")
        point_end = pd.to_datetime(point_end_date, errors="coerce")

        if pd.notna(point_start) and pd.notna(point_end):
            point_events = point_events[
                point_events[TIME_COLUMN]
                .dt.normalize()
                .between(
                    point_start.normalize(),
                    point_end.normalize(),
                )
            ].copy()

    else:
        point_start = latest_available_day 
        point_end = latest_available_day

        point_events = point_events[
            point_events[TIME_COLUMN]
            .dt.normalize()
            .between(
                point_start,
                point_end,
            )
        ].copy()

    point_metric_lookup = choropleth_gdf[
        [
            "mcpp_neighborhood",
            "population",
            "past_year_unique_events",
            "past_year_unique_events_per_1000",
            "selected_median_response_minutes",
        ]
    ].copy()

    point_events["mcpp_neighborhood"] = (
        point_events["mcpp_neighborhood"]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    point_events = point_events.merge(
        point_metric_lookup,
        on="mcpp_neighborhood",
        how="left",
    )

    point_events["event_time_display"] = (
        point_events[TIME_COLUMN]
        .dt.strftime("%Y-%m-%d %H:%M")
    )

    point_events["mcpp_neighborhood_display"] = (
        point_events["mcpp_neighborhood"]
        .astype("string")
        .str.title()
    )

    point_events["population_display"] = np.where(
        point_events["population"].notna(),
        point_events["population"].round(0).astype("Int64").astype(str),
        "Not available",
    )

    point_events["population_display"] = (
        point_events["population_display"]
        .replace("<NA>", "Not available")
    )

    point_events["past_year_unique_events_display"] = np.where(
        point_events["past_year_unique_events"].notna(),
        point_events["past_year_unique_events"].round(0).astype("Int64").astype(str),
        "Not available",
    )

    point_events["past_year_unique_events_display"] = (
        point_events["past_year_unique_events_display"]
        .replace("<NA>", "Not available")
    )

    point_events["past_year_events_per_1000_display"] = np.where(
        point_events["past_year_unique_events_per_1000"].notna(),
        point_events["past_year_unique_events_per_1000"].round(1).astype(str),
        "Not available",
    )

    point_events["median_response_display"] = np.where(
        point_events["selected_median_response_minutes"].notna(),
        (
            point_events["selected_median_response_minutes"]
            .round(1)
            .astype(str)
            + " min"
        ),
        "Not available",
    )

    for bin_name in TARGET_IMPORTANCE_BINS:
        if bin_name not in selected_bins:
            continue

        bin_points = point_events[
            point_events["event_importance_bin"] == bin_name
        ].copy()

        if bin_points.empty:
            continue

        fig.add_trace(
            go.Scattermapbox(
                lat=bin_points[LAT_COL],
                lon=bin_points[LON_COL],
                mode="markers",
                name=bin_name,
                legendgroup=bin_name,
                showlegend=True,
                marker=dict(
                    size=8,
                    opacity=0.85,
                    color=get_bin_color(bin_name),
                ),
                customdata=bin_points[
                    [
                        EVENT_ID_COLUMN,
                        "event_time_display",
                        "event_importance_bin",
                        "event_group",
                        "priority",
                        "initial_call_type",
                        "final_call_type",
                        "mcpp_neighborhood_display",
                        "population_display",
                        "past_year_unique_events_display",
                        "past_year_events_per_1000_display",
                        "median_response_display",
                    ]
                ].to_numpy(),
                hovertemplate=(
                    "<b>CAD Event:</b> %{customdata[0]}<br>"
                    "<b>Time:</b> %{customdata[1]}<br>"
                    f"<b>Selected Type of Call:</b> {combo_label}<br>"
                    "<b>Point Type of Call:</b> %{customdata[2]}<br>"
                    "<b>Event group:</b> %{customdata[3]}<br>"
                    "<b>Priority:</b> %{customdata[4]}<br>"
                    "<br>"
                    "<b>Initial call type:</b> %{customdata[5]}<br>"
                    "<b>Final call type:</b> %{customdata[6]}<br>"
                    "<b>Neighborhood:</b> %{customdata[7]}<br>"
                    "<br>"
                    "<b>Population:</b> %{customdata[8]}<br>"
                    "<b>Past-year unique CAD events:</b> %{customdata[9]}<br>"
                    "<b>Past-year events per 1,000 residents:</b> %{customdata[10]}<br>"
                    "<b>Median response time:</b> %{customdata[11]}"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=dict(
            text=f"Calls to SPD Per 1,000 Residents In the Past Year<br><sup>Type of Call: {combo_label}</sup>",
            x=0.01,
            xanchor="left",
        ),
        template=PLOTLY_TEMPLATE,
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PAPER_BG,
        mapbox=dict(
            style=PLOTLY_MAP_STYLE,
            center=PLOTLY_SEATTLE_CENTER,
            zoom=10,
        ),
        legend=dict(
            title="Point Type",
            x=0.02,
            y=0.50,
            xanchor="left",
            yanchor="middle",
            bgcolor="rgba(0,0,0,0.55)",
            bordercolor="rgba(255,255,255,0.25)",
            borderwidth=1,
            font=dict(
                color="#dddddd",
                size=11,
            ),
        ),
        margin={
            "l": 0,
            "r": 0,
            "t": 58,
            "b": 0,
        },
        showlegend=True,
    )

    return fig


if __name__ == "__main__":
    from dashboard.spd_dashboard_data import (
        load_dashboard_context,
    )

    context = load_dashboard_context()

    fig = make_map_figure(
        context=context,
        selected_bins=TARGET_IMPORTANCE_BINS,
        show_colorbar=True,
    )

    fig.show()
