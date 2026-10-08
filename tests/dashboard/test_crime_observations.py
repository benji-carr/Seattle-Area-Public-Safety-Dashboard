"""Synthetic collection, release transport, and coverage-aware analysis contracts."""

import json
import hashlib
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

from dashboard import crime_observations as observations
from dashboard.crime_source import crime_records_to_dataframe
from dashboard.crime_observation_analysis import analyze_observations
from dashboard.crime_observation_archive import GitHubArchive, archive_tag, sync_observations, upload_observation
from scripts.dashboard import refresh_crime_data as refresh


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(observations, "git_commit", lambda: "a" * 40)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *a, **k: pytest.fail("No HTTP in observation tests"))


def frame(ids=("a", "a", "b", None), dates=None, category="property crime"):
    dates = dates or ["2026-10-01"] * len(ids)
    return crime_records_to_dataframe([
        dict(offense_id=identity, offense_date=day, report_date_time="2026-10-02",
             offense_category=category, offense_sub_category="test", nibrs_offense_code="100",
             nibrs_crime_against_category="property")
        for identity, day in zip(ids, dates)
    ])


def capture(day="2026-10-05", **kwargs):
    return observations.new_capture(
        started=pd.Timestamp(day + "T18:00:00Z"), finished=pd.Timestamp(day + "T18:05:00Z"),
        start_date=kwargs.pop("start_date", "2026-10-01"),
        date_column=kwargs.pop("date_column", "offense_date"),
        fetch_settings=dict(page_size=5000, max_pages=None, timeout=120, max_retries=5, retry_backoff_seconds=5),
        exhausted=kwargs.pop("exhausted", True), **kwargs,
    )


def test_same_day_distinct_observations_and_manifest_statistics(tmp_path):
    data = frame()
    first = observations.persist_observation(data, capture(), tmp_path)
    second = observations.persist_observation(data, capture(), tmp_path)
    assert first != second
    loaded, manifest = observations.read_observation(first)
    pd.testing.assert_frame_equal(loaded, data)
    assert manifest["row_count"] == 4
    assert manifest["distinct_offense_id_count"] == 2
    assert manifest["missing_offense_id_count"] == 1
    assert manifest["query_upper_bound"] is None
    assert manifest["query"]["$where"] == "offense_date >= '2026-10-01T00:00:00.000'"
    assert manifest["atomic_source_snapshot"] is False
    assert manifest["date_statistics"]["offense_date"]["min"].startswith("2026-10-01")
    assert set(manifest["classification_provenance"]) == {"crime_classification.py", "crime_classification_decisions.py"}


def test_retry_reuses_identity_and_rejects_content_or_metadata_changes(tmp_path):
    data, identity = frame(), capture()
    bundle = observations.persist_observation(data, identity, tmp_path)
    checksum = observations.sha256(bundle / "snapshot.parquet")
    assert observations.persist_observation(data, identity, tmp_path) == bundle
    changed = data.copy()
    changed.loc[0, "offense_id"] = "replacement"
    with pytest.raises(ValueError, match="different snapshot content"):
        observations.persist_observation(changed, identity, tmp_path)
    with pytest.raises(ValueError, match="different capture metadata"):
        observations.persist_observation(data, identity.model_copy(update={"git_commit": "b" * 40}), tmp_path)
    assert observations.sha256(bundle / "snapshot.parquet") == checksum


@pytest.mark.parametrize("damage", ["missing", "json", "bytes", "counts", "identity"])
def test_readers_reject_incomplete_or_corrupt_bundles(tmp_path, damage):
    bundle = observations.persist_observation(frame(), capture(), tmp_path)
    manifest = bundle / "manifest.json"
    if damage == "missing":
        manifest.unlink()
    elif damage == "json":
        manifest.write_text("{")
    elif damage == "bytes":
        (bundle / "snapshot.parquet").write_bytes(b"damaged")
    else:
        payload = json.loads(manifest.read_text())
        payload["row_count" if damage == "counts" else "observation_id"] = 10 if damage == "counts" else "b" * 32
        manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        observations.read_observation(bundle)


