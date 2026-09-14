"""Queries for the operational Seattle SPD Use of Force stream."""

from datetime import date

UOF_DATASET_ID = "ppi5-g2bj"
TIME_COLUMN = "occured_date_time"  # Preserve the source spelling.
ID_COLUMN = "uniqueid"
UOF_COLUMNS = [
    "uniqueid", "incident_num", "incident_type", "occured_date_time",
    "precinct", "sector", "beat", "officer_id", "subject_id",
    "subject_race", "subject_gender",
]
UOF_ORDER = f"{TIME_COLUMN} DESC, {ID_COLUMN} ASC"


def validate_iso_date(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD date") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be in YYYY-MM-DD format")
    return value


def validate_integer(value: int, name: str, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def build_uof_query_params(
    start_date: str | None = None, *, end_date: str | None = None,
    limit: int = 1000, offset: int = 0,
    columns: list[str] | tuple[str, ...] | None = None,
) -> dict[str, str | int]:
    """Use an inclusive start and exclusive end at local midnight."""
    start_date = validate_iso_date(start_date, "start_date")
    end_date = validate_iso_date(end_date, "end_date")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")
    validate_integer(limit, "limit", 1)
    validate_integer(offset, "offset", 0)
    if columns is not None and not isinstance(columns, (list, tuple)):
        raise ValueError("columns must be a nonempty list or tuple of source columns")
    selected = list(columns) if columns is not None else UOF_COLUMNS
    if not selected or any(column not in UOF_COLUMNS for column in selected):
        raise ValueError("columns must contain known UOF source columns")
    params = {"$select": ",".join(selected), "$order": UOF_ORDER,
              "$limit": limit, "$offset": offset}
    filters = []
    if start_date is not None:
        filters.append(f"{TIME_COLUMN} >= '{start_date}T00:00:00.000'")
    if end_date is not None:
        filters.append(f"{TIME_COLUMN} < '{end_date}T00:00:00.000'")
    if filters:
        params["$where"] = " AND ".join(filters)
    return params
