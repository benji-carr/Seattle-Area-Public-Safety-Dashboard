"""Legacy raw snapshot refresh boundaries, with uniqueness only after deduplication."""

import pandera.pandas as pa

from dashboard.spd_config import ROW_ID_COLUMN, TIME_COLUMN


CRIME_TIME_SCHEMA = pa.DataFrameSchema({
    "offense_date": pa.Column(nullable=True), "report_date_time": pa.Column(nullable=True),
})
CRIME_INCREMENTAL_SCHEMA = CRIME_TIME_SCHEMA.add_columns({"offense_id": pa.Column(nullable=True)})
CRIME_DEDUPLICATED_SCHEMA = CRIME_TIME_SCHEMA.add_columns({
    "offense_id": pa.Column(nullable=True, unique=True),
})
CALLS_TIME_SCHEMA = pa.DataFrameSchema({TIME_COLUMN: pa.Column(nullable=True)})
CALLS_INCREMENTAL_SCHEMA = CALLS_TIME_SCHEMA.add_columns({ROW_ID_COLUMN: pa.Column(nullable=True)})
CALLS_DEDUPLICATED_SCHEMA = CALLS_TIME_SCHEMA.add_columns({ROW_ID_COLUMN: pa.Column(nullable=True, unique=True)})
