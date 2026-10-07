"""Target and feature table contracts; parsing and chronological checks stay explicit."""

import numpy as np
import pandera.pandas as pa


TARGET_COLUMNS_SCHEMA = pa.DataFrameSchema({
    name: pa.Column(nullable=True) for name in ("target_date", "neighborhood", "calls")
})
TARGET_PANEL_SCHEMA = pa.DataFrameSchema({
    "target_date": pa.Column(),
    "neighborhood": pa.Column("string"),
    "calls": pa.Column(checks=pa.Check.ge(0)),
}, unique=["target_date", "neighborhood"])
FINITE_PREDICTOR = pa.Column(checks=pa.Check(
    lambda values: np.isfinite(values.astype(float)), error="non-finite numeric model inputs",
))


def feature_panel_schema(numeric_features):
    return pa.DataFrameSchema({
        **{name: pa.Column(nullable=True) for name in ("target_date", "neighborhood", "calls")},
        **{name: FINITE_PREDICTOR for name in numeric_features},
    }, unique=["target_date", "neighborhood"], checks=pa.Check(lambda frame: not frame.empty))
