import pandas as pd
import pytest

from dashboard.crime_controls import crime_chart_dates
from dashboard import crime_dashboard_figures, spd_dashboard_figures
from dashboard.crime_dashboard_data import (
    EVENT_ID_COLUMN as CRIME_EVENT_ID_COLUMN,
    ROW_ID_COLUMN as CRIME_ROW_ID_COLUMN,
    TIME_COLUMN as CRIME_TIME_COLUMN,
)
from dashboard.spd_config import (
    EVENT_ID_COLUMN as CALL_EVENT_ID_COLUMN,
    ROW_ID_COLUMN as CALL_ROW_ID_COLUMN,
    TIME_COLUMN as CALL_TIME_COLUMN,
)


LATEST_DAY = pd.Timestamp("2026-08-31")


@pytest.mark.parametrize(
    ("figure_module", "context", "selected_bins"),
    [
        (
            spd_dashboard_figures,
            {
                "valid_time": pd.DataFrame(
                    {
                        CALL_TIME_COLUMN: pd.date_range(
                            "2026-08-29",
                            periods=3,
                            freq="D",
                        ),
                        CALL_EVENT_ID_COLUMN: [1, 2, 3],
                        CALL_ROW_ID_COLUMN: [11, 12, 13],
                        "event_importance_bin": ["property/nonviolent"] * 3,
                    }
                )
            },
            ["property/nonviolent"],
        ),
        (
            crime_dashboard_figures,
            {
                "valid_time": pd.DataFrame(
                    {
                        CRIME_TIME_COLUMN: pd.date_range(
                            "2026-08-29",
                            periods=3,
                            freq="D",
                        ),
                        CRIME_EVENT_ID_COLUMN: [1, 2, 3],
                        CRIME_ROW_ID_COLUMN: [11, 12, 13],
                        "event_importance_bin": ["crimes against persons"] * 3,
                    }
                )
            },
            ["crimes against persons"],
        ),
    ],
)
def test_daily_figures_expose_native_one_day_range_selector(
    figure_module,
    context,
    selected_bins,
):
    figure = figure_module.make_daily_figure(context, selected_bins)
    buttons = figure.layout.xaxis.rangeselector.buttons
    assert [button.label for button in buttons] == ["1D", "1W", "1M", "1Y"]
    assert buttons[0].count == 1
    assert buttons[0].step == "day"
    assert buttons[0].stepmode == "backward"
    if figure_module is crime_dashboard_figures:
        assert buttons[1].count == 7
        assert buttons[1].step == "day"
        assert buttons[2].count == 1
        assert buttons[2].step == "month"
        assert buttons[3].count == 1
        assert buttons[3].step == "year"
    else:
        assert buttons[1].count == 6
        assert buttons[2].count == 29
        assert buttons[3].step == "year"
        assert buttons[3].count == 1
        assert buttons[3].stepmode == "backward"
    assert figure.layout.xaxis.rangeslider.visible is True
    assert figure.layout.xaxis.range[1] == LATEST_DAY
    assert figure.layout.xaxis.range[0] < figure.layout.xaxis.range[1]


def test_native_one_day_viewport_maps_to_its_ending_calendar_day():
    start_date, end_date = crime_chart_dates(
        relayout={
            "xaxis.range[0]": "2026-08-30 00:00:00",
            "xaxis.range[1]": "2026-08-31 00:00:00",
        },
        earliest="2026-08-30",
        latest="2026-08-31",
    )

    assert (start_date, end_date) == ("2026-08-31", "2026-08-31")


def test_manual_multi_day_viewport_clamps_map_dates():
    start_date, end_date = crime_chart_dates(
        relayout={
            "xaxis.range": [
                "2026-08-27 00:00:00",
                "2026-08-31 00:00:00",
            ]
        },
        earliest="2026-08-30",
        latest="2026-08-31",
    )

    assert (start_date, end_date) == ("2026-08-30", "2026-08-31")
