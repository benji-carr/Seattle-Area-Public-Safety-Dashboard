# v1.1 metric definitions

Production reference audited against staging `9d67934` on 2026-09-15. This describes current data contracts and explicitly identifies pending KPI behavior. See the [figure docket](v1_1_figure_docket.md) for implementation work and the [audit](v1_1_pre_figure_audit.md) for validation.

## Analysis periods and comparisons

| Contract | Definition / current behavior |
| --- | --- |
| Latest available data | Anchor to the latest valid event date in the relevant unfiltered analytical stream, not today or the latest event matching a category/neighborhood filter. Crime uses `valid_time.offense_date`; CAD uses `valid_time.cad_event_original_time_queued`. UOF exposes `latest_available_date` from the full UOF snapshot, not just OIS rows. Streams have different end dates. |
| Selectable domain | `get_analysis_bounds(latest)` returns `[latest - DateOffset(years=1), latest]`. Both endpoints count; this is a calendar-year viewport, not a fixed 365-day count. Retained older history remains available for comparisons. |
| Current selected period | Inclusive calendar dates `[S, E]`. Crime state contains `start_date`, `end_date`, `crime_categories`, `crime_subcategories`, `neighborhoods`. Crime and calls initially select their latest single day. UOF controls are not yet wired. |
| Previous period | Let `D = (E - S).days + 1`. Previous bounds are `[S - D days, S - 1 day]`: immediately adjacent, equally long, with no overlap. Apply the same metric definition and dimension filters in both periods. |
| Coverage | `get_previous_period(..., history_bounds=...)` returns `None` when full previous-period bounds are unavailable; never truncate the previous period. Use unfiltered retained history bounds. Bounds alone do not prove there are no acquisition gaps; callers must check known gaps. |
| Date boundaries | Normalize to calendar days before filtering, so the whole end day is included. General window helpers preserve the supplied local calendar date. UOF additionally converts timezone-aware inputs to Seattle time. |
| Change display (pending KPI code) | Absolute change `current - previous`; percentage change `100 * (current - previous) / previous`. When `previous == 0`, percentage change is undefined, including `0 -> 0`; display unavailable, not zero or infinity. Missing comparison coverage also means unavailable. These display calculations are not implemented in `analysis_windows.py`. |

All period metrics below use these comparison rules. Counts and medians are recomputed separately for each period. Rates use the same loaded population vintage for both periods; population denominators themselves have no selected-period comparison. An empty response sample has no median, rather than a zero-minute response.

Authority: [analysis_windows.py](../dashboard/analysis_windows.py), [crime_controls.py](../dashboard/crime_controls.py), [crime_filters.py](../dashboard/crime_filters.py), and `app.py`.

### Verification

References below are repository-relative pytest selectors; parametrized functions include all their cases. Each description identifies the contract asserted, not blanket coverage of the section.

- `tests/dashboard/test_analysis_windows.py::test_native_year_and_equal_adjacent_periods` — verifies latest-date calendar-year bounds, inclusive day counts (including leap years), equal-length adjacent periods without overlap, and rejection of insufficient previous-period history.
- `tests/dashboard/test_analysis_windows.py::test_single_day_invalid_and_empty_history` — verifies single-day comparison, reversed-period rejection, empty history rejection, normalized history endpoints, and rejection when history ends too early.
- `tests/dashboard/test_analysis_windows.py::test_actual_retention_supports_complete_leap_comparison` — exercises crime/CAD incremental retention with synthetic history: production defaults retain the full leap-year comparison, including midnight and nonmidnight anchors; shorter lookbacks demonstrate lost coverage. It does not certify any live snapshot.
- `tests/dashboard/test_analysis_windows.py::test_notebook_period_examples_match_production` — executes the notebook's latest-year and leap-year period examples and compares their bounds with production helpers; it does not validate other notebook methodology.
- `tests/dashboard/test_analysis_window_figures.py::test_native_year_hides_history_without_losing_equal_previous_period` — verifies crime/CAD figures show only the selectable year while preserving comparison history; identically filtered current/previous selections remain equal-length and disjoint.
- `tests/dashboard/test_crime_controls.py::test_native_one_day_chart_range_becomes_latest_single_day` — verifies the native 24-hour chart viewport becomes one analytical calendar day.

