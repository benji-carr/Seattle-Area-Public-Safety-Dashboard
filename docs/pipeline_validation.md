# Pipeline validation migration

This migration uses Pydantic for refresh settings and metadata, and Pandera for
table boundaries. Work uses the existing `staging` branch, which matched local
`main` at `76ca00e` when work began. The working tree was clean. No applicable
`AGENTS.md` was present in the repository or its ancestor paths.

## Object contracts

`dashboard/refresh_models.py` defines pagination, rolling-window/overlap, retry,
timeout, ACS-vintage, and crime date-column contracts. Existing callable
signatures and return types remain intact. Named scalar validators remain
importable from their original modules.

Integer settings are strict: booleans, strings and fractional numbers do not
become valid page sizes, retry counts, windows or overlaps. Numeric timeout and
backoff fields accept the same native int/float inputs; a before-validator
prevents additionally accepting Decimal or arbitrary objects with `__float__`.
The SPD client still normalizes scalar and two-element tuple timeouts to floats.
Crime and dashboard refresh entrypoints still accept scalar timeouts only.
Forecasting keeps separate connect/read fields.

`dashboard/snapshot_models.py` checks required metadata fields and existing
identity/version rules. Compact calls requires integer version `1`; UOF requires
its established dataset ID. `forecasting/production/artifact_models.py` checks
the four locked identity fields and the feature schema's neighborhood-first
column order, using the existing production constants as expected values.
Readers return the original dictionaries.

Legacy metadata validation was intentionally shallow: many fields only had to
exist. Those fields use required `Any`, avoiding new types, date parsing or
defaults. Unknown provenance, optional `last_fetch`, null coverage dates and
original keys/formats remain supported. Artifact `schema_version` was not a
reader gate; it remains optional and does not gain a new version restriction.
The compact calls version rule remains a separate contract.

## Table contracts

Schemas do not coerce data or remove rows/columns. Existing parsing, filtering,
sorting and deduplication remain in their original order. Dtypes are specified
where normalization guarantees them, such as pandas string GEOIDs. Numeric
value checks retain existing integer/float/nullable storage representations.
Timestamp resolution, date interpretation and timezone conversions remain with
the original pandas code.

| Boundary | Schema module | Preserved behavior |
| --- | --- | --- |
| Calls metric source | `dashboard/call_metric_schemas.py` | Required columns, non-null/nonblank IDs, required queued times, nullable arrivals, maximum/minimum order. Repeated IDs, unknown priorities, missing neighborhoods and extra columns remain supported. Non-null arrival text parsing to NaT still fails explicitly. |
| Calls aggregate counts | `dashboard/call_metric_schemas.py` | Integral counts from 0 through 2^53-1, positive source counts, ID counts no larger than source counts. Existing numeric parsing precedes validation; int64 conversion follows it. |
| Legacy crime/calls refresh | `dashboard/refresh_schemas.py` | Required time/key columns. Uniqueness only after keep-last resolution in incremental refreshes. Full crime refresh still retains source duplicates and missing dates. |
| Stored UOF | `dashboard/uof_schemas.py` | Exact canonical column order, no extra or repeated column names. Historical nullable fields remain supported; older stored snapshots do not acquire a uniqueness requirement. |
| Normalized UOF refresh | `dashboard/uof_schemas.py` | Missing IDs fail before deduplication. Duplicate source IDs are resolved keep-last before normalized uniqueness validation. Missing occurrence dates remain supported. |
| Population | `dashboard/population_schemas.py` | Canonical snapshot columns, unique geography keys, padded GEOIDs, finite nonnegative population values. City block counts remain nullable. Unmatched ACS sentinel/missing estimates remain tolerated until geography matching. |
| Forecast source | `forecasting/production/table_schemas.py` | Required columns and valid parsed times before invalid ID/neighborhood filtering. Source duplicates remain for target aggregation. |
| Forecast target/features | `forecasting/features/table_schemas.py` | Existing neighborhood filtering, required columns, target null/negative rejection, unique daily keys, nonempty features and finite predictors. Extra columns remain supported. |
| Production training | `forecasting/production/table_schemas.py` | Selected feature requirements, missing target/neighborhood rejection, finite inputs and unique keys after date normalization. Finite predictor definitions are shared with feature validation. |

Both Pandera `SchemaError` and `SchemaErrors` become `ValueError` or
`PopulationValidationError` with column/check diagnostics. Population QA on
cross-table failures remains attached to the existing exception class. Pydantic
`ValidationError` is a `ValueError`, preserving existing catch interfaces.

Cross-file row counts/columns, population totals/year/coverage, timestamp extrema,
pagination completeness, calls fallback reconciliation, artifact checksums and
baseline-category agreement remain explicit. Retry loops, session ownership,
refresh strategies, retention, schedules, publication and metrics are unchanged.
No category vocabulary was invented or changed.

## Compatibility decisions and deferred changes

- Calls/crime/forecasting numeric settings historically allowed NaN and positive
  infinity through their comparisons; UOF/population required finiteness. This
  inconsistency is preserved and regression-tested. Unifying it is deferred.
- Presence-only metadata remains presence-only. Stronger types, timestamp parsing,
  provenance requirements and artifact version gating require separate decisions.
- Raw/compact calls and full crime snapshots do not gain generic unique event-ID
  constraints. Uniqueness schemas follow existing duplicate resolution.
- Geometry/CRS checks, classification, chronological daily coverage, holdout
  isolation, minimum feature history, maturity and operational cross-file checks
  stay explicit. The future crime observation study and Tenacity are out of scope.
- Diagnostics intentionally include library details. Missing calls metadata
  `row_count` now raises a useful `ValueError` instead of the accidental `KeyError`
  that preceded required-key validation. Empty/null artifact `raw_training_columns`
  similarly raises `ValueError` instead of an indexing exception. No supported
  data semantics are intentionally changed.

