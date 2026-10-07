"""Pydantic contracts for existing refresh settings; no date normalization."""

from typing import Annotated, Literal, get_args

from pydantic import AfterValidator, BeforeValidator, BaseModel, ConfigDict, Field, StrictFloat, StrictInt, TypeAdapter


PositiveInt = Annotated[StrictInt, Field(ge=1)]
NonnegativeInt = Annotated[StrictInt, Field(ge=0)]
CrimeDateColumn = Literal["offense_date", "report_date_time"]
VALID_CRIME_DATE_COLUMNS = set(get_args(CrimeDateColumn))


class CrimeDateConfig(BaseModel):
    date_column: CrimeDateColumn


def _positive(value):
    # Legacy calls/crime checks deliberately did not reject NaN or +inf.
    if value <= 0:
        raise ValueError("must be larger than zero")
    return value


def _nonnegative(value):
    if value < 0:
        raise ValueError("cannot be negative")
    return value


def _native_number(value):
    # StrictFloat also accepts Decimal and objects with __float__; callers did not.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be an integer or float")
    return value


PositiveNumber = Annotated[StrictInt | StrictFloat, BeforeValidator(_native_number), AfterValidator(_positive)]
NonnegativeNumber = Annotated[StrictInt | StrictFloat, BeforeValidator(_native_number), AfterValidator(_nonnegative)]
FinitePositiveNumber = Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False), BeforeValidator(_native_number)]
FiniteNonnegativeNumber = Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False), BeforeValidator(_native_number)]


class PaginationConfig(BaseModel):
    page_size: PositiveInt
    max_pages: PositiveInt | None = None


class QueryPagination(BaseModel):
    limit: PositiveInt
    offset: NonnegativeInt


class RetryConfig(BaseModel):
    max_retries: NonnegativeInt
    retry_backoff_seconds: NonnegativeNumber


class FiniteRetryConfig(RetryConfig):
    retry_backoff_seconds: FiniteNonnegativeNumber


class ScalarTimeoutConfig(BaseModel):
    timeout: PositiveNumber


class RequestTimeoutConfig(BaseModel):
    model_config = ConfigDict(strict=True)
    timeout: PositiveNumber | tuple[PositiveNumber, PositiveNumber]


class FiniteTimeoutConfig(BaseModel):
    timeout: FinitePositiveNumber


class SplitTimeoutConfig(BaseModel):
    connect_timeout: PositiveNumber
    read_timeout: PositiveNumber


class RollingWindowConfig(BaseModel):
    rolling_window_days: PositiveInt


class SnapshotRefreshConfig(PaginationConfig, ScalarTimeoutConfig):
    pass


class RollingRefreshConfig(RollingWindowConfig, ScalarTimeoutConfig):
    overlap_days: NonnegativeInt
    page_size: PositiveInt


class ACSConfig(BaseModel):
    acs_year: Annotated[StrictInt, Field(ge=2009)]


def validate_integer(value: int, name: str, minimum: int) -> None:
    """Compatibility entrypoint for named scalar settings, including overlap."""
    try:
        TypeAdapter(Annotated[StrictInt, Field(ge=minimum)]).validate_python(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer >= {minimum}: {error}") from error


def validate_positive_int(value: int, name: str) -> None:
    validate_integer(value, name, 1)


def validate_nonnegative_int(value: int, name: str) -> None:
    validate_integer(value, name, 0)


def validate_timeout(timeout: float) -> None:
    ScalarTimeoutConfig(timeout=timeout)