**Coverage limits:** the crime daily-filter test in the next section verifies that filtering cannot move the latest-date anchor. Percentage-change displays (including `previous == 0`) and same-vintage current/previous rate calculations remain pending KPI behavior, without direct regression coverage.

## Crime

Source: Seattle SPD crime stream `tazs-3rd5`, loaded from `data/processed/crime/crime_data.parquet`. Analytical identifier: **`offense_id`**. `report_number` identifies a report and is not interchangeable: one report can contain multiple offenses.

Common inclusion: classified rows with `is_excluded_from_crime_analysis == False`, a non-null `offense_id`, and a valid `offense_date` in the period. `valid_time` provides this population; the full context `df` retains excluded rows for QA. Classification uses reviewed `(offense_sub_category, nibrs_offense_code)` decisions, then source-category fallback. Explicit `not_a_crime` semantics override inclusion; code `999` alone does not exclude a record. Coordinates are not an analytical inclusion requirement.

| Metric | Grain / numerator | Denominator / formula | Qualification and interpretation |
| --- | --- | --- | --- |
| Citywide offense count | Distinct `offense_id` in the selected period | None | All analytical neighborhoods, including unknown or unmappable records. This counts reported offenses, not reports, calls, victims, or map points. |
| Top-level crime category count | Distinct `offense_id` within each canonical `offense_category` | None | Crimes Against Persons; Crimes Against Property; Crimes Against Society / Other. Stored values are lowercase. Uses the same inclusion rules as the overall count. |
| Neighborhood offense count | Distinct `offense_id` by analytical `mcpp_neighborhood` | None | A non-null analytical name must belong to the normalized authoritative MCPP boundary vocabulary from `load_mcpp_boundaries()`. Valid spatial assignment wins; only when spatial assignment is missing or invalid may a valid normalized source `neighborhood` supply the fallback. Otherwise geography is null/unassigned. Unassigned offenses remain in citywide analytical totals. |
| Citywide crime rate per 100,000 | Citywide offense count | `count / city_population * 100000` | Direct Seattle ACS denominator; selected-period rate, **not annualized**. Do not divide a neighborhood-filtered numerator by the full city population and label it citywide. KPI aggregation is pending. |
| Neighborhood crime rate per 100,000 | Neighborhood offense count | `count / neighborhood_population.population * 100000` | Use calibrated MCPP population with a matching normalized name and positive denominator. Missing/nonpositive population means unavailable rate. Apply the 5,000-person cutoff only to comparative population-adjusted rankings. |

Date field for every crime metric: `offense_date`, not `report_date_time`. Category/subcategory selections can narrow counts; a citywide label requires no neighborhood restriction. Point text search is a presentation filter and does not change analytical totals.

**Analytical geography contract:** `resolve_analytical_mcpp_neighborhood()` normalizes both spatial and source candidates using the same normalization as boundary names: trim, lowercase, replace `&` with `and`, and collapse repeated whitespace. Both candidates must pass boundary-vocabulary membership validation; non-null alone is insufficient. No authoritative source-to-MCPP alias layer exists in the repository, so unrecognized legacy labels, typos, and placeholders become null rather than invented mappings. `valid_time.mcpp_neighborhood.notna()` is sufficient evidence of a legitimate MCPP assignment. Coordinate validity does not determine analytical MCPP assignment: a missing/invalid-coordinate offense can receive a valid source fallback, while a coordinate-valid offense can remain geographically unassigned. Point plotting still requires usable coordinates. Choropleth and neighborhood aggregation use only legitimate MCPP assignments; geographically unassigned offenses remain represented in citywide counts and, when otherwise eligible, in `unmappable_events`.

**Existing map qualification:** the choropleth currently calculates selected-period rates **per 1,000**, using `event_mcpp` plus `unmappable_events`. A coordinate-valid offense with no MCPP match is already in the map event-ID set, so it does not enter `unmappable_events`; its source-neighborhood fallback exists in `valid_time` only. Consequently, current map totals can differ from analytical neighborhood totals. Reuse `valid_time` for KPI/ranking aggregation and reconcile the map during figure adaptation. Map points additionally require coordinates within the loader's Seattle bounds and are deduplicated by `offense_id`.

Authority: [crime_dashboard_data.py](../dashboard/crime_dashboard_data.py), [crime_classification.py](../dashboard/crime_classification.py), [crime_classification_decisions.py](../dashboard/crime_classification_decisions.py), [crime_dashboard_figures.py](../dashboard/crime_dashboard_figures.py).

