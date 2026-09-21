# Crime classification v1.1

`dashboard/crime_classification_decisions.py` owns the three canonical category
constants and 44 finalized decisions promoted from the reviewed QA notebook.
Decision keys are normalized `(offense_sub_category, nibrs_offense_code)` pairs;
each decision records an action, target category, and reason. Production does
not import notebook code, and no production decision has a `review` action.

`dashboard/crime_classification.py::apply_crime_classification` requires the
source category, subcategory, NIBRS code, and NIBRS crime-against category. It
preserves the normalized original category as `source_offense_category`, applies
reviewed pair decisions, and otherwise maps source categories as follows:

| Source category | Analytical category |
| --- | --- |
| violent crime | crimes against persons |
| property crime | crimes against property |
| all other / legacy other / remaining values | crimes against society / other |

Already canonical inputs retain their category. Explicit pair decisions override
the defaults, including moving reviewed offenses from the source other category
to Persons or Property. Normalization trims whitespace and ignores case.

Exclusions are represented by `is_excluded_from_crime_analysis = True`,
`classification_action = "exclude"`, a `classification_reason`, and a missing
analytical `offense_category`. Reviewed exclusions are Justifiable Homicide
(`justifiable homicide`, `09c`), (`unknown`, `-`), and records whose source
`nibrs_crime_against_category` explicitly means `not_a_crime`. Spaces, hyphens,
and underscores in that semantic field normalize to underscores. This semantic
exclusion overrides inclusion decisions. The presence of `999` in a code,
subcategory, description, or other text is not itself an exclusion.

`prepare_crime_snapshot` retains all classified rows for QA. Only after assigning
the final category does it copy that category to `event_group` and
`event_importance_bin`. `load_crime_dashboard_context` retains the full prepared
snapshot in `context["df"]`, then selects included rows into `analysis_df` before
constructing `valid_time`, map events, and unmappable events. Analytical consumers
must use these included derivatives, not count the QA snapshot directly. The
shared filter also removes explicitly excluded rows when given the QA snapshot.

Coordinate validity is never a classification or citywide analytical requirement.
All included offenses with usable IDs and dates enter `valid_time`, including
offenses with missing or invalid coordinates. Neighborhood selection uses spatial
assignments with the source neighborhood as a fallback. Map points alone require
valid coordinates. Controls, daily figures, neighborhood shading, and fullscreen
figures consume the included context derivatives. Red, green, and blue are
preserved for Persons, Property, and Society / Other respectively; UI labels use
title case while analytical values remain lowercase.

## Local snapshot reconciliation

Verified against the snapshot refreshed at `2026-09-11T20:36:00.093773+00:00`.
These counts describe this snapshot, not fixed expectations for future refreshes.

| Population | Rows | Unique offenses |
| --- | ---: | ---: |
| Before classification | 156,427 | 156,420 |
| Included | 138,674 | 138,667 |
| Excluded | 17,753 | 17,753 |

Included and excluded offense IDs are disjoint and reconcile to the original
population. Every included offense has exactly one canonical category.

| Final analytical category | Unique offenses |
| --- | ---: |
| crimes against persons | 24,497 |
| crimes against property | 93,987 |
| crimes against society / other | 20,183 |

The exclusions comprise 17,673 source `999` pairs explicitly marked
`not_a_crime`, 7 justifiable homicides also marked `not_a_crime`, and 73
`unknown` / `-` pairs. All 138,667 included unique offenses enter `valid_time`;
118,165 have valid map coordinates and 20,502 remain analytical without them.

The first two code cells of `08_crime_volume_trends.ipynb` and
`v1_1_metric_methodology.ipynb` were executed against the production context.
Both report only the three canonical types and the canonical-taxonomy PASS.
The methodology population check reports zero explicit non-crimes, missing
categories, or noncanonical categories in the analytical population.
