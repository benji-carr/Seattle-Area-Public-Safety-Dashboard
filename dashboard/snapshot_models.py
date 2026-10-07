"""Metadata presence contracts. Readers return the original, uncoerced JSON.

Any is intentional for legacy fields that were only required to be present.
Cross-file equality checks remain in the snapshot readers. Unknown/optional
provenance is allowed and is never removed or filled with defaults.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator


class SnapshotMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    refreshed_at_utc: Any
    row_count: Any
    columns: Any


class CallsSnapshotMetadata(SnapshotMetadata):
    source_start_date: Any


class CrimeSnapshotMetadata(CallsSnapshotMetadata):
    source_date_column: Any


class CompactCallsMetadata(BaseModel):
    metric_schema_version: StrictInt

    @field_validator("metric_schema_version")
    @classmethod
    def supported_version(cls, value):
        if value != 1:
            raise ValueError("expected metric_schema_version 1")
        return value


class UOFSnapshotMetadata(CallsSnapshotMetadata):
    source_dataset_id: Literal["ppi5-g2bj"]
    source_end_date: Any


class PopulationQA(BaseModel):
    city_population: Any
    raw_mcpp_total: Any
    calibrated_mcpp_total: Any
    calibration_factor: Any
    mcpp_count: Any
    mcpps_represented: Any
    assigned_2020_population: Any
    assigned_block_count: Any
    unmatched_acs_blocks: Any
    zero_weight_block_groups: Any
    raw_reconciliation_difference: Any
    raw_reconciliation_percentage: Any


class PopulationSnapshotMetadata(SnapshotMetadata, PopulationQA):
    acs_year: Any
    population_variable: Any
    population_moe_variable: Any
    census_block_vintage: Any
