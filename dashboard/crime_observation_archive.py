"""Release archive transport through authenticated gh, with manifest-last completion."""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from dashboard.crime_observations import (
    Manifest, OBSERVATIONS_DIR, read_observation, sha256,
)


TAG_PREFIX = "crime-observations-"
ASSET = re.compile(r"^([0-9a-f]{32})\.(snapshot\.parquet|manifest\.json)$")


def archive_tag(manifest):
    value = Manifest.model_validate(manifest)
    return TAG_PREFIX + value.collection_started_at_utc.strftime("%Y-%m")


class GitHubCommandError(RuntimeError):
    """Sanitized CLI failure; retain only an HTTP status for control flow."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GitHubArchive:
    def __init__(self, repository):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
            raise ValueError("Repository must be OWNER/REPO")
        self.repository = repository

    def gh(self, *args):
        try:
            return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            # gh stderr can contain authenticated URLs; never relay it or token values.
            status = re.search(r"HTTP (\d{3})", getattr(error, "stderr", "") or "")
            raise GitHubCommandError(
                f"gh {' '.join(args[:2])} failed. Check gh authentication, repository permissions, "
                "release mutability, and connectivity; existing assets were not overwritten.",
                int(status.group(1)) if status else None,
            ) from None

    def pages(self, endpoint):
        pages = json.loads(self.gh("api", endpoint, "--paginate", "--slurp"))
        return [item for page in pages for item in page]

    def releases(self):
        return self.pages(f"repos/{self.repository}/releases?per_page=100")

    def assets(self, release):
        assets = self.pages(f"repos/{self.repository}/releases/{release['id']}/assets?per_page=100")
        return {item["name"]: item for item in assets}

    def download(self, tag, name, destination):
        self.gh("release", "download", tag, "--repo", self.repository,
                "--pattern", name, "--output", str(destination))

    def upload(self, tag, path):
        self.gh("release", "upload", tag, str(path), "--repo", self.repository)

    def release_by_tag(self, tag):
        try:
            return json.loads(self.gh("api", f"repos/{self.repository}/releases/tags/{tag}"))
        except GitHubCommandError as error:
            if error.status == 404:
                return None
            raise

    def ensure_release(self, manifest):
        tag = archive_tag(manifest)
        match = self.release_by_tag(tag)
        if match is None:
            self.gh("release", "create", tag, "--repo", self.repository,
                    "--target", manifest["git_commit"] or "main", "--latest=false", "--prerelease",
                    "--title", f"Crime observations {tag.removeprefix(TAG_PREFIX)}",
                    "--notes", "Research archive. Each manifest completes its matching snapshot; paginated collections are not atomic.")
            # Publication may not be immediately visible. Retry only a missing
            # release; authentication, permission and other API failures stay fatal.
            for delay in (0, 1, 2, 4, 8):
                if delay:
                    time.sleep(delay)
                match = self.release_by_tag(tag)
                if match is not None:
                    break
        if match is None:
            raise RuntimeError(f"Crime archive release {tag} is still missing after creation and bounded retries")
        if match.get("draft"):
            raise RuntimeError(f"Crime archive release {tag} is a draft; publish it before retrying the original bundle")
        if match.get("tag_name") != tag:
            raise RuntimeError("Crime archive release lookup returned a different tag")
        return match


def upload_observation(directory, archive):
    directory = Path(directory)
    _, manifest = read_observation(directory)
    release = archive.ensure_release(manifest)
    tag = archive_tag(manifest)
    names = {part: f"{manifest['observation_id']}.{part}" for part in ("snapshot.parquet", "manifest.json")}
    assets = archive.assets(release)
    with TemporaryDirectory(prefix="crime-archive-") as temporary:
        temporary = Path(temporary)
        # Preflight every existing asset before filling any hole, including manifest-only partials.
        for part, name in names.items():
            if name in assets:
                remote = temporary / ("existing-" + name)
                archive.download(tag, name, remote)
                if sha256(remote) != sha256(directory / part):
                    raise ValueError(f"Remote asset content mismatch: {name}; refusing overwrite")
        for part, name in names.items():  # Snapshot first, manifest last.
            if name not in assets:
                upload = temporary / name
                shutil.copyfile(directory / part, upload)
                archive.upload(tag, upload)
                verified = temporary / ("verified-" + name)
                archive.download(tag, name, verified)
                if sha256(verified) != sha256(upload):
                    raise ValueError(f"Uploaded asset failed verification: {name}")
    return tag


def sync_observations(archive, root=OBSERVATIONS_DIR, month=None):
    """Incomplete remote pairs are errors, never local observations or zero totals."""
    if month is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Month must be YYYY-MM")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    completed, partial = [], []
    for release in archive.releases():
        tag = release["tag_name"]
        if not re.fullmatch(TAG_PREFIX + r"\d{4}-(0[1-9]|1[0-2])", tag) or release.get("draft"):
            continue
        if month and tag != TAG_PREFIX + month:
            continue
        assets = archive.assets(release)
        groups = {}
        for name in assets:
            match = ASSET.fullmatch(name)
            if match:
                groups.setdefault(match[1], set()).add(match[2])
        for identity, parts in sorted(groups.items()):
            if parts != {"snapshot.parquet", "manifest.json"}:
                partial.append(identity)
                continue
            with TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
                bundle = Path(temporary) / identity
                bundle.mkdir()
                archive.download(tag, identity + ".manifest.json", bundle / "manifest.json")
                manifest = Manifest.model_validate_json((bundle / "manifest.json").read_text(encoding="utf-8"))
                if manifest.observation_id != identity or archive_tag(manifest.model_dump(mode="json")) != tag:
                    raise ValueError("Remote manifest identity/month mismatch")
                remote = assets[identity + ".snapshot.parquet"]
                if remote.get("state") != "uploaded":
                    raise ValueError(f"Incomplete remote snapshot upload: {identity}")
                digest = remote.get("digest")
                if remote.get("size") != manifest.snapshot_bytes or (
                    digest is not None and digest != "sha256:" + manifest.snapshot_sha256
                ):
                    raise ValueError(f"Remote snapshot size/checksum mismatch: {identity}")
                destination = root / identity
                if destination.exists():
                    read_observation(destination)
                    if sha256(destination / "manifest.json") != sha256(bundle / "manifest.json"):
                        raise ValueError("Local and remote observation manifests disagree")
                    if digest is None:
                        # Older assets may lack a server digest; verify bytes before claiming remote completion.
                        archive.download(tag, identity + ".snapshot.parquet", bundle / "snapshot.parquet")
                        read_observation(bundle)
                else:
                    archive.download(tag, identity + ".snapshot.parquet", bundle / "snapshot.parquet")
                    read_observation(bundle)
                    bundle.rename(destination)
                completed.append(destination)
    if partial:
        raise ValueError(f"Incomplete remote observations (retry their upload): {', '.join(partial)}")
    return completed
