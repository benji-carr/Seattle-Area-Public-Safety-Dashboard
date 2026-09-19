import json

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dashboard.analysis_windows import daily_chart_window
from dashboard.crime_filters import filter_crime_records
from dashboard.crime_classification import (
    CANONICAL_CRIME_TYPES,
    CRIMES_AGAINST_PERSONS,
    CRIMES_AGAINST_PROPERTY,
    CRIMES_AGAINST_SOCIETY,
)

from dashboard.spd_config import (
    PAPER_BG,
    PLOTLY_MAP_STYLE,
    PLOTLY_SEATTLE_CENTER,
    PLOTLY_TEMPLATE,
    PLOT_BG,
)
from dashboard.crime_dashboard_data import (
    EVENT_ID_COLUMN,
    ROW_ID_COLUMN,
    TIME_COLUMN,
    LAT_COL,
    LON_COL,
    CATEGORY_COLUMN,
    SUB_CATEGORY_COLUMN,
    normalize_neighborhood_name,
)


TARGET_CRIME_CATEGORIES = CANONICAL_CRIME_TYPES

# Point stacking is independent of the canonical category / legend order.
CRIME_POINT_RENDER_ORDER = [
    CRIMES_AGAINST_SOCIETY,
    CRIMES_AGAINST_PROPERTY,
    CRIMES_AGAINST_PERSONS,
]


CRIME_CATEGORY_COLOR_MAP = {
    CRIMES_AGAINST_SOCIETY: "#2F80ED",   # blue
    CRIMES_AGAINST_PROPERTY: "#27AE60",  # green
    CRIMES_AGAINST_PERSONS: "#EB5757",   # red
}


def get_category_color(category_name: str) -> str:
    return CRIME_CATEGORY_COLOR_MAP.get(category_name, "#bbbbbb")


def get_combo_color(selected_categories: list[str]) -> str:
    if len(selected_categories) == 1:
        return get_category_color(selected_categories[0])

    return "#dddddd"


def make_crime_combo_label(category_combo: list[str]) -> str:
    if len(category_combo) == len(TARGET_CRIME_CATEGORIES):
        return "All selected categories"

    return " + ".join(category.title() for category in category_combo)


# Compatibility alias; both dashboards use the same analysis-domain calculation.
get_dataset_relative_daily_window = daily_chart_window


def prepare_daily_event_data(
    context: dict,
    selected_bins: list[str],
    analysis_state: dict | None = None,
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
    # Retain the selectable year for navigation, with shared analytical dimensions.
    valid_time = filter_crime_records(valid_time, analysis_state, include_dates=False)

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
            reported_offenses=(EVENT_ID_COLUMN, "nunique"),
            unique_reports=(ROW_ID_COLUMN, "nunique"),
        )
        .set_index("date")
        .reindex(date_index)
        .fillna(0)
        .rename_axis("date")
        .reset_index()
    )

    daily_volume["reported_offenses"] = (
        daily_volume["reported_offenses"]
        .astype(int)
    )

    daily_volume["unique_reports"] = (
        daily_volume["unique_reports"]
        .astype(int)
    )

    daily_volume["rolling_7_day_avg"] = (
        daily_volume["reported_offenses"]
        .rolling(
            window=7,
            min_periods=7,
        )
        .mean()
    )

    return daily_volume, window

def make_plotly_safe_customdata(
    data: pd.DataFrame,
    columns: list[str],
) -> np.ndarray:
    customdata = data[columns].copy()

    for column in columns:
        if pd.api.types.is_numeric_dtype(customdata[column]):
            customdata[column] = pd.to_numeric(
                customdata[column],
                errors="coerce",
            )
        else:
            customdata[column] = (
                customdata[column]
                .astype("object")
                .where(customdata[column].notna(), "Not available")
            )

    return customdata.to_numpy()


