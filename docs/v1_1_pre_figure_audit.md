# v1.1 pre-figure audit

Audited 2026-09-15 on `docs/v1-1-pre-figure-audit`, commit `9d67934` (merged UOF pipeline). Local `staging`, `origin/staging`, and a read-only GitHub `refs/heads/staging` check all matched this commit. Scope: current production code, dashboard tests, and local snapshots; no refresh jobs or implementation changes.

## Findings

| Audit question | Finding |
| --- | --- |
| Required streams present? | **Yes:** crime, CAD, 58 MCPP polygons, validated population snapshot, UOF and derived OIS all loaded. See observed coverage below. |
| Runtime consumers wired correctly? | `app.py` loads crime/CAD snapshots, local geography/lookups and the production population adapter. UOF has a validated standalone context but is not wired into the app yet. Legacy population CSV configuration is unused by these consumers. |
| Contexts sufficient? | **Yes:** crime provides analytical `valid_time`, classified QA rows and map subsets; calls provides `response_analysis`; both expose population and geography. UOF provides `df`, `ois_events`, source rows, metadata, latest date and missing-date QA. |
| Analysis windows available? | **Yes:** inclusive calendar domains, equal-length adjacent comparisons and retained-history bounds checks. All three local streams cover the previous full analysis period by bounds; this does not certify gap-free acquisition. Change displays remain KPI work. |
| Figures reusable? | Crime points and daily series largely reusable; choropleth needs units/total reconciliation; calls volume/response scatter needs redesign. Existing calls map/daily retention is optional. See the [docket](v1_1_figure_docket.md). |
| Methodological blockers to starting KPI work? | **None for the documented current contracts.** Use the event-level response loader; CAD map medians are not equivalent. Response-ranking sample/tie rules remain unresolved for that component. |

## Observed local data

Retained-history inventory, not selected-period KPIs or a certification of latest-day completeness:

| Stream | Context observation | Retained event dates |
| --- | --- | --- |
| Crime | 156,644 source rows; 17,750 excluded; 138,894 analytical offenses; all three canonical categories present | 2024-09-09 through 2026-09-13 |
| CAD | 1,161,055 dispatch rows; 681,306 valid-time event IDs; 609,519 qualified response events | 2024-09-06 through 2026-09-10 |
| UOF / OIS | 19,367 source rows; 18,366 distinct incident IDs; 122 OIS-classified rows derive 52 events; zero OIS rows missing event dates | 2014-01-27 through 2026-08-28 (full UOF stream) |
| Population / MCPP | ACS 2024 direct Seattle population **754,195**; 58 calibrated neighborhoods sum to **754,194** after rounding; 12 neighborhoods below 5,000; 58 polygons | Population vintage, not event history |

Both crime and calls expose `city_population`, `neighborhood_population`, and `population_metadata`. The one-person rounding difference confirms why city rates must use the direct city row.

## Concrete implementation boundaries

- **Crime map:** 495 coordinate-valid rows lack a spatial MCPP match. Map neighborhood totals omit them; `valid_time` retains source-neighborhood fallback. Reconcile the choropleth. City totals also retain unmappable offenses (19,542 analytical rows lack latitude).
- **Rates:** convert crime-map units from per 1,000 to per 100,000. The calls scatter annualizes volume. Implement the required 5,000-person rate-ranking cutoff and undefined zero-baseline percentage-change display.
- **Response:** loader qualification is 0–1,440 minutes inclusive, with no priority whitelist or coordinate requirement. Map medians use a retained dispatch row/spatial geography. Scatter's 100-event neighborhood/bin cutoff is not a settled ranking rule. Tests do not directly exercise loader qualification boundaries or establish map/loader equivalence.
- **Controls:** CAD bins differ from crime categories; no cross-stream mapping exists. Scatter ignores selected dates. UOF needs app integration.

Definitions and pending component decisions: [metric reference](v1_1_metric_definitions.md) and [docket](v1_1_figure_docket.md). No new data streams are needed to start implementation.

## Validation

- `python -m pytest tests/dashboard --basetemp=.pytest_tmp_dashboard -p no:cacheprovider`: system Python lacked pytest. Project `.venv\Scripts\python.exe` initially hit 50 sandbox temporary-directory errors; elevated rerun with `--tb=short`: **355 passed, 24 Plotly deprecation warnings**, 10.65 seconds.
- `.venv\Scripts\python.exe -m scripts.dashboard.smoke_check`: **passed**; calls daily/scatter/map produced 2/5/3 traces. This smoke script covers calls, not crime or UOF figures.
- Crime, calls and UOF context loads passed; required population/UOF/OIS fields were inspected. Local boundaries/lookups existed. No snapshots were mutated or refresh jobs called.

## Operational follow-ups, not blockers

Schedule annual population refresh and periodic full-history UOF reconciliation beyond incremental overlap. The daily workflow already includes CAD/crime/UOF. Review source freshness before release; this audit did not query live source freshness. Address Plotly deprecations during figure maintenance.

**National OIS comparison: DEFERRED TO v1.2.**

Retain notebooks for historical taxonomy/population QA and response research. `v1_1_metric_methodology.ipynb` explores candidate ranking rules; `04_response_time_analysis.ipynb` preserves historical distributions. Production code remains authoritative. No notebooks changed.

## Conclusion

READY FOR KPI / FIGURE IMPLEMENTATION
