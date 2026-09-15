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

Authority: [analysis_windows.py](../dashboard/analysis_windows.py), [crime_controls.py](../dashboard/crime_controls.py), [crime_filters.py](../dashboard/crime_filters.py), and `app.py`. Tests: `test_analysis_windows.py`, `test_analysis_window_figures.py`, `test_crime_analysis.py` under `tests/dashboard/`.

## Crime

Source: Seattle SPD crime stream `tazs-3rd5`, loaded from `data/processed/crime/crime_data.parquet`. Analytical identifier: **`offense_id`**. `report_number` identifies a report and is not interchangeable: one report can contain multiple offenses.

Common inclusion: classified rows with `is_excluded_from_crime_analysis == False`, a non-null `offense_id`, and a valid `offense_date` in the period. `valid_time` provides this population; the full context `df` retains excluded rows for QA. Classification uses reviewed `(offense_sub_category, nibrs_offense_code)` decisions, then source-category fallback. Explicit `not_a_crime` semantics override inclusion; code `999` alone does not exclude a record. Coordinates are not an analytical inclusion requirement.

| Metric | Grain / numerator | Denominator / formula | Qualification and interpretation |
| --- | --- | --- | --- |
| Citywide offense count | Distinct `offense_id` in the selected period | None | All analytical neighborhoods, including unknown or unmappable records. This counts reported offenses, not reports, calls, victims, or map points. |
| Top-level crime category count | Distinct `offense_id` within each canonical `offense_category` | None | Crimes Against Persons; Crimes Against Property; Crimes Against Society / Other. Stored values are lowercase. Uses the same inclusion rules as the overall count. |
| Neighborhood offense count | Distinct `offense_id` by analytical `mcpp_neighborhood` | None | `valid_time` uses spatial lookup first, then source `neighborhood` when lookup is missing; names are trimmed/lowercased, `&` becomes `and`, repeated whitespace collapses. Unmappable offenses with usable names remain eligible. Records without a usable neighborhood stay in city totals but cannot be assigned to a named neighborhood. |
| Citywide crime rate per 100,000 | Citywide offense count | `count / city_population * 100000` | Direct Seattle ACS denominator; selected-period rate, **not annualized**. Do not divide a neighborhood-filtered numerator by the full city population and label it citywide. KPI aggregation is pending. |
| Neighborhood crime rate per 100,000 | Neighborhood offense count | `count / neighborhood_population.population * 100000` | Use calibrated MCPP population with a matching normalized name and positive denominator. Missing/nonpositive population means unavailable rate. Apply the 5,000-person cutoff only to comparative population-adjusted rankings. |

Date field for every crime metric: `offense_date`, not `report_date_time`. Category/subcategory selections can narrow counts; a citywide label requires no neighborhood restriction. Point text search is a presentation filter and does not change analytical totals.

**Existing map qualification:** the choropleth currently calculates selected-period rates **per 1,000**, using `event_mcpp` plus `unmappable_events`. A coordinate-valid offense with no MCPP match is already in the map event-ID set, so it does not enter `unmappable_events`; its source-neighborhood fallback exists in `valid_time` only. Consequently, current map totals can differ from analytical neighborhood totals. Reuse `valid_time` for KPI/ranking aggregation and reconcile the map during figure adaptation. Map points additionally require coordinates within the loader's Seattle bounds and are deduplicated by `offense_id`.

Authority: [crime_dashboard_data.py](../dashboard/crime_dashboard_data.py), [crime_classification.py](../dashboard/crime_classification.py), [crime_classification_decisions.py](../dashboard/crime_classification_decisions.py), [crime_dashboard_figures.py](../dashboard/crime_dashboard_figures.py). Tests: `test_crime_classification.py`, `test_crime_analysis.py`.

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

Tests: `test_analysis_window_figures.py` checks the scatter's analysis-year selection and median; `test_population_dashboard_data.py` checks population wiring and map compatibility. Neither establishes equivalence between the map and event-level response calculations, and there is no dedicated dashboard test of `build_response_analysis()` qualification boundaries or conflicting priorities.