## Dependencies and verification

Runtime requirements pin `pydantic==2.13.4` and `pandera[pandas]==0.34.1` because
dashboard readers and forecasting modules import them directly. Installed package
metadata declares Python minimums of 3.9 for Pydantic, 3.10 for Pandera, 3.11 for
pandas 3.0.3 and 3.12 for numpy 2.5.0. Pandera requires pandas >=2.1.1 and numpy
>=1.24.4. The repository pins satisfy these constraints, including CI's Python
3.12 and the forecasting image's Python 3.14. Execution was tested on Python
3.14.6; Python 3.12 was not available locally. `pip check` reports no broken
requirements.

References: [Pydantic strict validation](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/),
[Pandera dataframe schemas](https://pandera.readthedocs.io/en/stable/dataframe_schemas.html).

Final complete-suite result: **696 passed, 13 warnings** in 61.21 seconds on
Python 3.14.6. Warnings were pandas date-inference warnings for intentionally bad
timestamp fixtures and existing Plotly mapbox deprecations. Focused existing
suites passed 238 tests, followed by 68 targeted compatibility tests; the final
exception-translation regressions passed 122 focused tests. `pip check` and
`git diff --check` also passed. Complete-suite verification uses:

```powershell
.venv\Scripts\python.exe -m pytest tests/dashboard tests/forecasting -q --tb=short
.venv\Scripts\python.exe -m pip check
git diff --check
```

New tests cover strict/boolean settings, scalar/tuple timeouts, missing metadata
and columns, nullable fields, extra-column policy, raw versus normalized duplicate
IDs, full crime retention, calls default-date lookup and historical artifact
metadata. Existing synthetic training/inference, checksum, retry, refresh,
chronology and maturity tests continue running. The Census HTTP-error test now
mocks retry sleep, avoiding 155 seconds of waiting on mocked request failures.

Read-only checks loaded existing local snapshots: raw calls 1,168,234 rows;
compact calls 683,958; crime 156,577; UOF 19,419; population 59. Calls readers
used projected columns. No complete local model artifact was present; historical
artifact compatibility uses synthetic legacy-format files and training/inference
tests. No live refresh ran and production snapshots were not overwritten.
Changes remain unstaged, without commits, pushes or merges.

<!-- validation-line-counts -->

## Changed files and physical line counts

Counts compare local `main` at `76ca00e` with the review tree and include comments,
docstrings and blank lines. Existing runtime files: **5010 → 4699** lines
(311 fewer). Their diff removes **549** lines and adds **238**
lines of model/schema calls, exception translation and imports. Deletions include
superseded handwritten checks, surrounding whitespace and moved constants; this
is a physical diff count, not a claim that every removed line was a check.

New model/schema modules are counted separately: **0 → 364** lines.
Existing runtime plus new definitions: **5010 → 5063** lines
(+53). Tests, requirements and this document are excluded
from those runtime totals.

| Migrated existing file | Before | After |
| --- | ---: | ---: |
| `dashboard/call_metrics_refresh.py` | 231 | 235 |
| `dashboard/crime_call_support_data.py` | 125 | 117 |
| `dashboard/crime_client.py` | 182 | 164 |
| `dashboard/crime_query.py` | 84 | 69 |
| `dashboard/crime_service.py` | 103 | 94 |
| `dashboard/crime_snapshot.py` | 129 | 110 |
| `dashboard/population_client.py` | 232 | 221 |
| `dashboard/population_service.py` | 263 | 271 |
| `dashboard/population_snapshot.py` | 109 | 114 |
| `dashboard/spd_client.py` | 168 | 149 |
| `dashboard/spd_query.py` | 92 | 86 |
| `dashboard/spd_service.py` | 134 | 127 |
| `dashboard/spd_snapshot.py` | 110 | 99 |
| `dashboard/uof_client.py` | 93 | 90 |
| `dashboard/uof_query.py` | 61 | 58 |
| `dashboard/uof_snapshot.py` | 85 | 92 |
| `forecasting/features/xgboost.py` | 689 | 581 |
| `forecasting/production/data_refresh.py` | 605 | 596 |
| `forecasting/production/inference.py` | 162 | 167 |
| `forecasting/production/xgboost.py` | 344 | 343 |
| `scripts/dashboard/refresh_call_metrics.py` | 61 | 62 |
| `scripts/dashboard/refresh_crime_data.py` | 396 | 344 |
| `scripts/dashboard/refresh_spd_data.py` | 418 | 365 |
| `scripts/dashboard/refresh_uof_data.py` | 134 | 145 |

| New definition module | Lines |
| --- | ---: |
| `dashboard/call_metric_schemas.py` | 39 |
| `dashboard/population_schemas.py` | 22 |
| `dashboard/refresh_models.py` | 115 |
| `dashboard/refresh_schemas.py` | 17 |
| `dashboard/snapshot_models.py` | 63 |
| `dashboard/uof_schemas.py` | 19 |
| `forecasting/features/table_schemas.py` | 24 |
| `forecasting/production/artifact_models.py` | 40 |
| `forecasting/production/table_schemas.py` | 25 |

Other changed files:

- `requirements.txt`: two pinned runtime dependencies.
- `tests/dashboard/test_pipeline_validation.py`: new boundary regression cases.
- `tests/dashboard/test_refresh_entrypoints.py`: calls default-start regression.
- `tests/dashboard/test_population_client.py`: mock retry sleep.
- `tests/forecasting/test_xgboost_production_data_refresh.py`: source filtering/duplicate compatibility.
- `tests/forecasting/test_xgboost_production_inference.py`: legacy metadata and verification order.
- `docs/pipeline_validation.md`: this contract inventory and migration report.