### Verification

- `tests/dashboard/test_crime_classification.py::test_reviewed_pair_overrides_source_defaults` — verifies reviewed subcategory/code pairs override contradictory source categories after text normalization, retaining source evidence.
- `tests/dashboard/test_crime_classification.py::test_default_mapping` — verifies unreviewed source-category fallback, including missing sources and already canonical categories.
- `tests/dashboard/test_crime_classification.py::test_reviewed_exclusions_and_semantics_override_inclusion` — verifies reviewed exclusions and normalized `not_a_crime` semantics overriding an otherwise included pair.
- `tests/dashboard/test_crime_classification.py::test_no_unreviewed_exclusions_or_code_only_rules` — verifies `999` alone and the tested unreviewed pairs do not cause exclusion or code-only reclassification.
- `tests/dashboard/test_crime_classification.py::test_context_reconciliation_exclusions_and_unmappable_analysis` — verifies all three canonical categories, exclusion from analytical derivatives, preservation of missing/invalid-coordinate offenses in analysis, and duplicate-offense accounting in daily totals.
- `tests/dashboard/test_crime_analysis.py::test_common_filter_keeps_unmappable_and_includes_entire_end_day` — verifies combined category/subcategory/neighborhood/date filtering retains an unmappable offense and a late end-day offense.
- `tests/dashboard/test_crime_analysis.py::test_empty_dimensions_mean_all_and_multiple_neighborhoods_are_union` — verifies empty dimension filters mean all and multiple neighborhood selections form a union.
- `tests/dashboard/test_crime_analysis.py::test_daily_filter_retains_unmappable_and_history_for_navigation` — verifies filtered daily totals include unmappable offenses, preserve the unfiltered latest-date navigation anchor, and become zero for a nonmatching subcategory.
- `tests/dashboard/test_crime_analysis.py::test_map_shading_and_points_share_analysis_but_text_only_filters_points` — verifies shared analytical filters affect shading/points, while text search changes only points and preserves choropleth values.
- `tests/dashboard/test_crime_controls.py::test_neighborhood_options_do_not_exclude_citywide_records` — verifies unusable neighborhood choices are hidden without removing their offenses from unfiltered analytical/daily totals; named selections match normalized neighborhoods.

- `tests/dashboard/test_crime_geography_contract.py::test_resolution_domain_precedence_and_fallback` — verifies normalized boundary membership, spatial precedence, accepted source fallback, stale spatial recovery, and null assignment for invalid candidates.
- `tests/dashboard/test_crime_geography_contract.py::test_resolution_preserves_rows_index_and_single_assignment` — verifies resolution preserves the original index and offense rows, including duplicate rows, with one assignment per offense; empty inputs and vocabulary are covered.
- `tests/dashboard/test_crime_geography_contract.py::test_context_canonical_domain_and_offense_reconciliation` — verifies the real context-loading path rejects invalid source geography and reconciles unique analytical offenses into disjoint recognized/unassigned sets, including multiple offenses sharing a report.
- `tests/dashboard/test_crime_geography_contract.py::test_coordinate_independence_and_unmappable_row_preservation` — verifies source fallback without usable coordinates, unassigned coordinate-valid offenses, spatial-only point assignments, and preservation of unmappable offenses with null geography.
- `tests/dashboard/test_crime_geography_contract.py::test_neighborhood_controls_consume_clean_context_without_changing_citywide` — verifies controls consume the upstream contract and unfiltered citywide offenses remain represented. Existing defensive UI exclusions remain in place.
- `tests/dashboard/test_crime_geography_contract.py::test_spatial_lookup_validates_cached_and_new_assignments` — verifies both cached and newly generated spatial lookups expose only normalized boundary names or null, without dropping offenses.

**Coverage limits:** planned per-100,000 KPI/ranking calculations are not directly tested.

## CAD / qualified response

Source: Seattle CAD stream `33kz-ixgy`, loaded from `data/processed/spd_calls.parquet`. The event-level production calculation is `build_response_analysis()` in [spd_dashboard_data.py](../dashboard/spd_dashboard_data.py); the calls scatter consumes its `response_analysis` result.

