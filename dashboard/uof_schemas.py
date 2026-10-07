"""Separate stored UOF column contract from deduplicated refresh output."""

import pandera.pandas as pa

from dashboard.uof_query import ID_COLUMN, TIME_COLUMN, UOF_COLUMNS


UOF_SNAPSHOT_SCHEMA = pa.DataFrameSchema(
    {name: pa.Column(nullable=True) for name in UOF_COLUMNS},
    strict=True, ordered=True, unique_column_names=True,
)
UOF_REFRESH_INPUT_SCHEMA = pa.DataFrameSchema({
    ID_COLUMN: pa.Column(nullable=True), TIME_COLUMN: pa.Column(nullable=True),
})
UOF_IDENTIFIERS_SCHEMA = pa.DataFrameSchema({ID_COLUMN: pa.Column()})
UOF_NORMALIZED_SCHEMA = pa.DataFrameSchema({
    **{name: pa.Column(nullable=True) for name in UOF_COLUMNS},
    ID_COLUMN: pa.Column("string", unique=True),
}, strict=True, ordered=True, unique_column_names=True)
