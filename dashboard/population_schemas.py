"""Population tables retain nullable city counts and unmatched ACS estimates."""

import numpy as np
import pandera.pandas as pa


POPULATION_COLUMNS = [
    "geography_type", "geography_name", "population", "population_raw",
    "population_year", "source", "source_vintage", "estimation_method",
    "census_blocks", "source_block_groups",
]
POPULATION_SNAPSHOT_SCHEMA = pa.DataFrameSchema(
    {name: pa.Column(nullable=True) for name in POPULATION_COLUMNS},
    strict=True, ordered=True, unique_column_names=True, unique=["geography_type", "geography_name"],
)
NONNEGATIVE_POPULATION = pa.SeriesSchema(checks=[pa.Check(np.isfinite), pa.Check.ge(0)])
ACS_GEOIDS_SCHEMA = pa.DataFrameSchema({"bg_geoid": pa.Column("string", unique=True)})
BLOCK_GEOIDS_SCHEMA = pa.DataFrameSchema({"block_geoid": pa.Column("string", unique=True)})


def fips_schema(width):
    return pa.SeriesSchema("string", checks=pa.Check.str_matches(r"^[0-9]{1," + str(width) + r"}$"))
