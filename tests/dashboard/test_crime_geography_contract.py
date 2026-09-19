"""Analytical MCPP names are members of the boundary vocabulary or unassigned."""

from unittest.mock import MagicMock

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from dashboard import crime_dashboard_data as data
from dashboard.crime_controls import make_neighborhood_options
from dashboard.crime_filters import filter_crime_records


@pytest.fixture
def boundaries():
    return gpd.GeoDataFrame(
        {"mcpp_neighborhood": [" Capitol   Hill ", "DOWNTOWN", " West & Center "],
         "mcpp_precinct": ["east", "west", "west"]},
        geometry=[box(-122.33, 47.60, -122.30, 47.65),
                  box(-122.36, 47.60, -122.33, 47.65),
                  box(-122.39, 47.60, -122.36, 47.65)],
        crs="EPSG:4326",
    )


@pytest.fixture
def vocabulary(boundaries):
    return set(data.normalize_neighborhood_name(boundaries.mcpp_neighborhood).dropna())


@pytest.mark.parametrize("spatial,source,expected", [
    (" CAPITOL   HILL ", "downtown", "capitol hill"),
    (None, " Capitol  Hill ", "capitol hill"),
    (None, " WEST  &  CENTER ", "west and center"),
    ("stale spatial label", "downtown", "downtown"),
    ("unknown", "-", None),
    *[(None, value, None) for value in ["-", "", "  ", "unknown", None,
                                      "not a real mcpp", "capitol hil"]],
])
def test_resolution_domain_precedence_and_fallback(vocabulary, spatial, source, expected):
    index = pd.Index([42], name="source_row")
    result = data.resolve_analytical_mcpp_neighborhood(
        pd.Series([spatial], index=index), pd.Series([source], index=index), vocabulary,
    )
    assert result.index.equals(index)
    assert set(result.dropna()) <= vocabulary
    if expected is None:
        assert result.iloc[0] is pd.NA
    else:
        assert result.iloc[0] == expected


def test_resolution_preserves_rows_index_and_single_assignment(vocabulary):
    offenses = pd.DataFrame({
        "offense_id": ["a", "b", "b", "c"],
        "spatial": ["capitol hill", None, None, "stale"],
        "source": ["downtown", "downtown", "downtown", "-"],
    }, index=[9, 3, 3, 1])
    original = offenses.copy(deep=True)
    resolved = data.resolve_analytical_mcpp_neighborhood(
        offenses.spatial, offenses.source, vocabulary,
    )
    assert resolved.index.equals(offenses.index)
    offenses["mcpp_neighborhood"] = resolved
    assert len(offenses) == len(original)
    assert offenses.groupby("offense_id").mcpp_neighborhood.nunique(dropna=False).eq(1).all()
    pd.testing.assert_frame_equal(offenses.drop(columns="mcpp_neighborhood"), original)
    empty = data.resolve_analytical_mcpp_neighborhood(
        offenses.spatial.iloc[:0], offenses.source.iloc[:0], vocabulary,
    )
    assert empty.empty
    assert empty.index.equals(offenses.index[:0])
    assert data.resolve_analytical_mcpp_neighborhood(
        offenses.spatial, offenses.source, set(),
    ).isna().all()


@pytest.fixture
def context(monkeypatch, boundaries):
    cases = [
        ("spatial-wins", "downtown", 47.62, -122.32, "capitol hill"),
        ("missing-coordinates", " CAPITOL  HILL ", None, None, None),
        ("invalid-coordinates", "downtown", 0, 0, None),
        ("stale-spatial", "downtown", 47.62, -122.32, "retired label"),
        ("no-match-fallback", "downtown", 47.62, -122.32, None),
        ("coordinates-only", "-", 47.62, -122.32, None),
        *[(f"invalid-source-{i}", value, None, None, None)
          for i, value in enumerate(["-", "", "unknown", None, "unmapped legacy label"])],
    ]
    raw = pd.DataFrame(cases, columns=["offense_id", "neighborhood", "latitude",
                                     "longitude", "spatial"])
    raw["report_number"] = "shared-report"  # Offenses, not reports, are counted.
    raw["offense_date"] = raw["report_date_time"] = "2026-09-02"
    raw["offense_category"] = "property crime"
    raw["offense_sub_category"] = "unreviewed"
    raw["nibrs_crime_against_category"] = "property"
    for column in ["nibrs_group_a_b", "nibrs_offense_code_description", "nibrs_offense_code",
                   "shooting_type_group", "block_address", "precinct", "sector", "beat",
                   "reporting_area", "census_block_2020"]:
        raw[column] = "test"
    raw = pd.concat([raw, raw.iloc[[1]]], ignore_index=True)
    lookup = raw[["offense_id", "spatial"]].drop_duplicates("offense_id").rename(
        columns={"spatial": "mcpp_neighborhood"},
    ).assign(mcpp_precinct="east")
    monkeypatch.setattr(data, "load_crime_snapshot", lambda _: (raw.drop(columns="spatial"), {}))
    monkeypatch.setattr(data, "load_mcpp_boundaries", lambda: boundaries.copy())
    monkeypatch.setattr(data, "build_or_load_event_mcpp_lookup", lambda **_: lookup.copy())
    monkeypatch.setattr(data, "load_dashboard_population", lambda: (pd.DataFrame(), 900, {}))
    return data.load_crime_dashboard_context()