| Metric / contract | Grain / numerator or statistic | Denominator | Date, qualification, interpretation |
| --- | --- | --- | --- |
| Qualified response event | One `cad_event_number`; `response_time_minutes = (first_arrival_time - queued_time).total_seconds() / 60` | None | Group records with non-null event IDs after sorting by original queue time. `queued_time = min(cad_event_original_time_queued)`; `first_arrival_time = min(cad_event_arrived_time)` across dispatch rows. Keep nonmissing durations **0 through 1,440 minutes inclusive**. Missing/negative/longer durations are excluded. Coordinates are unnecessary. Select periods by aggregated `queued_time`. |
| Citywide median qualified response time | Median of `response_time_minutes` across selected qualified CAD events | No population denominator; one observation per event | Includes events without usable neighborhoods/coordinates. A median describes the middle observed queue-to-arrival duration; it is not dispatch-to-arrival travel time. Citywide KPI is pending. |
| Neighborhood median qualified response time | Median of qualified event durations grouped by `dispatch_neighborhood` | No population denominator | Same queue-date selection. Requires a usable neighborhood for grouping. Current scatter excludes `unknown`, `-`, empty string and `nan`; pandas grouping also drops null names. This uses source dispatch names, not spatial MCPP assignment. Ranking UI is pending. |
| Priority handling | Event `priority` is the first non-null value in queue-time-sorted group order | None | Coerced numeric by snapshot preparation. No priority whitelist, priority-specific cutoff, or priority weighting exists in `build_response_analysis()`. Missing priority alone does not disqualify an event. Do not substitute a priority-1-only or priorities-1-to-3 rule from research. |

`event_group` and `dispatch_neighborhood` likewise use group aggregation `first` (first non-null per column, potentially from different rows). `dispatch_records` counts distinct `call_sign_dispatch_id`; it does not weight medians. Equal queue-time ties have no explicit secondary ordering. The loader does not filter `call_type` or `cad_event_response_category` and does not impose a minimum positive duration.

Existing presentation filters use CAD `event_importance_bin`, derived from `event_group`. Default target bins are `property/nonviolent`, `drug-related`, and `violent/person crime`; these are distinct from the canonical crime taxonomy. The context contains additional bins. No production cross-stream mapping connects crime subcategories to CAD events.

**Response inconsistency to preserve explicitly:** the CAD map in [spd_dashboard_figures.py](../dashboard/spd_dashboard_figures.py) calculates duration from a single coordinate-valid dispatch row retained per event, groups by spatial MCPP, and uses a fixed 365-day map window. It does not consume `response_analysis`. Its hover median is therefore not interchangeable with the event-level qualified median above. New qualified-response KPIs should consume the event-level context; map response text requires reconciliation if retained as the same metric.

**Ranking qualification is not settled in production.** The scatter requires at least **100 qualified events per neighborhood/bin** and positive matched population; this is a scatter rule, not a general median or response-ranking threshold. No response-ranking implementation establishes a minimum sample or tie policy. The methodology notebook's candidate 30-event threshold is research, not an authoritative production rule. Decide these before completing that ranking.

### Verification

- `tests/dashboard/test_analysis_window_figures.py::test_calls_scatter_uses_analysis_year_and_keeps_response_history` — supplies prebuilt response rows and verifies inclusive analysis-year selection, event count, median aggregation and preservation of older context rows. It sets `min_events=1`, so it does not verify the default 100-event cutoff.
- `tests/dashboard/test_population_dashboard_data.py::test_calls_map_accepts_both_neighborhood_aliases_without_changing_figure` — verifies population-adapter compatibility with the existing calls map, not correctness or equivalence of its response methodology. Population-context wiring is covered under Population denominators below.

**Not directly regression-tested:** `build_response_analysis()` event deduplication, earliest queue/arrival derivation, inclusive 0–1,440-minute qualification and missing/negative/long-duration exclusions, coordinate independence, priority policy, first-non-null grouping and dispatch-record weighting. The population-context test stubs this loader. Existing tests also do not establish map/loader equivalence, response-ranking qualification or a citywide median KPI. These remain documented production behavior or pending work, not contracts proven by the scatter/map tests.

## UOF / derived OIS

Source: Seattle SPD UOF stream `ppi5-g2bj`, loaded from `data/processed/uof/uof_data.parquet`. Source row key `uniqueid` is for snapshot deduplication, not incident/event counts.

