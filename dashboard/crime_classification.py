"""Apply the reviewed v1.1 taxonomy without discarding source evidence.

The result retains excluded rows for QA. Analytical consumers must select rows
where ``is_excluded_from_crime_analysis`` is false; coordinates play no role.
"""

import pandas as pd

from dashboard.crime_classification_decisions import (
    CANONICAL_CRIME_TYPES,
    CRIMES_AGAINST_PERSONS,
    CRIMES_AGAINST_PROPERTY,
    CRIMES_AGAINST_SOCIETY,
    CRIME_CLASSIFICATION_DECISIONS,
)


def _normalize_text(series: pd.Series) -> pd.Series:
    return (series.astype("string").str.strip().str.lower()
            .str.replace(r"\s+", " ", regex=True))


def apply_crime_classification(df: pd.DataFrame) -> pd.DataFrame:
    """Classify by reviewed (subcategory, code), then source-category fallback.

    Explicit not-a-crime semantics override inclusion decisions. A textual 999
    alone is not an exclusion. Reapplying preserves the original source category.
    """
    required = ["offense_category", "offense_sub_category", "nibrs_offense_code",
                "nibrs_crime_against_category"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Crime classification is missing required columns: {missing}")

    for key, decision in CRIME_CLASSIFICATION_DECISIONS.items():
        action = decision["action"]
        if action not in {"retain", "reclassify", "exclude"}:
            raise ValueError(f"Invalid production classification action for {key}: {action}")
        if action != "exclude" and decision["target_category"] not in CANONICAL_CRIME_TYPES:
            raise ValueError(f"Noncanonical classification target for {key}")
        if not decision["reason"]:
            raise ValueError(f"Missing classification reason for {key}")

    out = df.copy()
    source = out.get("source_offense_category", out["offense_category"])
    out["source_offense_category"] = _normalize_text(source)
    for column in required:
        out[column] = _normalize_text(out[column])
    out["nibrs_crime_against_category"] = (
        out["nibrs_crime_against_category"]
        .str.replace(r"[\s_-]+", "_", regex=True)
    )

    # Every unreviewed source category falls back to Society / Other, except
    # the two ordinary legacy categories and already canonical inputs.
    source_mapping = {category: category for category in CANONICAL_CRIME_TYPES}
    source_mapping.update({
        "violent crime": CRIMES_AGAINST_PERSONS,
        "property crime": CRIMES_AGAINST_PROPERTY,
    })
    out["offense_category"] = (
        out["source_offense_category"].map(source_mapping)
        .fillna(CRIMES_AGAINST_SOCIETY).astype("string")
    )
    out["classification_action"] = "retain"
    out["classification_reason"] = "Default normalized source-category mapping."

    # Map the small reviewed table onto all rows without a per-row apply.
    keys = pd.MultiIndex.from_frame(out[["offense_sub_category", "nibrs_offense_code"]])
    for field, column in [("action", "classification_action"),
                          ("reason", "classification_reason"),
                          ("target_category", "offense_category")]:
        mapping = {key: rule[field] for key, rule in CRIME_CLASSIFICATION_DECISIONS.items()}
        values = pd.Series(keys.map(mapping), index=out.index)
        matched = values.notna()
        out.loc[matched, column] = values.loc[matched]

    not_a_crime = out["nibrs_crime_against_category"].eq("not_a_crime").fillna(False)
    out.loc[not_a_crime, "classification_action"] = "exclude"
    out.loc[not_a_crime, "classification_reason"] = (
        "Source explicitly identifies the record as not_a_crime."
    )
    out["is_excluded_from_crime_analysis"] = out["classification_action"].eq("exclude")
    out.loc[out["is_excluded_from_crime_analysis"], "offense_category"] = pd.NA
    included = ~out["is_excluded_from_crime_analysis"]
    if not out.loc[included, "offense_category"].isin(CANONICAL_CRIME_TYPES).all():
        raise ValueError("Included crime records must have a canonical v1.1 category")
    return out
