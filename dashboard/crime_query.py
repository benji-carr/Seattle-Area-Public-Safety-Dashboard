from datetime import date

from dashboard.refresh_models import CrimeDateConfig, QueryPagination, VALID_CRIME_DATE_COLUMNS


CRIME_COLUMNS = [
    'report_number', 
    'report_date_time', 
    'offense_id', 
    'offense_date', 
    'nibrs_group_a_b', 
    'nibrs_crime_against_category', 
    'offense_sub_category', 
    'shooting_type_group', 
    'block_address', 
    'latitude', 
    'longitude', 
    'beat', 
    'precinct', 
    'sector', 
    'neighborhood', 
    'reporting_area', 
    'offense_category', 
    'nibrs_offense_code_description', 
    'nibrs_offense_code', 
    'census_block_2020'
]

def build_crime_query_params(
        start_date: str,
        limit: int = 1000,
        offset: int = 0,
        date_column : str = "offense_date", 
) -> dict[str, str | int]:
    CrimeDateConfig(date_column=date_column)

    if not isinstance(start_date, str):
            raise ValueError("date must be a string")
        
    try:
        parsed_date = date.fromisoformat(start_date)
    except ValueError as error:
        raise ValueError("start_date must be a valid date in YYYY-MM-DD format") from error
        
    if parsed_date.isoformat() != start_date:
        raise ValueError("start_date must be in YYYY-MM-DD format")
    
    QueryPagination(limit=limit, offset=offset)

    params = {
            "$select": ",".join(CRIME_COLUMNS),
            "$where": (f"{date_column} >= '{start_date}T00:00:00.000'"),
            "$order": f"{date_column} DESC, offense_id ASC",
            "$limit": limit, 
            "$offset": offset,
        }
    return params


if __name__ == "__main__":
    params = build_crime_query_params(
        start_date="2025-01-01",
        limit=500,
        offset=1000,
    )

    print(params)
    print(build_crime_query_params("2025-01-01", date_column="offense_date"))
    print(build_crime_query_params("2025-01-01", date_column="report_date_time"))
