# Daily crime observations

This archive preserves the complete dataframe returned by the existing full crime
fetch, including source duplicate IDs and all fetched fields. It is captured
before dashboard date preparation, sorting, or any downstream retention step.
The current full-refresh path does not trim rows after fetching; its lower-bound
query already limits the pull. Incremental refresh, dashboard retention,
classification, and freshness checks are unchanged.

No historical observations are fabricated. We do not calculate time-to-stability,
choose stabilization thresholds, or store derived daily/category totals.

## Collect locally

Install the ordinary runtime requirements. The following command **fetches live
data and refreshes the local dashboard**, with observation tracking enabled:

```powershell
python -m scripts.dashboard.refresh_crime_data --observation-root data/observations/crime
```

Without the option, existing callers behave as before. Python callers may pass
`observation_root=...` to `full_refresh_crime_snapshot`. This reuses its single
paginated dataset fetch. The existing latest-record queries for choosing a start
date and checking freshness remain separate and unchanged.

Each collection gets a random observation ID, even on the same day. Its bundle is:

```text
data/observations/crime/<observation_id>/
    snapshot.parquet
    manifest.json
```

The default directory is Git-ignored. Custom roots must also remain outside Git.
The manifest records UTC collection start/end, the actual query and inclusive
lower bound, `query_upper_bound: null`, query date column, pagination exhaustion,
fetch settings, row/distinct-nonmissing-ID counts, missing/invalid dates and
timestamp extrema, column order, Git commit, classification source-file hashes,
GitHub run/attempt when available, and snapshot byte size/SHA-256.

Collection timestamps describe when we observed the source. They are **not source
publication timestamps**. Offset pagination does not provide an atomic source
snapshot: records may change while pages are fetched. `atomic_source_snapshot`
is always false. The capture is the service's existing normalized dataframe,
not a claim to preserve the original HTTP JSON byte-for-byte.

Local persistence writes a temporary sibling workspace, validates Parquet and
manifest, then renames the complete bundle into place. Failed writes stop the
tracked refresh before dashboard publication. Tracking also requires confirmed
pagination exhaustion: a short final page, including an empty page after an
exact multiple of page size. Hitting `max_pages` on a full page fails before
creating a completed observation or publishing dashboard data. Completed-bundle
readers reject manifests with `pagination_exhausted: false`. Hidden `.pending-*` workspaces
left by a killed process are unpublished and ignored. Visible incomplete or
corrupt bundles cause reader failure.

To retry persistence in Python, retain the same `Capture` object returned by
`new_capture` and the same dataframe, then call `persist_observation` again. It
checks existing metadata and exact dataframe content instead of overwriting.
A new source fetch is a new observation, not a persistence retry.

## Upload the archive

Authenticate the installed `gh` CLI for the intended repository, then run:

```powershell
python -m scripts.dashboard.archive_crime_observations --repo OWNER/REPO --root data/observations/crime
```

The CLI groups observations by **UTC collection-start month**:

```text
release/tag: crime-observations-2026-10
    <observation_id>.snapshot.parquet
    <observation_id>.manifest.json
```

Dedicated archive releases are prereleases explicitly marked `--latest=false`.
Their tags point to the first capture's recorded commit (or `main` when unavailable).
Each manifest retains its own commit. These tags are archive labels, not software
versions. The implementation uses authenticated `gh` commands, including paginated
`gh api` release/asset inventories; it does not make its own GitHub HTTP requests.

The uploader looks up the monthly release directly by tag. After creating a
release, it retries a missing lookup up to five times, with waits of 1, 2, 4 and
8 seconds. Other API failures remain fatal. An existing draft is not automatically
published: publish it deliberately before retrying the original bundle. A release
still missing after the bounded retries produces a separate error. This lookup
avoids relying on immediate visibility in the release-list inventory.

Existing assets are downloaded and compared before any missing asset is uploaded.
Uploads never use `--clobber`. The snapshot is uploaded and verified first, and
the manifest is uploaded last as the completion signal. Readers still require
both files and a matching snapshot checksum: a manifest alone is insufficient.
If an upload is interrupted, rerun the same command against the **same local
bundle**. Matching assets are retained and only missing assets are uploaded.
Conflicting remote content fails without replacement. Do not fix a conflict by
clobbering evidence.

GitHub CLI errors omit subprocess stderr to avoid leaking authenticated URLs or
secrets. Check `gh auth status`, repository access, release mutability, and network
connectivity when an archive command fails.

## Sync and analyze

Downloads require read access. They never query the crime dataset:

```powershell
python -m scripts.dashboard.sync_crime_observations --repo OWNER/REPO --root data/observations/crime
python -m scripts.dashboard.sync_crime_observations --repo OWNER/REPO --root data/observations/crime --month 2026-10
python -m pip install -r requirements-analysis.txt
python -m scripts.dashboard.analyze_crime_observations --root data/observations/crime --start-date 2026-10-01 --end-date 2026-10-07
```

Sync downloads to temporary sibling workspaces. It verifies identity/month,
checksums, size, counts, dates and columns before exposing a local bundle.
Already verified matching local copies skip snapshot downloads when GitHub's
asset size and SHA-256 digest match the manifest. The remote manifest is still
compared; older assets without server digests are downloaded for verification.
Corrupt local copies fail rather than being silently
replaced. Partial remote pairs are reported as errors, although other complete
observations can be downloaded in the same run. Nothing partial becomes a local
observation.