def test_interrupted_write_never_exposes_bundle_and_can_retry(monkeypatch, tmp_path):
    identity = capture()
    original = pd.DataFrame.to_parquet
    monkeypatch.setattr(pd.DataFrame, "to_parquet", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        observations.persist_observation(frame(), identity, tmp_path)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setattr(pd.DataFrame, "to_parquet", original)
    observations.persist_observation(frame(), identity, tmp_path)


def test_capture_precedes_dashboard_transformation_and_publication(monkeypatch, tmp_path):
    data = frame(ids=("old", "new", "new"), dates=["2020-01-01", "2026-10-01", "2026-10-01"])
    data["extra_source_field"] = ["keep", "duplicates", "too"]
    calls = []
    def fetch(**kwargs):
        calls.append("fetch")
        kwargs["progress_callback"](dict(page_number=1, rows_fetched_this_page=3, cumulative_rows=3,
                                        page_elapsed_seconds=0, elapsed_seconds=0))
        return data.copy()
    def publish(**kwargs):
        bundles = observations.observation_directories(tmp_path / "archive")
        original, _ = observations.read_observation(bundles[0])
        pd.testing.assert_frame_equal(original, data)
        # A downstream retention operation cannot change already captured evidence.
        trimmed = kwargs["df"].loc[kwargs["df"].offense_date.ge("2026-01-01")]
        assert len(trimmed) == 2 and len(original) == 3
        calls.append("publish")
        return tmp_path / "dashboard.parquet", tmp_path / "metadata.json"
    monkeypatch.setattr(refresh, "load_crime_dataset", fetch)
    monkeypatch.setattr(refresh, "save_crime_snapshot", publish)
    refresh.full_refresh_crime_snapshot("2020-01-01", observation_root=tmp_path / "archive")
    assert calls == ["fetch", "publish"]


def test_archive_failure_stops_dashboard_publication(monkeypatch, tmp_path):
    def fetch(**kwargs):
        kwargs["progress_callback"](dict(page_number=1, rows_fetched_this_page=4, cumulative_rows=4,
                                        page_elapsed_seconds=0, elapsed_seconds=0))
        return frame()
    monkeypatch.setattr(refresh, "load_crime_dataset", fetch)
    monkeypatch.setattr(observations, "persist_observation", Mock(side_effect=OSError("disk full")))
    save = Mock()
    monkeypatch.setattr(refresh, "save_crime_snapshot", save)
    with pytest.raises(OSError):
        refresh.full_refresh_crime_snapshot("2026-10-01", observation_root=tmp_path)
    save.assert_not_called()


class FakeGH:
    """Exercise real gh argument construction without authenticating or uploading."""
    def __init__(self):
        self.releases = []
        self.assets = {}
        self.commands = []
        self.fail_manifest = False
        self.include_digest = True

    def __call__(self, command, **kwargs):
        assert command[0] == "gh"
        self.commands.append(command)
        args = command[1:]
        output = ""
        if args[0] == "api" and "/releases/tags/" in args[1]:
            tag = args[1].rsplit("/", 1)[1]
            match = next((r for r in self.releases if r["tag_name"] == tag), None)
            if match is None:
                raise subprocess.CalledProcessError(1, command, stderr="gh: Not Found (HTTP 404)")
            output = json.dumps(match)
        elif args[0] == "api":
            items = ([dict(name=name, size=len(content), state="uploaded",
                           digest="sha256:" + hashlib.sha256(content).hexdigest() if self.include_digest else None)
                      for name, content in self.assets.items()] if "/assets?" in args[1] else self.releases)
            output = json.dumps([items])
        elif args[:2] == ["release", "create"]:
            assert "--latest=false" in args and "--target" in args
            self.releases.append(dict(id=1, tag_name=args[2], draft=False))
        elif args[:2] == ["release", "upload"]:
            path = Path(args[3])
            assert "--clobber" not in args and path.name not in self.assets
            if self.fail_manifest and path.name.endswith("manifest.json"):
                raise subprocess.CalledProcessError(1, command, stderr="secret-token")
            self.assets[path.name] = path.read_bytes()
        elif args[:2] == ["release", "download"]:
            name = args[args.index("--pattern") + 1]
            path = Path(args[args.index("--output") + 1])
            assert not path.exists()
            path.write_bytes(self.assets[name])
        else:
            pytest.fail(f"Unexpected subprocess {command}")
        return subprocess.CompletedProcess(command, 0, output, "")


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGH()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake, GitHubArchive("owner/repo")


def test_partial_upload_retry_no_overwrites_manifest_last_and_sync(gh, tmp_path):
    fake, archive = gh
    bundle = observations.persist_observation(frame(), capture(), tmp_path / "source")
    fake.fail_manifest = True
    with pytest.raises(RuntimeError, match="authentication") as error:
        upload_observation(bundle, archive)
    assert "secret-token" not in str(error.value)
    assert list(fake.assets) == [bundle.name + ".snapshot.parquet"]
    with pytest.raises(ValueError, match="Incomplete remote"):
        sync_observations(archive, tmp_path / "download")
    assert observations.observation_directories(tmp_path / "download") == []
    fake.fail_manifest = False
    # Artifact extraction restores the original directory/bytes, not a new collection.
    recovered = tmp_path / "recovered" / bundle.name
    shutil.copytree(bundle, recovered)
    upload_observation(recovered, archive)
    assert list(fake.assets)[-1] == bundle.name + ".manifest.json"
    before = len([c for c in fake.commands if c[1:3] == ["release", "upload"]])
    upload_observation(bundle, archive)
    assert len([c for c in fake.commands if c[1:3] == ["release", "upload"]]) == before
    downloaded = sync_observations(archive, tmp_path / "download")
    assert observations.read_observation(downloaded[0])[1] == observations.read_observation(bundle)[1]
    fake.commands.clear()
    sync_observations(archive, tmp_path / "download")
    assert not any("--pattern" in c and c[c.index("--pattern") + 1].endswith("snapshot.parquet") for c in fake.commands)


@pytest.mark.parametrize("part", ["snapshot.parquet", "manifest.json"])
def test_remote_mismatch_is_never_overwritten_and_download_is_not_published(gh, tmp_path, part):
    fake, archive = gh
    bundle = observations.persist_observation(frame(), capture(), tmp_path / "source")
    upload_observation(bundle, archive)
    fake.assets[bundle.name + "." + part] = b"corrupt"
    with pytest.raises(ValueError, match="mismatch"):
        upload_observation(bundle, archive)
    with pytest.raises(ValueError):
        sync_observations(archive, tmp_path / "download")
    assert observations.observation_directories(tmp_path / "download") == []


def test_manifest_only_partial_can_be_repaired(gh, tmp_path):
    fake, archive = gh
    bundle = observations.persist_observation(frame(), capture(), tmp_path)
    upload_observation(bundle, archive)
    del fake.assets[bundle.name + ".snapshot.parquet"]
    fake.commands.clear()
    upload_observation(bundle, archive)
    uploads = [Path(c[4]).name for c in fake.commands if c[1:3] == ["release", "upload"]]
    assert uploads == [bundle.name + ".snapshot.parquet"]


def test_verified_local_copy_does_not_hide_remote_corruption(gh, tmp_path):
    fake, archive = gh
    bundle = observations.persist_observation(frame(), capture(), tmp_path / "source")
    upload_observation(bundle, archive)
    sync_observations(archive, tmp_path / "download")
    fake.include_digest = False
    sync_observations(archive, tmp_path / "download")  # Byte verification fallback.
    original = fake.assets[bundle.name + ".snapshot.parquet"]
    fake.assets[bundle.name + ".snapshot.parquet"] = b"x" * len(original)
    with pytest.raises(ValueError, match="checksum"):
        sync_observations(archive, tmp_path / "download")
    fake.include_digest = True
    with pytest.raises(ValueError, match="checksum"):
        sync_observations(archive, tmp_path / "download")


def test_archive_sync_analysis_cli_roundtrip(gh, tmp_path, capsys):
    from scripts.dashboard import archive_crime_observations, sync_crime_observations, analyze_crime_observations
    bundle = observations.persist_observation(frame(), capture(), tmp_path / "source")
    archive_crime_observations.main(["--repo", "owner/repo", "--root", str(tmp_path / "source")])
    sync_crime_observations.main(["--repo", "owner/repo", "--root", str(tmp_path / "download"), "--month", "2026-10"])
    assert bundle.name in capsys.readouterr().out
    analyze_crime_observations.main(["--root", str(tmp_path / "download"), "--start-date", "2026-10-01", "--end-date", "2026-10-02"])
    output = capsys.readouterr()
    assert "distinct_offenses" in output.out and "total_change" in output.out
    assert "analysis_classification_provenance" in output.err


def test_utc_collection_month_controls_release_tag():
    identity = capture("2026-11-01").model_copy(update={
        "collection_started_at_utc": pd.Timestamp("2026-11-01T00:01:00Z"),
    })
    # Still October in Seattle, but the release month is November UTC.
    manifest = dict(identity.model_dump(mode="json"), **observations.frame_statistics(frame()),
                    snapshot_bytes=10, snapshot_sha256="a" * 64)
    assert archive_tag(manifest) == "crime-observations-2026-11"


def test_distinct_counts_revisions_membership_zero_unobserved_and_gaps(tmp_path):
    first = observations.persist_observation(frame(("a", "a", "b")), capture("2026-10-05"), tmp_path)
    # Same total, changed membership. Missing Oct 6 collection remains missing.
    second = observations.persist_observation(frame(("b", "c")), capture("2026-10-07"), tmp_path)
    revised = frame(("b", "c"), ["2026-10-02", "2026-10-01"], category="violent crime")
    third = observations.persist_observation(revised, capture("2026-10-08"), tmp_path)
    result = analyze_observations(tmp_path, "2026-09-30", "2026-10-09")
    def row(identity, date, category="all"):
        return result.loc[result.observation_id.eq(identity.name) & result.offense_date.eq(date) & result.category.eq(category)].iloc[0]
    assert row(first, "2026-10-01").distinct_offenses == 2
    assert row(second, "2026-10-01").total_change == 0
    assert row(second, "2026-10-01").hours_since_previous_observation == 48
    assert set(observations.read_observation(first)[0].offense_id) != set(observations.read_observation(second)[0].offense_id)
    assert row(third, "2026-10-01").total_change == -1
    assert row(third, "2026-10-02").total_change == 1
    assert row(first, "2026-10-02").distinct_offenses == 0
    assert pd.isna(row(first, "2026-09-30").distinct_offenses)
    assert row(first, "2026-10-05").coverage_status == "partial_boundary"
    assert pd.isna(row(first, "2026-10-05").distinct_offenses)
    assert pd.isna(row(first, "2026-10-09").distinct_offenses)
    assert result.observation_id.nunique() == 3
    categories = result.loc[result.observation_id.eq(third.name) & result.offense_date.eq("2026-10-01")].set_index("category")
    assert categories.loc["crimes against persons", "distinct_offenses"] == 1
    assert categories.loc["crimes against property", "distinct_offenses"] == 0


def test_report_filtered_offense_date_coverage_never_implies_zero(tmp_path):
    observations.persist_observation(frame(), capture(date_column="report_date_time"), tmp_path)
    result = analyze_observations(tmp_path, "2026-10-01", "2026-10-02")
    assert result.coverage_status.eq("report_date_filtered").all()
    assert result.loc[result.offense_date.eq("2026-10-02"), "distinct_offenses"].isna().all()
    assert result.total_change.isna().all()


def test_empty_exhausted_capture_produces_covered_zeros_only(tmp_path):
    observations.persist_observation(frame(()), capture(), tmp_path)
    result = analyze_observations(tmp_path, "2026-10-04", "2026-10-05")
    assert result.loc[result.offense_date.eq("2026-10-04"), "distinct_offenses"].eq(0).all()
    assert result.loc[result.offense_date.eq("2026-10-05"), "distinct_offenses"].isna().all()


def test_partial_boundary_positive_counts_and_not_a_crime_category_exclusion(tmp_path):
    data = frame(("a", "a"), ["2026-10-05", "2026-10-05"])
    data["nibrs_crime_against_category"] = "not_a_crime"
    observations.persist_observation(data, capture(), tmp_path)
    result = analyze_observations(tmp_path, "2026-10-05", "2026-10-05").set_index("category")
    assert result.loc["all", "distinct_offenses"] == 1
    assert result.drop(index="all").distinct_offenses.isna().all()
    assert result.coverage_status.eq("partial_boundary").all()


@pytest.mark.parametrize("page_lengths,max_pages,complete", [
    ([2], 1, False), ([2, 2], 2, False),
    ([2, 1], 2, True), ([2, 2, 0], None, True), ([2, 2, 0], 3, True), ([0], 1, True),
])
def test_pagination_completion_and_github_provenance(monkeypatch, tmp_path, page_lengths, max_pages, complete):
    from dashboard import crime_source
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    offsets = []
    def page(**kwargs):
        offsets.append(kwargs["offset"])
        length = page_lengths[kwargs["offset"] // 2]
        return frame(tuple(str(kwargs["offset"] + n) for n in range(length))).to_dict("records")
    monkeypatch.setattr(crime_source, "fetch_crime_page", page)
    options = dict(page_size=2, max_pages=max_pages, output_directory=tmp_path / "dashboard",
                   observation_root=tmp_path / "archive")
    if not complete:
        with pytest.raises(ValueError, match="exhausted pagination"):
            refresh.full_refresh_crime_snapshot("2026-10-01", **options)
        assert not (tmp_path / "dashboard").exists()
        assert not (tmp_path / "archive").exists()
        assert offsets == list(range(0, 2 * len(page_lengths), 2))
        return
    refresh.full_refresh_crime_snapshot("2026-10-01", **options)
    _, manifest = observations.read_observation(observations.observation_directories(tmp_path / "archive")[0])
    assert offsets == list(range(0, 2 * len(page_lengths), 2))
    assert manifest["pagination_exhausted"] is True
    assert manifest["row_count"] == sum(page_lengths)
    assert manifest["fetch_settings"]["max_pages"] == max_pages
    assert manifest["github_run_id"] == "123" and manifest["github_run_attempt"] == "2"


def test_incomplete_capture_cannot_be_persisted_or_read_as_completed(tmp_path):
    with pytest.raises(ValueError, match="incomplete crime fetch"):
        observations.persist_observation(frame(), capture(exhausted=False), tmp_path)
    assert list(tmp_path.iterdir()) == []
    bundle = observations.persist_observation(frame(), capture(), tmp_path)
    metadata_path = bundle / "manifest.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["pagination_exhausted"] = False
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="pagination_exhausted"):
        observations.read_observation(bundle)


def test_later_crime_freshness_failure_leaves_bundle_for_archive_and_backup(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "get_default_start_date", lambda **kw: "2026-10-01")
    monkeypatch.setattr(refresh, "CRIME_OUTPUT_DIR", tmp_path / "dashboard")
    def fetch(**kwargs):
        kwargs["progress_callback"](dict(page_number=1, rows_fetched_this_page=4, cumulative_rows=4,
                                        page_elapsed_seconds=0, elapsed_seconds=0))
        return frame()
    monkeypatch.setattr(refresh, "load_crime_dataset", fetch)
    monkeypatch.setattr(refresh, "check_crime_freshness", Mock(side_effect=ValueError("source stale")))
    with pytest.raises(ValueError, match="source stale"):
        refresh.main(observation_root=tmp_path / "archive")
    bundles = observations.observation_directories(tmp_path / "archive")
    assert len(bundles) == 1
    observations.read_observation(bundles[0])


def test_workflow_failure_guards_backup_and_effective_token_permissions():
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/daily_spd_refresh.yml").read_text()
    recovery = (root / ".github/workflows/dashboard_freshness_recovery.yml").read_text()
    steps = workflow.split("      - name: ")[1:]
    archive = next(s for s in steps if s.startswith("Archive crime observation"))
    backup = next(s for s in steps if s.startswith("Back up completed crime observations"))
    assert "!cancelled()" in archive and "success()" not in archive
    assert "steps.crime_collection.outcome != 'skipped'" in archive
    assert '"${{ steps.crime_collection.outcome }}" = "failure"' in archive
    assert "! compgen -G" in archive and "original collection failure remains fatal" in archive
    assert "always()" in backup and "success()" not in backup
    assert "actions/upload-artifact@v4" in backup
    assert "retention-days: 7" in backup and "if-no-files-found: ignore" in backup
    assert "include-hidden-files: false" in backup
    assert "crime-observations/*/snapshot.parquet" in backup and "crime-observations/*/manifest.json" in backup
    assert "${{ github.run_id }}-${{ github.run_attempt }}" in backup
    assert steps.index(archive) < steps.index(backup) < next(i for i, s in enumerate(steps) if s.startswith("Refresh SPD calls data"))
    # Normal steps retain GitHub's implicit success() guard: backup cannot erase an earlier failure.
    for step in steps:
        if step.startswith(("Refresh SPD calls data", "Refresh SPD use of force data", "Commit refreshed data", "Push refresh branch", "Open and merge")):
            assert "always()" not in step and "!cancelled()" not in step
            assert "continue-on-error" not in step
    assert "continue-on-error" not in archive
    assert "permissions:\n  contents: write\n  pull-requests: write" in workflow
    assert "      contents: write\n      pull-requests: write" in recovery
    assert "only_if_stale: true" in recovery
    assert 'cron: "17 10 * * *"' in workflow and 'cron: "17 16 * * *"' in recovery
    assert "cancel-in-progress: false" in workflow
    assert "if [ \"$ONLY_IF_STALE\" != \"true\" ]" in workflow


def test_analysis_rejects_visible_incomplete_bundle(tmp_path):
    (tmp_path / ("a" * 32)).mkdir()
    with pytest.raises(ValueError, match="incomplete"):
        analyze_observations(tmp_path, "2026-10-01", "2026-10-02")


def test_workflow_archives_before_unrelated_dataset_failure():
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/daily_spd_refresh.yml").read_text()
    steps = workflow.split("      - name: ")
    commands = []
    for step in steps:
        commands.extend(line.strip() for line in step.splitlines() if line.strip().startswith("python -m scripts.dashboard."))
    events = []
    for command in commands:
        if "refresh_crime_data" in command:
            assert "--observation-root" in command
            events.append("crime")
        elif "archive_crime_observations" in command:
            events.append("archive")
        elif "refresh_call_metrics" in command:
            events.append("calls failure")
            break
        elif "refresh_uof_data" in command:
            pytest.fail("UOF must not precede crime archival")
    assert events == ["crime", "archive", "calls failure"]
    archive_step = next(step for step in steps if step.startswith("Archive crime observation"))
    assert "GH_TOKEN: ${{ github.token }}" in archive_step
    assert "!cancelled()" in archive_step
    assert "cancel-in-progress: false" in workflow


@pytest.mark.parametrize("missing_reads", [0, 2, 5])
def test_release_visibility_retry_is_bounded(gh, monkeypatch, missing_reads):
    fake, archive = gh
    # The capture fixture is not a manifest: only these fields are needed here.
    manifest = {"collection_started_at_utc": "2026-10-07T12:00:00Z", "git_commit": None}
    monkeypatch.setattr("dashboard.crime_observation_archive.archive_tag", lambda _: "crime-observations-2026-10")
    published = dict(id=1, tag_name="crime-observations-2026-10", draft=False)
    reads = iter([None] + [None] * missing_reads + [published])
    monkeypatch.setattr(archive, "release_by_tag", lambda _: next(reads))
    sleeps = []
    monkeypatch.setattr("dashboard.crime_observation_archive.time.sleep", sleeps.append)
    if missing_reads == 5:
        with pytest.raises(RuntimeError, match="bounded retries"):
            archive.ensure_release(manifest)
        assert sleeps == [1, 2, 4, 8]
    else:
        assert archive.ensure_release(manifest) == published
        assert len(sleeps) == missing_reads
    assert sum(c[1:3] == ["release", "create"] for c in fake.commands) == 1


def test_existing_draft_is_not_published_or_recreated(gh, tmp_path):
    fake, archive = gh
    fake.releases.append(dict(id=1, tag_name="crime-observations-2026-10", draft=True))
    with pytest.raises(RuntimeError, match="is a draft"):
        archive.ensure_release(observations.read_observation(observations.persist_observation(frame(), capture(), tmp_path))[1])
    assert not any(c[1:3] == ["release", "create"] for c in fake.commands)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_release_lookup_errors_do_not_trigger_creation(monkeypatch, status):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, stderr=f"secret-token (HTTP {status})")
    monkeypatch.setattr(subprocess, "run", fail)
    archive = GitHubArchive("owner/repo")
    monkeypatch.setattr("dashboard.crime_observation_archive.archive_tag", lambda _: "crime-observations-2026-10")
    with pytest.raises(RuntimeError, match="authentication") as error:
        archive.ensure_release({})
    assert "secret-token" not in str(error.value)