## UOF / derived OIS

Source: Seattle SPD UOF stream `ppi5-g2bj`, loaded from `data/processed/uof/uof_data.parquet`. Source row key `uniqueid` is for snapshot deduplication, not incident/event counts.

| Metric | Grain / numerator | Denominator | Date and qualification | Interpretation |
| --- | --- | --- | --- | --- |
| UOF incident count | `nunique(incident_num)` via `count_uof_incidents()` | None | Inclusive `occured_date_time` calendar dates (source spelling retained). Strip IDs and exclude blank/null IDs; invalid dates cannot enter the period. No incident-type or geography restriction. | Multiple officer/subject/source records can belong to one incident. OIS-classified records are not excluded from UOF. |
| Derived OIS event count | `nunique(ois_event_key)` via `count_ois_events()` | None | Select UOF rows whose `incident_type` matches standalone, case-insensitive `\bOIS\b`. Require valid occurrence dates; group by local `event_date` plus `normalized_beat`. Select periods on `event_date`. | Operational day-and-beat events, not UOF rows, force incident numbers, people shot, or fatalities. Multiple same-day same-beat records collapse; different beats remain separate. |

Beat normalization strips whitespace and uppercases. Nulls and `""`, `-`, `OOJ`, `99`, `NAN`, `NONE`, `<NA>`, `NA`, `N/A` map to **`OUTSIDE_OR_UNKNOWN`**; other labels are retained without boundary validation. `ois_event_key` is the ISO event date, a pipe separator, and normalized beat. All outside/unknown encodings on the same day form one event. No geocoding requirement applies.

Naive UOF timestamps represent Seattle wall time; aware inputs convert to `America/Los_Angeles` before calendar grouping. Invalid-date OIS rows remain in `ois_rows` and are counted in `ois_rows_missing_event_date`, but cannot form events. Counts use the shared comparison contract; comparison cards and app integration are pending.

Authority: [uof_dashboard_data.py](../dashboard/uof_dashboard_data.py), [uof_snapshot.py](../dashboard/uof_snapshot.py), [uof_data.py](../dashboard/uof_data.py). Tests: `test_uof_dashboard_data.py`, `test_uof_pipeline.py`.

## Population denominators

Source: `data/processed/population/population_estimates.parquet` plus `population_metadata.json`. `load_dashboard_population()` exposes `neighborhood_population`, `city_population`, and `population_metadata` in both crime and calls contexts.

| Contract | Grain / value and calculation | Qualification / interpretation |
| --- | --- | --- |
| Direct Seattle city denominator | One `geography_type == "city"`, `geography_name == "seattle"` row; direct ACS 5-Year `B01003_001E` Seattle place estimate | Use this row's `population`, not the sum of neighborhoods. `population_year` / metadata `acs_year` identifies the vintage, not an event date. No period numerator or percentage change applies. |
| Calibrated MCPP denominator | One row per MCPP. Allocate ACS block-group population using each block's share of its **full** 2020 block-group population; assign block representative points within MCPP polygons; sum and round raw neighborhood estimates. `population = round(population_raw * city_population / sum(population_raw))`. | Runtime rates use calibrated `population`, not `population_raw`. Refresh validation requires raw reconciliation within 1% of city population and complete MCPP coverage. Independent rounding can make the calibrated total differ slightly from the direct city estimate. Snapshot carries source/vintage/method and QA metadata. |
| Comparative rate-ranking threshold | Include neighborhoods with calibrated `population >= 5000`; exclude those below 5,000 | Required v1.1 ranking contract from this audit brief, **not yet enforced in production ranking code**. Existing maps and scatter accept positive populations below 5,000. This cutoff does not exclude offenses from city totals, raw-count rankings, or determine response-median sample sufficiency. |

Authority: [population_dashboard_data.py](../dashboard/population_dashboard_data.py), [population_snapshot.py](../dashboard/population_snapshot.py), [population_service.py](../dashboard/population_service.py). Tests: `test_population_dashboard_data.py`, `test_population_service.py`, `test_population_snapshot.py`.
