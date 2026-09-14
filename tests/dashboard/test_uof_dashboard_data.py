import pandas as pd
import pytest

from dashboard.uof_data import uof_records_to_dataframe
from dashboard.uof_dashboard_data import (
    OUTSIDE_OR_UNKNOWN, count_ois_events, count_uof_incidents,
    derive_ois_events, is_ois_record, normalize_ois_beat,
)


def test_ois_matching_requires_standalone_term():
    labels = pd.Series(["Level 3 - OIS", "ois", "Other (OiS)", "OIS-related", "NOIS", "OISX", None, "Level 2"])
    assert is_ois_record(labels).tolist() == [True, True, True, True, False, False, False, False]


def test_outside_and_unknown_beats_normalize_together():
    beats = pd.Series([None, pd.NA, float("nan"), "", "  ", " - ", " oOj ", "99", "NaN", " none ", "<na>"])
    assert normalize_ois_beat(beats).eq(OUTSIDE_OR_UNKNOWN).all()
    assert normalize_ois_beat(pd.Series([" k1 ", "l2", "Q2", "u3"])).tolist() == ["K1", "L2", "Q2", "U3"]


def test_multiple_rows_incidents_officers_subjects_and_times_form_one_event():
    frame = uof_records_to_dataframe([
        {"uniqueid": str(i), "incident_num": str(i), "officer_id": str(i), "subject_id": str(i),
         "incident_type": "ois", "beat": beat, "occured_date_time": f"2024-04-17T{hour}:00:00"}
        for i, (beat, hour) in enumerate([(" k1 ", "01"), ("K1", "12"), ("k1", "23")])
    ])
    original = frame.copy(deep=True)
    event = derive_ois_events(frame)
    assert len(event) == 1
    assert event.ois_event_key.iloc[0] == "2024-04-17|K1"
    assert event.uof_rows.iloc[0] == event.force_incidents.iloc[0] == 3
    assert event.unique_officers.iloc[0] == event.unique_subjects.iloc[0] == 3
    assert event.first_occurrence.iloc[0] == pd.Timestamp("2024-04-17T01:00:00")
    assert event.last_occurrence.iloc[0] == pd.Timestamp("2024-04-17T23:00:00")
    pd.testing.assert_frame_equal(event, derive_ois_events(frame.iloc[::-1]))
    pd.testing.assert_frame_equal(frame, original)


def test_2015_09_29_distinct_beats_remain_two_events():
    frame = uof_records_to_dataframe([
        {"incident_type": "OIS", "occured_date_time": "2015-09-29", "beat": beat}
        for beat in ["K1", "L2"]
    ])
    assert derive_ois_events(frame).ois_event_key.tolist() == ["2015-09-29|K1", "2015-09-29|L2"]


@pytest.mark.parametrize("outside_beat", ["99", "OOJ"])
def test_2024_04_17_outside_encodings_collapse_to_one_event(outside_beat):
    frame = uof_records_to_dataframe([
        {"incident_type": "Level 3 - OIS", "occured_date_time": "2024-04-17", "beat": beat}
        for beat in ["-", outside_beat]
    ])
    events = derive_ois_events(frame)
    assert events.ois_event_key.tolist() == ["2024-04-17|OUTSIDE_OR_UNKNOWN"]
    assert events.uof_rows.iloc[0] == 2


def test_non_ois_and_invalid_dates_do_not_form_events_and_empty_schema_is_stable():
    frame = uof_records_to_dataframe([
        {"incident_type": "Level 1", "occured_date_time": "2024-01-01"},
        {"incident_type": "OIS", "occured_date_time": "invalid"},
    ])
    events = derive_ois_events(frame)
    assert events.empty
    assert list(events.columns) == ["ois_event_key", "event_date", "normalized_beat", "first_occurrence",
                                   "last_occurrence", "uof_rows", "force_incidents", "unique_officers", "unique_subjects"]
    assert derive_ois_events(uof_records_to_dataframe([])).empty
    assert count_ois_events(events, "2024-01-01", "2024-01-02") == 0


def test_local_calendar_dates_keep_naive_time_and_convert_aware_time():
    frame = uof_records_to_dataframe([
        {"incident_type": "OIS", "occured_date_time": timestamp, "beat": "K1"}
        for timestamp in ["2024-04-17T23:00:00", "2024-04-18T06:00:00Z", "2024-04-18T00:00:00"]
    ])
    events = derive_ois_events(frame)
    assert events.ois_event_key.tolist() == ["2024-04-17|K1", "2024-04-18|K1"]
    assert events.uof_rows.tolist() == [2, 1]


def test_counts_unique_incidents_and_events_with_inclusive_calendar_endpoints():
    frame = uof_records_to_dataframe([
        {"incident_num": incident, "incident_type": "OIS", "occured_date_time": timestamp, "beat": "K1"}
        for incident, timestamp in [("a", "2024-01-01T00:00:00"), ("a", "2024-01-01T12:00:00"),
                                    ("b", "2024-01-02T23:59:59.999999"), ("c", "2024-01-03"),
                                    (" ", "2024-01-01")]
    ])
    assert count_uof_incidents(frame, "2024-01-01", "2024-01-02") == 2
    assert count_uof_incidents(frame, "2024-01-02", "2024-01-02") == 1
    events = derive_ois_events(frame)
    duplicated = pd.concat([events, events], ignore_index=True)
    assert count_ois_events(duplicated, "2024-01-01", "2024-01-02") == 2
    with pytest.raises(ValueError):
        count_uof_incidents(frame, "2024-01-03", "2024-01-01")
