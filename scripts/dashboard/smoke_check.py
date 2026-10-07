"""Exercise the production neighborhood table and citywide response KPI inputs."""

from dashboard.crime_call_support_data import load_crime_call_support_context
from dashboard.crime_dashboard_data import load_crime_dashboard_context
from dashboard.crime_dashboard_components import (
    RESPONSE_PRIORITY_OPTIONS, get_response_kpi_values,
    prepare_multimetric_sources, prepare_multimetric_ranking,
)
from dashboard.analysis_windows import get_history_bounds


def main():
    calls = load_crime_call_support_context()
    crime = load_crime_dashboard_context()
    sources = prepare_multimetric_sources(calls, crime)
    if sources["analysis_bounds"] is None:
        raise ValueError("Production calls/crime metrics have no shared analysis period")
    start, end = sources["analysis_bounds"]
    history = get_history_bounds(calls["response_analysis"]["queued_time"])
    for scope, priorities in RESPONSE_PRIORITY_OPTIONS.items():
        values = get_response_kpi_values(calls["response_analysis"], start, end, priorities, *history)
        if values["current_events"] == 0:
            raise ValueError(f"No qualified responses for {scope}")
        for metric in ("response_time", "call_volume", "crime_volume"):
            ranking, _ = prepare_multimetric_ranking(sources, start, end, metric, scope, 1)
            if ranking.empty:
                raise ValueError(f"Empty production ranking: {scope}/{metric}")
        print(f"{scope}: median={values['current_median']:.2f} minutes; "
              f"qualified events={values['current_events']:,}")
    print("Production table and response KPI smoke test passed.")


if __name__ == "__main__":
    main()