DuckDB is pinned in `requirements-analysis.txt`, included by development
requirements, and imported only when analysis runs. Analysis verifies every
visible local bundle, then derives distinct-ID totals and category totals in
DuckDB. It prints CSV to stdout; no derived tables are stored by the implementation.
The date range is inclusive and uses the source's Seattle offense calendar.

`category=all` counts every distinct nonmissing source offense ID. Category rows
use the current established classification function, including explicit
not-a-crime exclusions. The current classifier hashes are printed to stderr;
collection-time hashes remain in each manifest. Category totals need not sum to
the all-source total because exclusions and inconsistent duplicate source rows
can affect category membership. No arbitrary duplicate winner is introduced.

Coverage rules:

- With an exhausted offense-date query, dates on/after its inclusive midnight
  lower bound and before the Seattle collection-start date are covered by the
  query. Empty counts on those dates are zero. This does not assert that the
  source has published every offense yet or that pagination was atomic.
- The collection-start date is a partial boundary date. Positive observed counts
  are shown, but no rows produces null, not zero. Later dates and dates before
  the query lower bound are unobserved/null.
- A capped pull that did not reach a short final page is rejected by tracking
  and cannot contribute an observation. A fully fetched report-date-filtered
  pull cannot establish complete offense-date coverage and is
  `report_date_filtered`; it does not synthesize zeros.
- Total changes compare adjacent available observations only when both cover
  the date completely. Collection timestamps, previous observation ID, elapsed
  hours, bounds, and coverage status accompany the totals. A missed collection
  creates no observation; gaps are not filled or labeled stable.

Unchanged totals do not imply unchanged IDs. Use `read_observation(bundle)` for
verified ID-level evidence, and compare distinct `offense_id` sets for the same
offense date across bundles. Date/category revisions, disappearances and later
reappearances remain recoverable from the full snapshots. The totals CLI does
not decide a final time-to-stability.

## Workflow and recovery

The daily workflow retains its schedules, concurrency lock, dashboard publication,
and recovery logic. After the existing recovery decision, it collects crime and
uploads its observation **before calls or UOF**. A completed local capture is
eligible for upload even if the crime command's later freshness check fails;
the original refresh failure still prevents dashboard publication.

The archive step uses `GITHUB_TOKEN` via `GH_TOKEN`, with the workflow's existing
`contents: write`. It does not depend on `REFRESH_PR_TOKEN` having release access.
The latter remains solely for the existing PR flow. Local uploads require a gh
identity with release-write access. Organization policies/tag rules must allow
the `crime-observations-*` tags and release assets. Monthly releases must permit
additional assets after publication; immutable-release enforcement is incompatible
with this append-throughout-the-month layout. Confirm this repository setting
before enabling production collection.

Recovery has an intentional shortcut: if all dashboard sources look fresh, the
outer recovery checker or the post-lock recheck skips the refresh entirely.
An unchanged latest crime timestamp therefore need not produce a recovery
observation. A skip is **not** an observation and does not prove ID-level stability.
The scheduled daily refresh still collects when it runs normally.

An upload failure stops later dataset work and keeps the publication job failed.
Immediately after the release archive attempt, an `always()` step backs up
completed local bundles with `actions/upload-artifact@v4`, including when release
upload or the later crime freshness check failed. Its name is
`crime-observations-<run_id>-<run_attempt>` and its retention is **7 days** (subject
to repository policy). Hidden pending workspaces are excluded. When collection
failed before producing a bundle, `if-no-files-found: ignore` avoids a secondary
backup failure; it does not change the original collection failure into success.

Download the backup into a fresh, ignored directory before it expires, then retry
the **original bundle** through the normal checksum-verifying upload command:

```powershell
gh run download RUN_ID --repo OWNER/REPO --name crime-observations-RUN_ID-RUN_ATTEMPT --dir data/observations/recovered-RUN_ID-RUN_ATTEMPT
python -m scripts.dashboard.archive_crime_observations --repo OWNER/REPO --root data/observations/recovered-RUN_ID-RUN_ATTEMPT
```

The downloaded layout is `<observation_id>/snapshot.parquet` and
`<observation_id>/manifest.json`, preserving IDs, original collection timestamps,
and exact bytes. The uploader validates both files, verifies any existing release
assets, and uploads only missing matching assets, with the manifest last. Download
access requires Actions read access; the subsequent upload requires Contents write.

GitHub-hosted runner files are ephemeral: a whole-job rerun performs a new fetch
with a new ID, not a retry of the previous capture. If both the runner bundle and
the recovery artifact are unavailable, a snapshot-only remote upload remains
incomplete and must not be fabricated into a completed observation. Missing
evidence stays missing. **Releases remain the permanent archive**; the expiring
Actions artifact is only an upload-failure recovery copy.

## Size and operational monitoring

Monitor workflow failures and periodically run sync to detect incomplete pairs.
Every manifest reports snapshot bytes. To inspect monthly asset count/size:

```powershell
gh release view crime-observations-2026-10 --repo OWNER/REPO --json assets --jq '{assets: (.assets|length), bytes: ([.assets[].size]|add)}'
```

For large inventories use `gh api --paginate` on the release's assets endpoint,
as the archive implementation does. Account for two assets per collection,
including multiple runs/day. Monitor GitHub's current release-asset size/count
limits and repository storage expectations; this implementation never deletes
old evidence automatically. If size demands a different archive, migrate and
verify bundles rather than replacing them with derived totals.

CLI references: [create releases](https://cli.github.com/manual/gh_release_create),
[upload assets](https://cli.github.com/manual/gh_release_upload),
[download assets](https://cli.github.com/manual/gh_release_download).
