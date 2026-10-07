from datetime import date

from dashboard.refresh_models import QueryPagination


SPD_CALL_COLUMNS = [
    "cad_event_number",
    "cad_event_original_time_queued",
    "cad_event_arrived_time",
    "cad_event_clearance_description",
    "call_sign_dispatch_id",
    "call_type",
    "priority",
    "initial_call_type",
    "final_call_type",
    "cad_event_response_category",
    "dispatch_precinct",
    "dispatch_sector",
    "dispatch_beat",
    "dispatch_neighborhood",
    "dispatch_latitude",
    "dispatch_longitude",
    "count_of_officers",
    "event_group",
]


def _validate_iso_date(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be a valid date in YYYY-MM-DD format") from error
    if parsed_date.isoformat() != value:
        raise ValueError(f"{field_name} must be in YYYY-MM-DD format")
    return value


def build_spd_call_query_params(
    start_date: str | None = None,
    *,
    end_date: str | None = None,
    limit: int = 1000,
    offset: int = 0,
    columns: list[str] | tuple[str, ...] | None = None,
    order: str = "cad_event_original_time_queued DESC",
) -> dict[str, str | int]:
    start_date = _validate_iso_date(start_date, "start_date")
    end_date = _validate_iso_date(end_date, "end_date")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")

    QueryPagination(limit=limit, offset=offset)

    selected_columns = list(columns) if columns is not None else SPD_CALL_COLUMNS
    if not selected_columns:
        raise ValueError("columns cannot be empty")

    filters: list[str] = []
    if start_date is not None:
        filters.append(f"cad_event_original_time_queued >= '{start_date}T00:00:00.000'")
    if end_date is not None:
        filters.append(f"cad_event_original_time_queued < '{end_date}T00:00:00.000'")

    params = {
        "$select": ",".join(selected_columns),
        "$order": order,
        "$limit": limit,
        "$offset": offset,
    }
    if filters:
        params["$where"] = " AND ".join(filters)
    return params

if __name__ == "__main__":
    params = build_spd_call_query_params(
        start_date="2025-01-01",
        limit=500,
        offset=1000,
    )

    print(params)

