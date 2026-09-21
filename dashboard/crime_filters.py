"""Shared analytical selection. Coordinate validity is deliberately not a filter."""

import pandas as pd


def filter_crime_records(records, state, *, include_dates=True):
    # Also protect callers using the full classified QA snapshot.
    if "is_excluded_from_crime_analysis" in records.columns:
        records = records.loc[~records["is_excluded_from_crime_analysis"]]
    if not state or records.empty:
        return records.copy()
    mask = pd.Series(True, index=records.index)
    for key, column in [
        ("crime_categories", "event_importance_bin"),
        ("crime_subcategories", "offense_sub_category"),
        ("neighborhoods", "mcpp_neighborhood"),
    ]:
        values = state.get(key) or []
        if values:
            series = records[column]
            if key == "neighborhoods":
                series = (series.astype("string").str.strip().str.lower()
                          .str.replace("&", "and", regex=False)
                          .str.replace(r"\s+", " ", regex=True))
            mask &= series.isin(values)
    if include_dates:
        dates = pd.to_datetime(records["offense_date"], errors="coerce").dt.normalize()
        mask &= dates.between(pd.Timestamp(state["start_date"]), pd.Timestamp(state["end_date"]))
    return records.loc[mask].copy()
