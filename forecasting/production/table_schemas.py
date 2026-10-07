"""Production boundaries after existing source parsing and feature selection."""

import pandera.pandas as pa

from forecasting.features.table_schemas import FINITE_PREDICTOR

SOURCE_SCHEMA = pa.DataFrameSchema({name: pa.Column(nullable=True) for name in (
    "cad_event_number", "cad_event_original_time_queued", "dispatch_neighborhood",
)})
SOURCE_TIMES_SCHEMA = pa.SeriesSchema()


def training_columns_schema(numeric_features):
    return pa.DataFrameSchema({name: pa.Column(nullable=True) for name in (
        "target_date", "neighborhood", "calls", *numeric_features,
    )})


def training_schema(numeric_features):
    return pa.DataFrameSchema({
        "target_date": pa.Column(nullable=True),
        "neighborhood": pa.Column(checks=pa.Check.ne("")),
        "calls": pa.Column(),
        **{name: FINITE_PREDICTOR for name in numeric_features},
    }, unique=["target_date", "neighborhood"])