def test_context_canonical_domain_and_offense_reconciliation(context, vocabulary):
    analytical = context["valid_time"]
    assert set(analytical.mcpp_neighborhood.dropna()) <= vocabulary
    recognized = set(analytical.loc[analytical.mcpp_neighborhood.notna(), "offense_id"])
    unassigned = set(analytical.loc[analytical.mcpp_neighborhood.isna(), "offense_id"])
    assert recognized.isdisjoint(unassigned)
    assert analytical.offense_id.nunique() == len(recognized) + len(unassigned) == 11
    assert len(analytical) == len(context["df"]) == 12
    assert analytical.groupby("offense_id").mcpp_neighborhood.nunique(dropna=False).eq(1).all()
    assert analytical.report_number.nunique() == 1
    by_id = analytical.drop_duplicates("offense_id").set_index("offense_id")
    assert by_id.loc["spatial-wins", "mcpp_neighborhood"] == "capitol hill"
    assert by_id.loc["stale-spatial", "mcpp_neighborhood"] == "downtown"
    assert by_id.loc["no-match-fallback", "mcpp_neighborhood"] == "downtown"
    assert by_id.loc[by_id.index.str.startswith("invalid-source-"), "mcpp_neighborhood"].isna().all()


def test_coordinate_independence_and_unmappable_row_preservation(context, vocabulary):
    analytical = context["valid_time"].drop_duplicates("offense_id").set_index("offense_id")
    assert analytical.loc["missing-coordinates", "mcpp_neighborhood"] == "capitol hill"
    assert analytical.loc["invalid-coordinates", "mcpp_neighborhood"] == "downtown"
    assert pd.isna(analytical.loc["coordinates-only", "mcpp_neighborhood"])
    points = context["event_mcpp"].set_index("offense_id")
    assert "coordinates-only" in points.index
    assert {"missing-coordinates", "invalid-coordinates"}.isdisjoint(points.index)
    assert pd.isna(points.loc["stale-spatial", "mcpp_neighborhood"])
    assert pd.isna(points.loc["no-match-fallback", "mcpp_neighborhood"])
    unmappable = context["unmappable_events"].set_index("offense_id")
    assert len(unmappable) == 7
    assert unmappable.index.is_unique
    assert unmappable.loc[unmappable.index.str.startswith("invalid-source-"), "mcpp_neighborhood"].isna().all()
    assert set(unmappable.mcpp_neighborhood.dropna()) <= vocabulary
    assert set(points.mcpp_neighborhood.dropna()) <= vocabulary
    assert set(unmappable.index).isdisjoint(points.index)


def test_neighborhood_controls_consume_clean_context_without_changing_citywide(context):
    analytical = context["valid_time"]
    options = make_neighborhood_options(analytical.mcpp_neighborhood)
    assert {option["value"] for option in options} == set(analytical.mcpp_neighborhood.dropna())
    assert filter_crime_records(analytical, None).offense_id.nunique() == 11


@pytest.mark.parametrize("cached", [True, False])
def test_spatial_lookup_validates_cached_and_new_assignments(monkeypatch, boundaries, vocabulary, cached):
    directory = MagicMock()
    directory.__truediv__.return_value.exists.return_value = cached
    monkeypatch.setattr(data, "GEO_PROCESSED_DIR", directory)
    saved = []
    monkeypatch.setattr(pd.DataFrame, "to_parquet", lambda self, *a, **k: saved.append(self.copy()))
    lookup = pd.DataFrame({"offense_id": ["a", "b"],
                           "mcpp_neighborhood": [" Capitol   Hill ", "stale label"],
                           "mcpp_precinct": ["east", "west"]})
    monkeypatch.setattr(pd, "read_parquet", lambda _: lookup.copy())
    events = pd.DataFrame({"offense_id": ["a", "b"], "latitude": [47.62, 47.70],
                           "longitude": [-122.32, -122.32]})
    result = data.build_or_load_event_mcpp_lookup(events, boundaries)
    assert result.offense_id.tolist() == ["a", "b"]
    assert result.mcpp_neighborhood.iloc[0] == "capitol hill"
    assert pd.isna(result.mcpp_neighborhood.iloc[1])
    assert set(result.mcpp_neighborhood.dropna()) <= vocabulary
    assert len(saved) == (0 if cached else 1)
