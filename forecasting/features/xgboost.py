from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandera.errors import SchemaError, SchemaErrors

from forecasting.features.table_schemas import TARGET_COLUMNS_SCHEMA, TARGET_PANEL_SCHEMA, feature_panel_schema

from forecasting.features.calendar import (
    build_calendar_features,
)


TARGET_COLUMN = "calls"

LAG_DAYS = [
    1,
    2,
    3,
    7,
    14,
    21,
    28,
]

ROLLING_WINDOWS = [
    7,
    14,
    28,
]


TARGET_HISTORY_FEATURES = [
    *[
        f"calls_lag_{lag}"
        for lag in LAG_DAYS
    ],

    *[
        f"calls_rolling_mean_{window}"
        for window in ROLLING_WINDOWS
    ],

    "calls_rolling_std_7",
    "calls_rolling_std_28",
]


CALENDAR_XGB_FEATURES = [
    "is_weekend",
    "week_of_year_sin",
    "week_of_year_cos",
]


XGB_FEATURE_SETS = {
    "lags_only": [
        *[
            f"calls_lag_{lag}"
            for lag in LAG_DAYS
        ],
    ],

    "lags_rolling": [
        *TARGET_HISTORY_FEATURES,
    ],

    "lags_rolling_calendar": [
        *TARGET_HISTORY_FEATURES,
        *CALENDAR_XGB_FEATURES,
    ],
}


MODELING_KEYS = [
    "target_date",
    "neighborhood",
]


def prepare_target_panel(
    target_panel: pd.DataFrame,
) -> pd.DataFrame:
    """
    Validate and normalize the neighborhood-level daily target panel.
    """

    try:
        TARGET_COLUMNS_SCHEMA.validate(target_panel)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Target panel is missing required columns: {error}") from error

    df = target_panel.copy()

    df["target_date"] = pd.to_datetime(
        df["target_date"],
        errors="coerce",
    ).dt.normalize()

    df["neighborhood"] = (
        df["neighborhood"]
        .astype("string")
        .str.strip()
    )

    df[TARGET_COLUMN] = pd.to_numeric(
        df[TARGET_COLUMN],
        errors="coerce",
    )

    df = df.loc[
        df["neighborhood"].notna()
        & df["neighborhood"].ne("")
        & df["neighborhood"].ne("NULL")
    ].copy()

    try:
        TARGET_PANEL_SCHEMA.validate(df)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"Target panel contains invalid target dates, missing/non-numeric or negative calls, or duplicate date/neighborhood rows: {error}") from error

    df = (
        df
        .sort_values(
            [
                "neighborhood",
                "target_date",
            ]
        )
        .reset_index(drop=True)
    )

    return df


def validate_daily_panel(
    target_panel: pd.DataFrame,
) -> None:
    """
    Ensure every neighborhood contains one observation for every
    calendar day over the common date range.
    """

    min_date = (
        target_panel[
            "target_date"
        ].min()
    )

    max_date = (
        target_panel[
            "target_date"
        ].max()
    )

    expected_dates = pd.date_range(
        min_date,
        max_date,
        freq="D",
    )

    expected_count = len(
        expected_dates
    )

    counts = (
        target_panel
        .groupby(
            "neighborhood"
        )["target_date"]
        .nunique()
    )

    bad_counts = counts.loc[
        counts.ne(
            expected_count
        )
    ]

    if not bad_counts.empty:
        raise ValueError(
            "Some neighborhoods do not contain "
            "the complete daily date range:\n"
            f"{bad_counts}"
        )

    expected_set = set(
        expected_dates
    )

    for (
        neighborhood,
        group,
    ) in target_panel.groupby(
        "neighborhood"
    ):
        actual_set = set(
            group["target_date"]
        )

        if actual_set != expected_set:
            raise ValueError(
                f"{neighborhood!r} has missing "
                "or unexpected dates."
            )


def add_lag_features(
    target_panel: pd.DataFrame,
    lags: list[int] = LAG_DAYS,
) -> pd.DataFrame:
    """
    Add past target values.

    calls_lag_k for target date t contains calls from t-k.

    Because shift() only moves observations from earlier dates
    forward, these features contain no future target information.
    """

    df = target_panel.copy()

    grouped_calls = (
        df
        .groupby(
            "neighborhood",
            sort=False,
        )[TARGET_COLUMN]
    )

    for lag in lags:
        df[
            f"calls_lag_{lag}"
        ] = (
            grouped_calls
            .shift(lag)
        )

    return df


def add_rolling_features(
    target_panel: pd.DataFrame,
    windows: list[int] = ROLLING_WINDOWS,
) -> pd.DataFrame:
    """
    Add rolling statistics based only on observations available
    before the target date.

    IMPORTANT:
        shift(1) occurs BEFORE rolling().

    Therefore the target day's own call count is never included.
    """

    df = target_panel.copy()

    shifted_calls = (
        df
        .groupby(
            "neighborhood",
            sort=False,
        )[TARGET_COLUMN]
        .shift(1)
    )

    for window in windows:
        df[
            f"calls_rolling_mean_{window}"
        ] = (
            shifted_calls
            .groupby(
                df["neighborhood"],
                sort=False,
            )
            .transform(
                lambda series:
                series.rolling(
                    window=window,
                    min_periods=window,
                ).mean()
            )
        )

    for window in [
        7,
        28,
    ]:
        df[
            f"calls_rolling_std_{window}"
        ] = (
            shifted_calls
            .groupby(
                df["neighborhood"],
                sort=False,
            )
            .transform(
                lambda series:
                series.rolling(
                    window=window,
                    min_periods=window,
                ).std()
            )
        )

    return df


