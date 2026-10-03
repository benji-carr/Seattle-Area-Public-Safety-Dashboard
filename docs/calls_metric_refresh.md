# Calls inputs for the production crime dashboard

`app.py` loads `load_crime_call_support_context()` once. Its response KPI callback
uses `render_response_kpi()`; the neighborhood table uses
`prepare_multimetric_sources()` and `render_multimetric_ranking()` in
`dashboard/crime_v1_1_prototypes.py`. The response ranking overlay uses the same
qualified response events.

| Output | Exact inputs and rules |
| --- | --- |
| Neighborhood call volume | Distinct normalized CAD event IDs, earliest valid queued timestamp and that row's neighborhood; all priorities, including calls without a qualified response. Calendar dates are inclusive. Only canonical MCPP neighborhoods appear in the table. |
| Neighborhood median response | Per event: minimum queued time, independent minimum arrival time, first non-null numeric priority and first non-null neighborhood in queued-time order. Duration must be between 0 and 1,440 minutes inclusive. Then filter by selected dates, priority scope (1–3, 1–2, or 1) and canonical neighborhood; compute an exact median and distinct qualified-event count. The minimum-event control gates response rankings. |
| Citywide median response KPI | The same qualified event durations and selected priority scope; no neighborhood or geographic-coordinate requirement. Compare exact current median with the exact median of the adjacent equal-length previous period. Counts are qualified event counts. |
| Neighborhood crime volume | Distinct offense IDs and offense dates from the separate crime context, using its canonical MCPP assignment. Calls queries cannot supply this metric. |

Call priority controls do not filter call volume. Shared crime-category and other
crime dimensions do not filter CAD response metrics. The table uses the shared
calls/response/crime date domain. The KPI clamps dates to its own response domain.
Retain 734 days of calls so a selected year can have an equal preceding year.

## Production query path

Run `python -m scripts.dashboard.refresh_call_metrics`. It determines the latest
valid queued date from the API and queries the whole retained period, using
half-open monthly partitions and at most four concurrent requests. Each partition
paginates in deterministic group order with a 50,000-row page size and no page cap.
Existing HTTP retries/backoff handle transient errors, including rate limits.

Example query (the end date is exclusive):

```sql
SELECT cad_event_number, priority, dispatch_neighborhood,
       min(cad_event_original_time_queued) AS queued_min,
       min(cad_event_arrived_time) AS arrived_min,
       max(cad_event_original_time_queued) AS latest_queued_time
WHERE cad_event_original_time_queued >= '2026-09-01T00:00:00.000'
  AND cad_event_original_time_queued < '2026-10-01T00:00:00.000'
  AND cad_event_number IS NOT NULL
GROUP BY cad_event_number, priority, dispatch_neighborhood
ORDER BY cad_event_number, priority, dispatch_neighborhood
LIMIT 50000 OFFSET 0
```

Use aggregate aliases distinct from source fields. The live endpoint rejected
same-name aliases with `aggregate-in-ungrouped-context`; the query above succeeded.
Do not aggregate final medians by neighborhood, priority or day: that would lose
the values needed to compute exact arbitrary-period citywide medians.

Groups crossing month boundaries are merged with independent timestamp minima
and maxima. If an event has multiple priority/neighborhood groups, or different
source IDs normalize to one ID, fetch its original five-column dispatch rows in
batches of 50 event IDs. This preserves the distinct neighborhood rules and
first-non-null behavior. Quoted event IDs are escaped. Conflicting values tied at
the earliest relevant queued time fail the refresh: the existing code has no
specified tie rule, and compacting unrelated rows must not choose a new winner.

Successful queries produce:

- `data/processed/calls_metrics/spd_calls.parquet`: five metric input columns plus
  `latest_queued_time`, usually one row per event; conflicting events retain their
  dispatch rows.
- `data/processed/calls_metrics/spd_calls_metadata.json`: source coverage,
  refresh timestamp, schema version, row/column checks and query/fallback counts.

The production context prefers this snapshot. Until the first compact refresh,
its original raw snapshot remains a bootstrap fallback. A partially present
compact snapshot fails rather than serving stale raw data. Queries and metric
validation finish before snapshot replacement. Git publishes the data/metadata
pair together through the existing refresh PR.

The full retained window is reconciled each run, so old corrections and deletions
are included. This is a compact **full** calls reconciliation, not an overlap-based
incremental fetch. It leaves `refresh_spd_data.py` available for research workflows
that need the full dispatch schema. The production workflow uses the compact
refresh, drops the obsolete calls spatial-cache rebuild, checks compact freshness
against the maximum source queued date, and smoke-tests the current table/KPI.
The workflow still publishes crime, calls and UOF together; a remaining source
failure can still block that shared refresh PR.

## Validation

Parity tests cover independent earliest arrival, conflicting priority/neighborhood,
null neighborhoods, normalized ID collisions, no arrival, invalid priority,
negative/over-day/24-hour response durations, monthly boundaries, pagination,
all priority scopes, all three table metrics, minimum-event thresholds and the
previous-period KPI. Tests also cover deletion reconciliation, freshness,
partial snapshots, failure preservation and tied conflicting values.

Live September 2026 comparison: 50,217 projected raw dispatch rows and 28,720
compact input rows produced identical call-volume inputs and 25,410 qualified
response events. The grouped month query completed in 6.3 seconds in that probe;
the projected raw month query completed in 26.8 seconds. These are single samples,
not a latency guarantee. Benchmark the complete production window separately.

The complete 734-day live refresh (2024-09-25 through 2026-09-29) completed in
about 96 seconds using 25 aggregate requests, with no conflicting-event fallbacks.
Its 683,504 compact rows matched the existing 1,168,234-row raw snapshot exactly
for all 683,504 call events and all 611,481 qualified response events. The compact
parquet was 19.1 MB versus 35.0 MB for the raw parquet. These figures describe the
measured run; future source latency and conflict rates can change them.