def make_daily_figure(
    context: dict,
    selected_bins: list[str],
    analysis_state: dict | None = None,
) -> go.Figure:
    combo_label = make_crime_combo_label(selected_bins)
    combo_color = get_combo_color(selected_bins)

    daily_volume, window = prepare_daily_event_data(
        context=context,
        selected_bins=selected_bins,
        analysis_state=analysis_state,
    )

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=daily_volume["date"],
            y=daily_volume["reported_offenses"],
            mode="lines",
            name="Daily reported offenses",
            line=dict(
                width=1.4,
                color=combo_color,
            ),
            opacity=0.45,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "Daily reported offenses: %{y:,}"
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
        title=dict(
            text=f"Daily Crime Events<br><sup>Type of Crime: {combo_label}</sup>",
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
                    dict(count=1, label="1D", step="day", stepmode="backward"),
                    dict(count=7, label="1W", step="day", stepmode="backward"),
                    dict(count=1, label="1M", step="month", stepmode="backward"),
                    dict(count=1, label="1Y", step="year", stepmode="backward"),
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
                text="Reported offenses",
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

RATE_MIN_POPULATION = 5_000
ACTIVE_REGION_OPACITY = 0.72
DISABLED_REGION_OPACITY = 0.06
RATE_INELIGIBLE_OPACITY = 0.10


def get_canonical_mcpp_names(context: dict) -> set[str]:
    return set(normalize_neighborhood_name(
        context["mcpp_boundaries"]["mcpp_neighborhood"]
    ).dropna())


def assert_analytical_mcpp_contract(context: dict, records: pd.DataFrame) -> None:
    """Reject broken shared geography; never repair assignments in a figure."""
    invalid = set(records["mcpp_neighborhood"].dropna()) - get_canonical_mcpp_names(context)
    if invalid:
        raise AssertionError(f"Noncanonical analytical MCPP names: {sorted(invalid)}")
    assignments = records.groupby(EVENT_ID_COLUMN)["mcpp_neighborhood"].nunique(dropna=False)
    if assignments.gt(1).any():
        raise AssertionError("An offense has multiple analytical MCPP assignments")


def prepare_crime_point_source(context: dict) -> pd.DataFrame:
    """Attach shared analytical geography to a copy of coordinate-valid points."""
    analytical = context["valid_time"]
    assert_analytical_mcpp_contract(context, analytical)
    lookup = analytical[[EVENT_ID_COLUMN, "mcpp_neighborhood"]].drop_duplicates(EVENT_ID_COLUMN)
    points = context["event_mcpp"].copy()
    points["spatial_mcpp_neighborhood"] = points["mcpp_neighborhood"]
    points = points.drop(columns="mcpp_neighborhood").merge(
        lookup, on=EVENT_ID_COLUMN, how="left", validate="many_to_one",
    )
    return points


def prepare_crime_choropleth_data(context: dict, records: pd.DataFrame):
    """Count analytical offenses on every boundary; retain unassigned in summary."""
    assert_analytical_mcpp_contract(context, records)
    assigned = records.dropna(subset=[EVENT_ID_COLUMN, "mcpp_neighborhood"])
    counts = assigned.groupby("mcpp_neighborhood").agg(
        offense_count=(EVENT_ID_COLUMN, "nunique"),
    ).reset_index()
    boundaries = context["mcpp_boundaries"].copy()
    boundaries["mcpp_neighborhood"] = normalize_neighborhood_name(boundaries["mcpp_neighborhood"])
    population = context["neighborhood_population"].copy()
    if "mcpp_neighborhood" not in population:
        population = population.rename(columns={"dispatch_neighborhood": "mcpp_neighborhood"})
    population["mcpp_neighborhood"] = normalize_neighborhood_name(population["mcpp_neighborhood"])
    population["population"] = pd.to_numeric(population["population"], errors="coerce")
    choropleth = boundaries.merge(
        counts, on="mcpp_neighborhood", how="left", validate="one_to_one",
    ).merge(
        population[["mcpp_neighborhood", "population"]],
        on="mcpp_neighborhood", how="left", validate="one_to_one",
    )
    choropleth["offense_count"] = choropleth["offense_count"].fillna(0).astype(int)
    choropleth["crime_rate_per_100k"] = (
        choropleth["offense_count"] / choropleth["population"].where(choropleth["population"] > 0)
        * 100_000
    )
    choropleth["rate_eligible"] = choropleth["population"].ge(RATE_MIN_POPULATION)
    choropleth["mcpp_neighborhood_display"] = choropleth["mcpp_neighborhood"].str.title()
    choropleth["population_display"] = choropleth["population"].map(
        lambda value: f"{value:,.0f}" if pd.notna(value) else "Not available",
    )
    choropleth["rate_display"] = np.where(
        choropleth["rate_eligible"],
        choropleth["crime_rate_per_100k"].map(lambda value: f"{value:,.1f}"),
        "Not shown (<5,000 population)",
    )
    total = int(records[EVENT_ID_COLUMN].nunique())
    recognized = int(assigned[EVENT_ID_COLUMN].nunique())
    if int(choropleth["offense_count"].sum()) != recognized:
        raise AssertionError("Polygon counts do not reconcile to recognized analytical offenses")
    return choropleth, {
        "total_offenses": total, "assigned_offenses": recognized,
        "unassigned_offenses": total - recognized,
    }


def _add_crime_choropleth(fig, choropleth, active_names, metric_mode, show_colorbar):
    active = choropleth["mcpp_neighborhood"].isin(active_names)
    rate = metric_mode == "rate"
    eligible = choropleth["rate_eligible"]
    metric = "crime_rate_per_100k" if rate else "offense_count"
    active_values = choropleth.loc[active & eligible if rate else active, metric].dropna()
    zmin = float(active_values.min()) if not active_values.empty else 0.0
    zmax = float(active_values.max()) if not active_values.empty else 1.0
    if zmin == zmax:
        zmax = zmin + 1.0
    opacity = np.where(
        ~active, DISABLED_REGION_OPACITY,
        np.where(rate & ~eligible, RATE_INELIGIBLE_OPACITY, ACTIVE_REGION_OPACITY),
    )
    choropleth = choropleth.assign(region_status=np.where(active, "Enabled", "Disabled"))
    offense_hover = "Offenses: %{customdata[1]:,}<br>"
    population_hover = "Estimated population: %{customdata[2]}<br>"
    rate_hover = "Rate per 100,000: %{customdata[3]}<br>"
    fig.add_trace(go.Choroplethmap(
        geojson=json.loads(choropleth[["mcpp_neighborhood", "geometry"]].to_json()),
        locations=choropleth["mcpp_neighborhood"], featureidkey="properties.mcpp_neighborhood",
        z=choropleth[metric].where(eligible, 0).fillna(0) if rate else choropleth[metric],
        colorscale="Viridis", zmin=zmin, zmax=zmax, zauto=False,
        marker={"opacity": opacity.tolist(), "line": {"width": 0.7, "color": "rgba(255,255,255,0.28)"}},
        colorbar={"title": "Rate<br>/100k" if rate else "Offenses", "thickness": 12, "len": 0.55, "x": 0.98},
        showscale=show_colorbar,
        customdata=choropleth[["mcpp_neighborhood_display", "offense_count", "population_display",
                              "rate_display", "region_status", "mcpp_neighborhood"]].to_numpy(),
        hovertemplate=("<b>%{customdata[0]}</b><br>"
                       + (rate_hover + offense_hover + population_hover if rate
                          else offense_hover + population_hover + rate_hover)
                       + "<br><i>Ctrl+click to enable/disable</i><extra></extra>"),
        showlegend=False, name="Neighborhoods",
    ))


def _add_crime_points(fig, points):
    points = points.copy()
    points["offense_time_display"] = pd.to_datetime(points[TIME_COLUMN]).dt.strftime("%b %d, %Y %H:%M")
    for source, target in [(CATEGORY_COLUMN, "crime_category_display"),
                           (SUB_CATEGORY_COLUMN, "crime_subcategory_display")]:
        points[target] = points[source].astype("string").str.title().fillna("Not available")
    points["neighborhood_display"] = points["mcpp_neighborhood"].astype("string").str.title().fillna("Unassigned")
    if "block_address" not in points:
        points["block_address"] = pd.NA
    for category in CRIME_POINT_RENDER_ORDER:
        selected = points.loc[points[CATEGORY_COLUMN] == category]
        if selected.empty:
            continue
        fig.add_trace(go.Scattermap(
            lat=selected[LAT_COL], lon=selected[LON_COL], mode="markers",
            marker={"size": 7, "opacity": 0.72, "color": get_category_color(category)},
            name=category.title(), legendgroup=category,
            legendrank=CANONICAL_CRIME_TYPES.index(category),
            customdata=make_plotly_safe_customdata(selected, [
                "crime_subcategory_display", "crime_category_display", "offense_time_display",
                "neighborhood_display", "block_address", ROW_ID_COLUMN,
            ]),
            hovertemplate=("<b>%{customdata[0]}</b><br>Category: %{customdata[1]}<br>"
                           "Offense time: %{customdata[2]}<br>Neighborhood: %{customdata[3]}<br>"
                           "Block: %{customdata[4]}<br>Report ID: %{customdata[5]}<extra></extra>"),
        ))


def make_map_figure(
    context: dict,
    selected_bins: list[str],
    point_start_date: str | None = None,
    point_end_date: str | None = None,
    show_colorbar: bool = False,
    point_filters: dict | None = None,
    analysis_state: dict | None = None,
    metric_mode: str = "raw",
    layer_mode: str = "choropleth",
) -> go.Figure:
    if metric_mode not in {"raw", "rate"} or layer_mode not in {"choropleth", "points", "both"}:
        raise ValueError("Unknown crime map metric or layer mode")
    analytical = context["valid_time"]
    if analysis_state is None:
        window = get_dataset_relative_daily_window(analytical)
        analysis_state = {
            "start_date": point_start_date or window["plot_start_day"].date().isoformat(),
            "end_date": point_end_date or window["plot_end_day"].date().isoformat(),
            "crime_categories": selected_bins, "crime_subcategories": [], "neighborhoods": [],
        }
    canonical = get_canonical_mcpp_names(context)
    active_names = set(analysis_state.get("neighborhoods") or canonical)
    if not active_names <= canonical:
        raise AssertionError("Neighborhood selection contains noncanonical MCPP names")
    choropleth_records = filter_crime_records(analytical, {**analysis_state, "neighborhoods": []})
    choropleth, _ = prepare_crime_choropleth_data(context, choropleth_records)
    active_records = filter_crime_records(analytical, analysis_state)
    total = int(active_records[EVENT_ID_COLUMN].nunique())
    assigned = int(active_records.loc[active_records["mcpp_neighborhood"].notna(), EVENT_ID_COLUMN].nunique())
    points = filter_crime_records(prepare_crime_point_source(context), analysis_state)
    points = apply_point_filters(points, point_filters)
    fig = go.Figure()
    if layer_mode in {"choropleth", "both"}:
        _add_crime_choropleth(fig, choropleth, active_names, metric_mode, show_colorbar)
    if layer_mode in {"points", "both"}:
        _add_crime_points(fig, points)
    fig.update_layout(
        map={"style": PLOTLY_MAP_STYLE, "center": PLOTLY_SEATTLE_CENTER, "zoom": 10},
        paper_bgcolor="#181818", plot_bgcolor="#181818", font={"color": "#dddddd"},
        autosize=True, margin={"l": 0, "r": 0, "t": 0, "b": 0},
        legend={"x": 0.02, "y": 0.98, "xanchor": "left", "yanchor": "top",
                "bgcolor": "rgba(17,17,17,0.80)", "bordercolor": "#333333", "borderwidth": 1,
                "font": {"size": 10, "color": "#ffffff"}},
        clickmode="event", uirevision="v11-crime-map-camera",
        legend_uirevision=json.dumps(analysis_state.get("crime_categories", [])),
        meta={"total_offenses": total, "assigned_offenses": assigned,
              "unassigned_offenses": total - assigned,
              "enabled_neighborhoods": len(active_names),
              "disabled_neighborhoods": len(canonical - active_names)},
    )
    return fig


def apply_point_filters(
    point_events: pd.DataFrame,
    point_filters: dict | None,
) -> pd.DataFrame:
    if point_filters is None:
        return point_events

    filtered = point_events.copy()

    selected_subcategories = point_filters.get("offense_sub_categories", [])
    selected_neighborhoods = point_filters.get("mcpp_neighborhoods", [])
    text_filter = str(point_filters.get("text", "")).strip().lower()

    if selected_subcategories:
        filtered = filtered[
            filtered["offense_sub_category"].isin(selected_subcategories)
        ].copy()

    if selected_neighborhoods:
        filtered = filtered[
            filtered["mcpp_neighborhood"].isin(selected_neighborhoods)
        ].copy()

    if text_filter:
        searchable_text = (
            filtered["offense_id"].astype("string").fillna("")
            + " "
            + filtered["report_number"].astype("string").fillna("")
            + " "
            + filtered["block_address"].astype("string").fillna("")
            + " "
            + filtered["offense_sub_category"].astype("string").fillna("")
        ).str.lower()

        filtered = filtered[
            searchable_text.str.contains(
                text_filter,
                regex=False,
                na=False,
            )
        ].copy()

    return filtered


if __name__ == "__main__":
    from dashboard.crime_dashboard_data import (
        load_crime_dashboard_context,
    )

    context = load_crime_dashboard_context()

    fig = make_map_figure(
        context=context,
        selected_bins=TARGET_CRIME_CATEGORIES,
        show_colorbar=True,
    )

    fig.show()

    daily_fig = make_daily_figure(
        context=context,
        selected_bins=TARGET_CRIME_CATEGORIES,
    )

    daily_fig.show()