| Metric | Grain / numerator | Denominator | Date and qualification | Interpretation |
| --- | --- | --- | --- | --- |
| UOF incident count | `nunique(incident_num)` via `count_uof_incidents()` | None | Inclusive `occured_date_time` calendar dates (source spelling retained). Strip IDs and exclude blank/null IDs; invalid dates cannot enter the period. No incident-type or geography restriction. | Multiple officer/subject/source records can belong to one incident. OIS-classified records are not excluded from UOF. |
| Derived OIS event count | `nunique(ois_event_key)` via `count_ois_events()` | None | Select UOF rows whose `incident_type` matches standalone, case-insensitive `\bOIS\b`. Require valid occurrence dates; group by local `event_date` plus `normalized_beat`. Select periods on `event_date`. | Operational day-and-beat events, not UOF rows, force incident numbers, people shot, or fatalities. Multiple same-day same-beat records collapse; different beats remain separate. |

Beat normalization strips whitespace and uppercases. Nulls and `""`, `-`, `OOJ`, `99`, `NAN`, `NONE`, `<NA>`, `NA`, `N/A` map to **`OUTSIDE_OR_UNKNOWN`**; other labels are retained without boundary validation. `ois_event_key` is the ISO event date, a pipe separator, and normalized beat. All outside/unknown encodings on the same day form one event. No geocoding requirement applies.

Naive UOF timestamps represent Seattle wall time; aware inputs convert to `America/Los_Angeles` before calendar grouping. Invalid-date OIS rows remain in `ois_rows` and are counted in `ois_rows_missing_event_date`, but cannot form events. Counts use the shared comparison contract; comparison cards and app integration are pending.

Authority: [uof_dashboard_data.py](../dashboard/uof_dashboard_data.py), [uof_snapshot.py](../dashboard/uof_snapshot.py), [uof_data.py](../dashboard/uof_data.py).

### Verification

- `tests/dashboard/test_uof_dashboard_data.py::test_ois_matching_requires_standalone_term` — verifies case-insensitive standalone `OIS` matching, rejecting embedded substrings, nulls and non-OIS labels.
- `tests/dashboard/test_uof_dashboard_data.py::test_outside_and_unknown_beats_normalize_together` — verifies trimming/case normalization and the tested null/unknown/outside encodings. The explicit `NA` and `N/A` tokens are not cases in this test.
- `tests/dashboard/test_uof_dashboard_data.py::test_multiple_rows_incidents_officers_subjects_and_times_form_one_event` — verifies same-day normalized-beat grouping, key construction, multiple source rows/incidents collapsing into one event, and row-order independence.
- `tests/dashboard/test_uof_dashboard_data.py::test_2015_09_29_distinct_beats_remain_two_events` — verifies distinct beats on the same synthetic event day remain separate events.
- `tests/dashboard/test_uof_dashboard_data.py::test_2024_04_17_outside_encodings_collapse_to_one_event` — verifies `-` with either `99` or `OOJ` collapses to one same-day outside/unknown event. These dated fixtures test grouping rules, not live snapshot counts.
- `tests/dashboard/test_uof_dashboard_data.py::test_local_calendar_dates_keep_naive_time_and_convert_aware_time` — verifies naive Seattle dates and UTC-to-Seattle conversion before event-day grouping.
- `tests/dashboard/test_uof_dashboard_data.py::test_counts_unique_incidents_and_events_with_inclusive_calendar_endpoints` — verifies distinct incident counts, blank incident-ID exclusion, deduplicated OIS event counts, inclusive start/late end-day selection, and reversed-period rejection.
- `tests/dashboard/test_uof_dashboard_data.py::test_non_ois_and_invalid_dates_do_not_form_events_and_empty_schema_is_stable` — verifies non-OIS/invalid-date rows form no events and empty derived data retains its schema/count behavior.
- `tests/dashboard/test_refresh_uof_data.py::test_initial_refresh_fetches_complete_history_and_deduplicates` — verifies an unbounded initial fetch and source-row deduplication by `uniqueid`, retaining the last copy.
- `tests/dashboard/test_uof_pipeline.py::test_snapshot_round_trip_and_context` — verifies snapshot/frame and metadata round-trip, source dataset/date provenance, and context exposure of the source dataframe and derived OIS key.
- `tests/dashboard/test_uof_pipeline.py::test_snapshot_rejects_inconsistent_metadata` — verifies rejection of row-count, column-list and source-dataset mismatches.

**Coverage limits:** the context round-trip does not assert `latest_available_date` against a later non-OIS record or verify `ois_rows_missing_event_date`. Invalid-date event exclusion is tested separately; comparison cards remain unimplemented.

