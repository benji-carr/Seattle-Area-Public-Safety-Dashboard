import geopandas as gpd
import pandas as pd
import pytest

from dashboard import crime_dashboard_data as data
from dashboard.crime_classification import (
    CANONICAL_CRIME_TYPES,
    CRIMES_AGAINST_PERSONS as PERSONS,
    CRIMES_AGAINST_PROPERTY as PROPERTY,
    CRIMES_AGAINST_SOCIETY as SOCIETY,
    apply_crime_classification,
)
from dashboard.crime_classification_decisions import CRIME_CLASSIFICATION_DECISIONS
from dashboard.crime_controls import make_analysis_state
from dashboard.crime_dashboard_figures import (
    CRIME_CATEGORY_COLOR_MAP, TARGET_CRIME_CATEGORIES, make_crime_combo_label,
    prepare_daily_event_data,
)
from dashboard.crime_filters import filter_crime_records


FRAUD = "extortion/fraud/forgery/bribery (includes bad checks)"
PROPERTY_OFFENSES = "property offenses (includes stolen, destruction)"
EXPECTED = [
    ("assault offenses", "13B", PERSONS),
    ("assault offenses", "13C", PERSONS),
    ("kidnapping/abduction", "100", PERSONS),
    ("sex offenses", "11D", PERSONS),
    ("sex offenses", "36A", PERSONS),
    ("sex offenses", "36B", PERSONS),
    ("human trafficking", "64A", PERSONS),
    *[(PROPERTY_OFFENSES, code, PROPERTY) for code in ["280", "290"]],
    *[(FRAUD, code, PROPERTY) for code in
      ["210", "250", "26A", "26B", "26C", "26D", "26E", "26F", "26G", "270", "510", "90A"]],
    ("narcotic violations (includes drug equip.)", "35A", SOCIETY),
    ("narcotic violations (includes drug equip.)", "35B", SOCIETY),
    ("disorderly conduct & vagrancy violations", "90B", SOCIETY),
    ("disorderly conduct & vagrancy violations", "90C", SOCIETY),
    ("dui", "90D", SOCIETY),
    ("liquor law violations & drunkenness", "90E", SOCIETY),
    ("non-violent family offenses", "90F", SOCIETY),
    ("liquor law violations & drunkenness", "90G", SOCIETY),
    ("sex offenses", "90H", SOCIETY),
    ("trespass", "90J", SOCIETY),
    ("pornography", "370", SOCIETY),
    *[("gambling offenses", code, SOCIETY) for code in ["39A", "39B", "39C"]],
    *[("prostitution offenses", code, SOCIETY) for code in ["40A", "40B", "40C"]],
    ("weapon law violation", "520", SOCIETY),
    ("animal cruelty", "720", SOCIETY),
    ("all other", "90Z", SOCIETY),
    ("violation of no contact order", "500", SOCIETY),
]


def source_row(**overrides):
    row = dict(offense_category="all other", offense_sub_category="unreviewed",
               nibrs_offense_code="test", nibrs_crime_against_category="society")
    row.update(overrides)
    return row


@pytest.mark.parametrize("subcategory,code,expected", EXPECTED)
def test_reviewed_pair_overrides_source_defaults(subcategory, code, expected):
    # Deliberately contradictory defaults prove these are explicit decisions.
    source = "property crime" if expected != PROPERTY else "violent crime"
    df = pd.DataFrame([source_row(offense_category=f"  {source.upper()}  ",
                                 offense_sub_category=f" {subcategory.upper()} ",
                                 nibrs_offense_code=f" {code} ")])
    original = df.copy(deep=True)
    row = apply_crime_classification(df).iloc[0]
    assert row.offense_category == expected
    assert row.source_offense_category == source
    assert not row.is_excluded_from_crime_analysis
    assert row.classification_reason != "Default normalized source-category mapping."
    pd.testing.assert_frame_equal(df, original)


@pytest.mark.parametrize("source,expected", [
    ("violent crime", PERSONS), ("property crime", PROPERTY),
    ("all other", SOCIETY), ("other (includes drug and sex offenses)", SOCIETY),
    ("other", SOCIETY), (None, SOCIETY),
    *[(category, category) for category in CANONICAL_CRIME_TYPES],
])
def test_default_mapping(source, expected):
    result = apply_crime_classification(pd.DataFrame([source_row(offense_category=source)]))
    assert result.offense_category.tolist() == [expected]


@pytest.mark.parametrize("fields", [
    dict(offense_sub_category="Justifiable Homicide", nibrs_offense_code="09C"),
    dict(offense_sub_category=" UNKNOWN ", nibrs_offense_code=" - "),
    *[dict(nibrs_crime_against_category=value, offense_sub_category="assault offenses",
           nibrs_offense_code="13B") for value in
      ["not_a_crime", " Not A Crime ", "NOT-A-CRIME", "not__a--crime"]],
])
def test_reviewed_exclusions_and_semantics_override_inclusion(fields):
    row = apply_crime_classification(pd.DataFrame([source_row(**fields)])).iloc[0]
    assert row.is_excluded_from_crime_analysis
    assert row.classification_action == "exclude"
    assert pd.isna(row.offense_category)
    assert row.classification_reason


@pytest.mark.parametrize("fields", [
    dict(nibrs_offense_code="999"),
    dict(offense_sub_category="999", nibrs_offense_code="999"),
    dict(offense_sub_category="unknown", nibrs_offense_code="999"),
    dict(offense_sub_category="unknown", nibrs_offense_code="13B"),
])
def test_no_unreviewed_exclusions_or_code_only_rules(fields):
    row = apply_crime_classification(pd.DataFrame([source_row(**fields)])).iloc[0]
    assert not row.is_excluded_from_crime_analysis
    assert row.offense_category == SOCIETY


