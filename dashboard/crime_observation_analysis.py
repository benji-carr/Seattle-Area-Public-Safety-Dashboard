"""On-demand DuckDB totals from verified bundles; no derived archive is persisted."""

import pandas as pd

from dashboard.crime_classification import apply_crime_classification
from dashboard.crime_classification_decisions import CANONICAL_CRIME_TYPES
from dashboard.crime_observations import (
    classification_provenance, observation_directories, read_observation,
)


def coverage_status(manifest, day):
    lower = pd.Timestamp(manifest["query_lower_bound"])
    # This is an observation horizon, NOT an invented query upper bound.
    horizon = pd.Timestamp(manifest["collection_started_at_utc"]).tz_convert("America/Los_Angeles").tz_localize(None)
    if day > horizon.normalize():
        return "outside_coverage"
    if manifest["date_column"] != "offense_date":
        return "report_date_filtered"
    if day + pd.Timedelta(days=1) <= lower:
        return "outside_coverage"
    if not manifest["pagination_exhausted"]:
        return "incomplete_fetch"
    if day < lower or day == horizon.normalize():
        return "partial_boundary"
    return "complete"


def analyze_observations(root, start_date, end_date):
    """Inclusive offense-date range; changes compare adjacent available observations.

    Totals include every nonmissing source offense ID. Category totals apply the
    current established taxonomy and exclude its explicit not-a-crime rows.
    Neither path normalizes IDs beyond the existing fetched dataframe.
    """
    import duckdb  # Optional analysis dependency, never needed by dashboard startup.

    start, end = pd.Timestamp(start_date), pd.Timestamp(end_date)
    if start != start.normalize() or end != end.normalize() or start > end:
        raise ValueError("Specify an ordered range of whole offense dates")
    directories = observation_directories(root)
    if not directories:
        raise ValueError("No verified crime observations to analyze")
    totals, grid = [], []
    for directory in directories:
        # Hold one raw observation at a time; retain only small derived results in memory.
        frame, manifest = read_observation(directory)
        identity = manifest["observation_id"]
        classified = apply_crime_classification(frame)
        dates = pd.to_datetime(frame.offense_date, errors="coerce").dt.normalize()
        rows = pd.DataFrame({"observation_id": identity, "offense_date": dates,
                             "offense_id": frame.offense_id, "category": "all"})
        included = ~classified.is_excluded_from_crime_analysis
        categorized = rows.loc[included].copy()
        categorized["category"] = classified.loc[included, "offense_category"]
        records = pd.concat([rows, categorized], ignore_index=True)
        records["offense_id"] = records.offense_id.astype("string")
        with duckdb.connect() as connection:
            connection.register("source_records", records)
            totals.append(connection.execute("""
                SELECT observation_id, offense_date, category,
                       count(DISTINCT offense_id) AS n
                FROM source_records GROUP BY ALL
            """).df())
        for day in pd.date_range(start, end):
            for category in ["all", *CANONICAL_CRIME_TYPES]:
                grid.append({
                    "observation_id": identity, "offense_date": day, "category": category,
                    "collection_started_at_utc": manifest["collection_started_at_utc"],
                    "collection_finished_at_utc": manifest["collection_finished_at_utc"],
                    "query_lower_bound": manifest["query_lower_bound"],
                    "query_upper_bound": manifest["query_upper_bound"],
                    "query_date_column": manifest["date_column"],
                    "lower_inclusive": manifest["lower_inclusive"],
                    "upper_inclusive": manifest["upper_inclusive"],
                    "pagination_exhausted": manifest["pagination_exhausted"],
                    "atomic_source_snapshot": False,
                    "coverage_status": coverage_status(manifest, day),
                })
    counts = pd.concat(totals, ignore_index=True)
    coverage = pd.DataFrame(grid)
    with duckdb.connect() as connection:
        connection.register("counts", counts)
        connection.register("coverage", coverage)
        result = connection.execute("""
            SELECT coverage.*,
                CASE WHEN coverage_status = 'outside_coverage' THEN NULL
                     WHEN coverage_status = 'complete' THEN coalesce(n, 0)
                     ELSE nullif(n, 0) END AS distinct_offenses
            FROM coverage LEFT JOIN counts USING (observation_id, offense_date, category)
            ORDER BY collection_started_at_utc, observation_id, offense_date, category
        """).df()
    groups = result.groupby(["offense_date", "category"], sort=False)
    previous = groups.distinct_offenses.shift()
    both_complete = result.coverage_status.eq("complete") & groups.coverage_status.shift().eq("complete")
    result["previous_observation_id"] = groups.observation_id.shift()
    result["previous_collection_started_at_utc"] = groups.collection_started_at_utc.shift()
    result["total_change"] = (result.distinct_offenses - previous).where(both_complete).astype("Int64")
    result["hours_since_previous_observation"] = (
        pd.to_datetime(result.collection_started_at_utc, utc=True)
        - pd.to_datetime(result.previous_collection_started_at_utc, utc=True)
    ).dt.total_seconds() / 3600
    # Makes analyses across taxonomy changes reproducible without persisting totals.
    result.attrs["analysis_classification_provenance"] = classification_provenance()
    return result