## Population denominators

Source: `data/processed/population/population_estimates.parquet` plus `population_metadata.json`. `load_dashboard_population()` exposes `neighborhood_population`, `city_population`, and `population_metadata` in both crime and calls contexts.

| Contract | Grain / value and calculation | Qualification / interpretation |
| --- | --- | --- |
| Direct Seattle city denominator | One `geography_type == "city"`, `geography_name == "seattle"` row; direct ACS 5-Year `B01003_001E` Seattle place estimate | Use this row's `population`, not the sum of neighborhoods. `population_year` / metadata `acs_year` identifies the vintage, not an event date. No period numerator or percentage change applies. |
| Calibrated MCPP denominator | One row per MCPP. Allocate ACS block-group population using each block's share of its **full** 2020 block-group population; assign block representative points within MCPP polygons; sum and round raw neighborhood estimates. `population = round(population_raw * city_population / sum(population_raw))`. | Runtime rates use calibrated `population`, not `population_raw`. Refresh validation requires raw reconciliation within 1% of city population and complete MCPP coverage. Independent rounding can make the calibrated total differ slightly from the direct city estimate. Snapshot carries source/vintage/method and QA metadata. |
| Comparative rate-ranking threshold | Include neighborhoods with calibrated `population >= 5000`; exclude those below 5,000 | Required v1.1 ranking contract from this audit brief, **not yet enforced in production ranking code**. Existing maps and scatter accept positive populations below 5,000. This cutoff does not exclude offenses from city totals, raw-count rankings, or determine response-median sample sufficiency. |

Authority: [population_dashboard_data.py](../dashboard/population_dashboard_data.py), [population_snapshot.py](../dashboard/population_snapshot.py), [population_service.py](../dashboard/population_service.py).

### Verification

- `tests/dashboard/test_population_dashboard_data.py::test_snapshot_adapter_preserves_population_contract_and_provenance` — verifies the direct city denominator differs from the neighborhood sum, calibrated `population` is exposed separately from `population_raw`, neighborhood aliases normalize consistently, and provenance is preserved.
- `tests/dashboard/test_population_dashboard_data.py::test_context_uses_population_snapshot` — verifies both crime and calls contexts expose calibrated neighborhood values, the direct city value and population metadata through the real adapter; unrelated event/geography work is stubbed.
- `tests/dashboard/test_population_service.py::test_full_county_weights_sum_to_one` — verifies Census block population shares and full block-group weights summing to one.
- `tests/dashboard/test_population_service.py::test_spatial_redistribution_spans_two_mcpps_and_keeps_outside_denominator` — verifies one block group's population redistributes across two MCPPs while outside blocks remain in the weighting denominator.
- `tests/dashboard/test_population_service.py::test_geographic_input_crs_uses_projected_representative_points` — verifies geographic-CRS inputs produce the expected block assignments and neighborhood estimates through the projected representative-point path.
- `tests/dashboard/test_population_service.py::test_aggregation_rounds_before_calibration` and `tests/dashboard/test_population_service.py::test_calibration_rounds_as_notebook` — verify raw neighborhood rounding before calibration, the direct-city/raw-total factor, and rounded calibrated estimates.
- `tests/dashboard/test_population_service.py::test_reconciliation_guard` and `tests/dashboard/test_population_service.py::test_exact_one_percent_is_allowed` — verify discrepancies above 1% fail and exactly 1% is allowed, on either side of the city estimate.
- `tests/dashboard/test_population_service.py::test_expected_mcpp_coverage` and `tests/dashboard/test_population_service.py::test_mcpp_with_no_assigned_blocks_fails` — verify unexpected/missing MCPPs and an MCPP with no assigned blocks fail coverage validation.
- `tests/dashboard/test_population_snapshot.py::test_snapshot_round_trip` — verifies stored population values, ACS variable/year, Census block vintage, refresh timestamp and QA metadata survive serialization.
- `tests/dashboard/test_population_snapshot.py::test_metadata_mismatches_fail` — verifies inconsistent row counts, columns, MCPP counts, city totals and ACS years are rejected.

**Coverage limits:** `population >= 5000` remains an unimplemented comparative ranking requirement, not an enforced/tested cutoff. Adapter and calibration tests protect denominator construction/exposure; they do not establish end-to-end rate calculation, annualization policy or same-vintage comparisons in future KPI consumers.