def add_calendar_features(
    feature_panel: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge the calendar variables that showed the strongest
    incremental SARIMAX value.
    """

    min_date = (
        feature_panel[
            "target_date"
        ].min()
    )

    max_date = (
        feature_panel[
            "target_date"
        ].max()
    )

    calendar = build_calendar_features(
        min_date,
        max_date,
    )

    calendar = calendar[
        [
            "target_date",
            *CALENDAR_XGB_FEATURES,
        ]
    ].copy()

    df = feature_panel.merge(
        calendar,
        on="target_date",
        how="left",
        validate="many_to_one",
    )

    if (
        df[
            CALENDAR_XGB_FEATURES
        ]
        .isna()
        .any()
        .any()
    ):
        raise ValueError(
            "Calendar merge introduced missing values."
        )

    return df


def merge_external_features(
    feature_panel: pd.DataFrame,
    external_panel: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    """
    Strictly merge external predictors onto the existing
    date x neighborhood supervised-learning panel.
    """

    missing_external_columns = (
        set(feature_columns)
        - set(external_panel.columns)
    )

    if missing_external_columns:
        raise ValueError(
            "External panel is missing requested columns: "
            f"{sorted(missing_external_columns)}"
        )

    left = feature_panel.copy()
    right = external_panel.copy()

    for frame_name, frame in [
        ("feature_panel", left),
        ("external_panel", right),
    ]:
        if not set(MODELING_KEYS).issubset(
            frame.columns
        ):
            raise ValueError(
                f"{frame_name} is missing modeling keys."
            )

        frame["target_date"] = pd.to_datetime(
            frame["target_date"],
            errors="coerce",
        ).dt.normalize()

        frame["neighborhood"] = (
            frame["neighborhood"]
            .astype("string")
            .str.strip()
        )

        if frame[
            MODELING_KEYS
        ].isna().any().any():
            raise ValueError(
                f"{frame_name} contains invalid modeling keys."
            )

        duplicate_count = (
            frame
            .duplicated(
                subset=MODELING_KEYS
            )
            .sum()
        )

        if duplicate_count:
            raise ValueError(
                f"{frame_name} contains duplicate "
                "date/neighborhood keys."
            )

    left = left.reset_index(
        drop=False
    ).rename(
        columns={"index": "_merge_order"}
    )

    original_keys = left[
        MODELING_KEYS
    ].copy()

    merged = left.merge(
        right[
            [
                *MODELING_KEYS,
                *feature_columns,
            ]
        ],
        on=MODELING_KEYS,
        how="left",
        validate="one_to_one",
    )

    if len(merged) != len(left):
        raise ValueError(
            "External merge changed row count."
        )

    if not merged[
        MODELING_KEYS
    ].equals(original_keys):
        raise ValueError(
            "External merge changed modeling keys."
        )

    if (
        merged[feature_columns]
        .isna()
        .any()
        .any()
    ):
        raise ValueError(
            "External merge introduced missing "
            "feature coverage within the experiment period."
        )

    merged = (
        merged
        .sort_values("_merge_order")
        .drop(columns="_merge_order")
        .reset_index(drop=True)
    )

    return merged


def build_xgboost_feature_panel(
    target_panel: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the full supervised-learning panel.

    Rows at the beginning of each neighborhood series contain
    missing lag/rolling values by design. They are removed only
    after all features are constructed.
    """

    df = prepare_target_panel(
        target_panel
    )

    validate_daily_panel(
        df
    )

    df = add_lag_features(
        df
    )

    df = add_rolling_features(
        df
    )

    df = add_calendar_features(
        df
    )

    required_history_features = (
        TARGET_HISTORY_FEATURES
    )

    df = df.dropna(
        subset=
            required_history_features
    ).copy()

    df = (
        df
        .sort_values(
            [
                "target_date",
                "neighborhood",
            ]
        )
        .reset_index(drop=True)
    )

    return df


def validate_xgboost_feature_panel(
    feature_panel: pd.DataFrame,
) -> None:
    """
    Validate the final machine-learning feature matrix.
    """

    try:
        feature_panel_schema([*TARGET_HISTORY_FEATURES, *CALENDAR_XGB_FEATURES]).validate(feature_panel)
    except (SchemaError, SchemaErrors) as error:
        raise ValueError(f"XGBoost feature panel is empty or contains missing columns/values, duplicate modeling keys, or non-finite predictor values: {error}") from error


def save_xgboost_feature_panel(
    target_panel: pd.DataFrame,
    output_path: str | Path,
) -> pd.DataFrame:
    output_path = Path(
        output_path
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    features = (
        build_xgboost_feature_panel(
            target_panel
        )
    )

    validate_xgboost_feature_panel(
        features
    )

    features.to_parquet(
        output_path,
        index=False,
    )

    return features
