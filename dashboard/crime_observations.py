"""Immutable, verified bundles of the full fetched crime dataframe."""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal
from uuid import uuid4

import pandas as pd
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from dashboard.crime_source import build_crime_query_params
from dashboard.refresh_schemas import CRIME_INCREMENTAL_SCHEMA


OBSERVATIONS_DIR = Path("data/observations/crime")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def utc_now():
    return datetime.now(timezone.utc)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def classification_provenance():
    return {name: sha256(PROJECT_ROOT / "dashboard" / name) for name in (
        "crime_classification.py", "crime_classification_decisions.py",
    ) if (PROJECT_ROOT / "dashboard" / name).is_file()}


def git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


class Capture(BaseModel):
    """Create once per fetch; reuse this identity when retrying local persistence."""

    observation_id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    manifest_schema_version: Literal[1] = 1
    collection_started_at_utc: AwareDatetime
    collection_finished_at_utc: AwareDatetime
    refresh_mode: Literal["full"] = "full"
    date_column: Literal["offense_date", "report_date_time"]
    query_lower_bound: str
    lower_inclusive: Literal[True] = True
    query_upper_bound: None = None
    upper_inclusive: None = None
    source_time_basis: Literal["America/Los_Angeles"] = "America/Los_Angeles"
    query: dict[str, Any]
    fetch_settings: dict[str, Any]
    pagination_exhausted: bool
    atomic_source_snapshot: Literal[False] = False
    git_commit: str | None = None
    classification_provenance: dict[str, str]
    github_run_id: str | None = None
    github_run_attempt: str | None = None

    @model_validator(mode="after")
    def validate_coverage(self):
        start, end = self.collection_started_at_utc, self.collection_finished_at_utc
        if start.utcoffset().total_seconds() or end.utcoffset().total_seconds() or end < start:
            raise ValueError("Collection timestamps must be ordered UTC timestamps")
        # This schema describes the existing lower-bound-only crime query.
        date = self.query_lower_bound.removesuffix("T00:00:00.000")
        expected = build_crime_query_params(date, date_column=self.date_column,
                                           limit=self.fetch_settings["page_size"])
        if self.query != expected or self.query_lower_bound != date + "T00:00:00.000":
            raise ValueError("Observation query and bounds disagree")
        return self


class Manifest(Capture):
    pagination_exhausted: Literal[True]
    row_count: int = Field(ge=0)
    distinct_offense_id_count: int = Field(ge=0)
    missing_offense_id_count: int = Field(ge=0)
    date_statistics: dict[str, dict[str, Any]]
    columns: list[str]
    snapshot_bytes: int = Field(gt=0)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def new_capture(*, started, finished, start_date, date_column, fetch_settings, exhausted):
    return Capture(
        collection_started_at_utc=started, collection_finished_at_utc=finished,
        date_column=date_column, query_lower_bound=start_date + "T00:00:00.000",
        query=build_crime_query_params(start_date, limit=fetch_settings["page_size"],
                                       date_column=date_column),
        fetch_settings=fetch_settings, pagination_exhausted=exhausted,
        git_commit=git_commit(), classification_provenance=classification_provenance(),
        github_run_id=os.environ.get("GITHUB_RUN_ID"),
        github_run_attempt=os.environ.get("GITHUB_RUN_ATTEMPT"),
    )


def frame_statistics(frame):
    # Nullable IDs/dates and source duplicates are supported by the existing contract.
    CRIME_INCREMENTAL_SCHEMA.validate(frame)
    dates = {}
    for column in ("offense_date", "report_date_time"):
        values = pd.to_datetime(frame[column], errors="coerce")
        dates[column] = {
            "missing": int(frame[column].isna().sum()),
            "invalid_nonmissing": int((frame[column].notna() & values.isna()).sum()),
            "min": None if values.dropna().empty else values.min().isoformat(),
            "max": None if values.dropna().empty else values.max().isoformat(),
        }
    return dict(row_count=len(frame), distinct_offense_id_count=int(frame.offense_id.nunique()),
                missing_offense_id_count=int(frame.offense_id.isna().sum()),
                columns=list(frame.columns), date_statistics=dates)


def read_observation(directory, *, expected_id=None):
    """Verify bytes before Parquet decoding, then reconcile every recorded statistic."""
    directory = Path(directory)
    try:
        metadata = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(metadata)
        if manifest.observation_id != (expected_id or directory.name):
            raise ValueError("Observation directory/manifest identity mismatch")
        snapshot = directory / "snapshot.parquet"
        if snapshot.stat().st_size != manifest.snapshot_bytes or sha256(snapshot) != manifest.snapshot_sha256:
            raise ValueError("Observation snapshot checksum/size mismatch")
        frame = pd.read_parquet(snapshot)
        for key, value in frame_statistics(frame).items():
            if metadata[key] != value:
                raise ValueError(f"Observation {key} mismatch")
        return frame, metadata
    except (OSError, ValueError, KeyError) as error:
        raise ValueError(f"Invalid or incomplete crime observation {directory.name}: {error}") from error


def persist_observation(frame, capture, root=OBSERVATIONS_DIR):
    """Commit a validated sibling directory; an existing identity is never overwritten."""
    capture = Capture.model_validate(capture)
    if not capture.pagination_exhausted:
        raise ValueError("Cannot persist an incomplete crime fetch: pagination was not exhausted")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / capture.observation_id
    metadata = capture.model_dump(mode="json")
    statistics = frame_statistics(frame)

    def verify_retry():
        previous, saved = read_observation(destination)
        if any(saved[key] != value for key, value in metadata.items()):
            raise ValueError("Observation identity already exists with different capture metadata")
        try:
            pd.testing.assert_frame_equal(previous, frame.reset_index(drop=True), check_exact=True)
        except AssertionError as error:
            raise ValueError("Observation identity already exists with different snapshot content") from error
        return destination

    if destination.exists():
        return verify_retry()
    with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
        bundle = Path(temporary) / capture.observation_id
        bundle.mkdir()
        snapshot = bundle / "snapshot.parquet"
        frame.to_parquet(snapshot, index=False)
        manifest = Manifest(**metadata, **statistics, snapshot_bytes=snapshot.stat().st_size,
                            snapshot_sha256=sha256(snapshot))
        (bundle / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        read_observation(bundle)
        try:
            bundle.rename(destination)
        except OSError:
            if not destination.exists():
                raise
            return verify_retry()
    return destination


def observation_directories(root=OBSERVATIONS_DIR):
    """Ignore unpublished sibling workspaces; visible incomplete bundles must fail."""
    root = Path(root)
    return sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith("."))
