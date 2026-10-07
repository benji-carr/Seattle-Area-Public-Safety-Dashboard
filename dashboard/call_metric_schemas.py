"""Compact calls input contracts, before event aggregation or reconciliation."""

import pandera.pandas as pa

from dashboard.spd_config import ARRIVAL_TIME_COLUMN, EVENT_ID_COLUMN, TIME_COLUMN


SOURCE_COLUMNS = [EVENT_ID_COLUMN, TIME_COLUMN, ARRIVAL_TIME_COLUMN, "priority", "dispatch_neighborhood"]
LATEST_TIME_COLUMN = "latest_queued_time"


def call_metric_source_schema(*, require_latest=True):
    columns = {name: pa.Column(nullable=True) for name in SOURCE_COLUMNS}
    columns[EVENT_ID_COLUMN] = pa.Column(checks=pa.Check(
        lambda values: values.astype("string").str.strip().ne(""), error="invalid event ID"))
    if require_latest:
        columns[LATEST_TIME_COLUMN] = pa.Column(nullable=True)
    # Repeated IDs are essential for fallback rows and independent first values.
    return pa.DataFrameSchema(columns)


def call_metric_times_schema(*, require_latest=True):
    columns = {TIME_COLUMN: pa.Column(), ARRIVAL_TIME_COLUMN: pa.Column(nullable=True)}
    checks = None
    if require_latest:
        columns[LATEST_TIME_COLUMN] = pa.Column()
        checks = pa.Check(lambda frame: ~frame[LATEST_TIME_COLUMN].lt(frame[TIME_COLUMN]),
                          error="maximum queued time precedes minimum queued time")
    return pa.DataFrameSchema(columns, checks=checks)


AGGREGATE_COUNTS_SCHEMA = pa.DataFrameSchema(
    {name: pa.Column(checks=[pa.Check.in_range(0, 2**53 - 1),
                            pa.Check(lambda values: values.mod(1).eq(0))])
     for name in ("source_row_count", "dispatch_id_count")},
    checks=pa.Check(lambda frame: frame.source_row_count.ge(1)
                    & frame.dispatch_id_count.le(frame.source_row_count),
                    error="Invalid aggregate dispatch counts"),
)