@pytest.mark.parametrize("column", ["offense_category", "offense_sub_category",
                                    "nibrs_offense_code", "nibrs_crime_against_category"])
def test_required_source_fields(column):
    with pytest.raises(ValueError, match=column):
        apply_crime_classification(pd.DataFrame([source_row()]).drop(columns=column))


def test_decisions_are_final_and_invalid_rules_fail_loudly(monkeypatch):
    for decision in CRIME_CLASSIFICATION_DECISIONS.values():
        assert decision["action"] in {"retain", "reclassify", "exclude"}
        assert decision["reason"]
        if decision["action"] != "exclude":
            assert decision["target_category"] in CANONICAL_CRIME_TYPES
    for invalid in [dict(action="review", target_category=PERSONS, reason="test"),
                    dict(action="retain", target_category="violent crime", reason="test")]:
        monkeypatch.setitem(CRIME_CLASSIFICATION_DECISIONS, ("unreviewed", "test"), invalid)
        with pytest.raises(ValueError):
            apply_crime_classification(pd.DataFrame([source_row()]))


def test_empty_frame_and_reapplication_preserve_evidence():
    df = pd.DataFrame([source_row(), source_row(offense_sub_category="unknown", nibrs_offense_code="-")])
    result = apply_crime_classification(df)
    pd.testing.assert_frame_equal(result, apply_crime_classification(result))
    assert apply_crime_classification(df.iloc[:0]).empty


def test_context_reconciliation_exclusions_and_unmappable_analysis(monkeypatch):
    rows = [source_row(offense_id=str(i), offense_sub_category=sub, nibrs_offense_code=code)
            for i, (sub, code, _) in enumerate(EXPECTED)]
    rows += [source_row(offense_id="excluded-homicide", offense_sub_category="justifiable homicide", nibrs_offense_code="09C"),
             source_row(offense_id="excluded-unknown", offense_sub_category="unknown", nibrs_offense_code="-"),
             source_row(offense_id="excluded-noncrime", nibrs_crime_against_category="not_a_crime")]
    raw = pd.DataFrame(rows)
    raw["report_number"] = raw.offense_id
    raw["offense_date"] = "2026-09-02"
    raw.loc[0, "offense_date"] = "2026-09-01"
    raw["report_date_time"] = raw.offense_date
    raw["latitude"], raw["longitude"] = 47.6, -122.33
    raw.loc[1, ["latitude", "longitude"]] = None
    raw.loc[2, ["latitude", "longitude"]] = 0
    for column in ["nibrs_group_a_b", "nibrs_offense_code_description", "shooting_type_group",
                   "block_address", "precinct", "sector", "beat", "reporting_area", "census_block_2020"]:
        raw[column] = "test"
    raw["neighborhood"] = "downtown"
    raw = pd.concat([raw, raw.iloc[[1]]], ignore_index=True)  # Unique-offense accounting.
    monkeypatch.setattr(data, "load_crime_snapshot", lambda _: (raw.copy(), {}))
    monkeypatch.setattr(data, "load_mcpp_boundaries", lambda: gpd.GeoDataFrame())
    monkeypatch.setattr(data, "load_dashboard_population", lambda: (pd.DataFrame(), 900, {}))

    def lookup(mappable_events, mcpp_boundaries):
        return mappable_events[["offense_id"]].assign(mcpp_neighborhood="downtown", mcpp_precinct="west")
    monkeypatch.setattr(data, "build_or_load_event_mcpp_lookup", lookup)
    context = data.load_crime_dashboard_context()
    prepared = context["df"]
    excluded = prepared.loc[prepared.is_excluded_from_crime_analysis]
    included = prepared.loc[~prepared.is_excluded_from_crime_analysis]
    assert raw.offense_id.nunique() == included.offense_id.nunique() + excluded.offense_id.nunique()
    assert set(included.offense_id).isdisjoint(excluded.offense_id)
    assert set(included.offense_category) == set(CANONICAL_CRIME_TYPES)
    assert included.groupby("offense_id").offense_category.nunique().eq(1).all()
    pd.testing.assert_series_equal(prepared.event_group, prepared.offense_category, check_names=False)
    pd.testing.assert_series_equal(prepared.event_importance_bin, prepared.offense_category, check_names=False)
    for name in ["valid_time", "mappable_events", "event_mcpp", "unmappable_events"]:
        assert set(context[name].offense_id).isdisjoint(excluded.offense_id)
    assert {"1", "2"} <= set(context["valid_time"].offense_id)
    assert {"1", "2"}.isdisjoint(context["mappable_events"].offense_id)
    assert {"1", "2"} <= set(context["unmappable_events"].offense_id)
    assert set(filter_crime_records(prepared, None).offense_id) == set(included.offense_id)
    daily, _ = prepare_daily_event_data(context, CANONICAL_CRIME_TYPES)
    assert daily.reported_offenses.sum() == len(EXPECTED)


def test_canonical_defaults_colors_and_labels():
    state = make_analysis_state(None, [], [], [], "2026-09-01", "2026-09-02", TARGET_CRIME_CATEGORIES)
    assert state["crime_categories"] == CANONICAL_CRIME_TYPES
    assert CRIME_CATEGORY_COLOR_MAP == {PERSONS: "#EB5757", PROPERTY: "#27AE60", SOCIETY: "#2F80ED"}
    assert make_crime_combo_label([PERSONS]) == "Crimes Against Persons"
    assert make_crime_combo_label([SOCIETY]) == "Crimes Against Society / Other"
