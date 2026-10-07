# Dashboard architecture

Source collection, snapshot persistence and dashboard presentation have separate
responsibilities. Collection does not write snapshots or build UI contexts.

```text
Seattle Open Data
  |
  +-- crime_source.py ------------------+--> crime_snapshot.py
  |                                    +--> crime_observations.py (opt-in capture)
  +-- uof_source.py ------------------------> uof_snapshot.py
  +-- spd_source.py ------------------------> spd_snapshot.py (raw calls/research)
          |
          +--> call_metrics_refresh.py -----> spd_snapshot.py (compact calls)
          +--> forecasting/production/data_refresh.py (forecasting data)

Refresh scripts orchestrate collection and persistence.

Saved snapshots + population_snapshot.load_dashboard_population()
  |
  +--> crime_dashboard_data.py / crime_call_support_data.py / uof_dashboard_data.py
         |
         +--> figures, components and controls --> app.py
```

## Source modules

Each source module has four sections: constants/query construction, HTTP access,
record normalization and dataset assembly. Public callable signatures, source
column constants and return values are retained under the new import paths.

| Module | Responsibilities and entrypoints |
| --- | --- |
| `dashboard/crime_source.py` | Crime SoQL, retrying requests, `crime_records_to_dataframe`, `load_crime_dataset`, latest-record lookup. |
| `dashboard/uof_source.py` | UOF SoQL, session/retry handling, Seattle occurrence-time normalization, `fetch_uof_dataset`, `load_uof_dataset`, latest-record lookup. |
| `dashboard/spd_source.py` | Calls SoQL, scalar/tuple timeouts, session/retry handling, `spd_calls_to_dataframe`, `fetch_spd_call_dataset`, `load_spd_call_dataset`, latest-record lookup. |

For example, these imports do not fetch data:

```python
from dashboard.crime_source import CRIME_COLUMNS, load_crime_dataset
from dashboard.uof_source import UOF_COLUMNS, fetch_uof_dataset
from dashboard.spd_source import SPD_CALL_COLUMNS, build_spd_call_query_params
from dashboard.population_snapshot import load_dashboard_population
```

General calls collection retains its existing page-count accounting; UOF counts
HTTP attempts including retries. The compact calls collector keeps its own
aggregate-query, fallback and attempt accounting in `call_metrics_refresh.py`.
Progress callbacks, ordering, pagination, session ownership and normalization
remain source-specific. Full crime collection preserves duplicates; refresh
entrypoints apply their existing deduplication and retention rules where required.

The old query/client/data/service import paths have been removed. Update imports,
qualified module references, dynamic import strings and test mock targets to the
source module where the function now looks up its dependencies. There are no
compatibility shims. Exploratory module `__main__` examples were removed; use the
operational refresh entrypoints below when intentionally refreshing data.

## Snapshot, validation and presentation modules

`crime_snapshot.py`, `uof_snapshot.py`, `spd_snapshot.py` and
`population_snapshot.py` read/write snapshots and validate their metadata.
`population_snapshot.load_dashboard_population()` adapts the saved population
table into neighborhood denominators, the direct city population and provenance.
Population collection remains in `population_client.py` and `population_service.py`.

Pydantic settings/metadata models and Pandera schemas stay in their existing
model/schema modules. Source modules depend on refresh settings, not snapshot
schemas or UI modules; UOF schemas consume source column constants without a
reverse dependency. No validation rules or exception interfaces change.

`app.py` loads crime and compact calls contexts for the crime page, with UOF
context for its fixed context cards. It connects layouts, controls, cached figures
and callbacks. It does not fetch source datasets. Crime analytical date controls
and filters populate `crime-analysis-state-store`, shared by figures and labels.
Map point filters, neighborhood classification and figure construction stay in
their existing modules. The full calls context/figures remain available for
research consumers; production startup uses the compact calls context.

Observation collection/persistence, archiving and analysis remain separate from
source modules. DuckDB loads only when observation analysis runs; SARIMA research
dependencies are not eagerly loaded by source collection or production forecasting.
See [crime observations](crime_observations.md) and [metric definitions](v1_1_metric_definitions.md).

## Production refresh and runtime data

`.github/workflows/daily_spd_refresh.yml` runs the refresh after its freshness
recovery decision. The current sequence is:

```text
Check out main; install dependencies; decide whether refresh is needed
  -> Collect crime full window and optional observation
  -> Archive completed observation; back up completed bundles
  -> Reconcile compact calls over the full retained window
  -> Refresh UOF full history
  -> Rebuild crime geography lookup and dashboard contexts
  -> Smoke tests, dashboard tests and source freshness checks
  -> Publish data through the automation branch and refresh PR
```

These commands fetch live data and replace local snapshots; they are operational
commands, not offline tests:

```powershell
python -m scripts.dashboard.refresh_call_metrics
python -m scripts.dashboard.refresh_crime_data --observation-root data/observations/crime
python -m scripts.dashboard.refresh_uof_data
python -m scripts.dashboard.refresh_population_data
```

Production calls refresh is a **full compact reconciliation** of the retained
window via `refresh_call_metrics`, including older corrections and deletions.
It bootstraps without a prior calls snapshot. The legacy raw-dispatch incremental
path in `scripts/dashboard/refresh_spd_data.py` is available for research; it is
not the daily production calls refresh. See [calls metric refresh](calls_metric_refresh.md)
for reconciliation and local publication guarantees.

Crime production `main()` performs a full offense-date pull for the retained
window; the optional observation captures the normalized dataframe before
downstream snapshot preparation. UOF production refresh fetches full history.
The existing incremental helpers remain available with their existing semantics.
Population refresh is a separate command, not a daily-workflow step.

Production calls metrics, crime, UOF and population snapshots and selected
geography lookups are versioned in Git. Their filenames and formats are unchanged.
Downloaded source data, observation bundles, most generated geography data and
forecasting runtime artifacts are ignored; restore or regenerate required local
state before use. Never use a production snapshot directory for test output.

## Where to make changes

| Change | Location |
| --- | --- |
| Source fields, SoQL, HTTP, normalization, pagination | The relevant `dashboard/*_source.py` section |
| Compact calls aggregation and fallback | `dashboard/call_metrics_refresh.py` |
| Snapshot reading/writing and population adapter | `dashboard/*_snapshot.py` |
| Refresh orchestration/retention | `scripts/dashboard/refresh_*.py` |
| Dataframe contracts/settings | Existing `*_schemas.py` / `*_models.py` |
| Dashboard context preparation | `dashboard/*_dashboard_data.py`, `crime_call_support_data.py` |
| Classification, filters, observations | Their existing dedicated modules |
| Plots and components | `dashboard/*_dashboard_figures.py`, `crime_dashboard_components.py` |
| Layout, controls and callbacks | `app.py`, `dashboard/crime_controls.py`, `assets/` |
| Daily automation and smoke checks | `.github/workflows/daily_spd_refresh.yml`, `scripts/dashboard/smoke_check.py` |
