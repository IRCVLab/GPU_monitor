"""Archive validation shared by the Storage release puller and activator."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import posixpath
from pathlib import PurePosixPath
import re
import stat
import tarfile
from typing import BinaryIO
from urllib.parse import unquote, urlsplit


DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
APPLICATION_NAME = "storage-monitor"
SCHEMA_VERSION = 1
ARTIFACT_FORMAT_VERSION = 1
MANIFEST_PATH = "storage-monitor/RELEASE-MANIFEST.json"
VIEWER_PAGES = ("viewer/index.html",)
PACKAGED_DOCUMENTATION = frozenset({"docs/schema-v1.md"})
REQUIRED_RELEASE_FILES = PACKAGED_DOCUMENTATION | frozenset(VIEWER_PAGES) | frozenset(
    {
        "api/__init__.py",
        "api/errors.py",
        "api/auth.py",
        "api/fixtures.py",
        "api/routes.py",
        "api/server.py",
        "viewer/serve.py",
        "viewer/api.js",
        "viewer/columns.js",
        "viewer/echarts.min.js",
        "viewer/effects.js",
        "viewer/format.js",
        "viewer/main.js",
        "viewer/removal.js",
        "viewer/router.js",
        "viewer/state.js",
        "viewer/store.js",
        "viewer/styles.css",
        "viewer/users-chart.js",
        "viewer/views/files.js",
        "viewer/views/header.js",
        "viewer/views/notices.js",
        "viewer/views/overview.js",
        "viewer/views/scan-status.js",
        "viewer/views/selection-bar.js",
        "viewer/views/server.js",
        "viewer/views/theme.js",
        "viewer/views/treemap.js",
        "viewer/views/users.js",
        "collector/__init__.py",
        "collector/actor.py",
        "collector/fleet.py",
        "collector/inventory.py",
        "collector/jobs.py",
        "collector/legacy.py",
        "collector/snapshot.py",
        "collector/store.py",
        "collector/transport.py",
        "collector/view.py",
        "config/servers.example.yaml",
        "deploy/direct_proxy.py",
    }
)

# The signed host agent bundle for the same SHA (target-design §4.5). The
# puller fetches it from CI, verifies it and requires it in every release it
# activates; `fleet.desired_agent()` reads it from the active release.
AGENT_BUNDLE_FILES = frozenset({
    "agent/storage-viz-agent.tar.gz",
    "agent/storage-viz-agent.tar.gz.sig",
    "agent/storage-viz-agent.json",
})

BUILD_RELEASE_FILES = REQUIRED_RELEASE_FILES | {
    "collector/contract.py", "collector/errors.py", "collector/health.py", "collector/ids.py", "collector/judge.py",
    "collector/live_capacity.py", "collector/update.py", "storage_fstypes.py",
}


class ActivationError(RuntimeError):
    """Raised for fail-closed release validation and activation errors."""


@dataclass(frozen=True)
class InspectedArchive:
    members: tuple[tarfile.TarInfo, ...]
    files: dict[str, str]
    modes: dict[str, int]
    runtime_digest: str


def validate_digest(value: str, label: str = "digest") -> None:
    if not DIGEST_RE.fullmatch(value):
        raise ActivationError(f"invalid {label}: expected 64 lowercase hex characters")


def _is_relative_safe_storage_path(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or name == "storage-monitor":
        return False
    parts = path.parts
    if not parts or parts[0] != "storage-monitor" or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _safe_mode(mode: int) -> bool:
    permissions = stat.S_IMODE(mode)
    return permissions in {0o444, 0o555, 0o644, 0o755}


class ViewerAssets(HTMLParser):
    """Collect local scripts and stylesheets loaded by a viewer page."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script":
            reference = attributes.get("src")
        elif tag == "link" and "stylesheet" in (attributes.get("rel") or "").lower().split():
            reference = attributes.get("href")
        else:
            return
        if not reference:
            return
        url = urlsplit(reference)
        if not url.scheme and not url.netloc and url.path:
            self.paths.add(posixpath.normpath("/" + unquote(url.path).lstrip("/")).lstrip("/"))


def viewer_asset_paths(page: bytes) -> set[str]:
    parser = ViewerAssets()
    try:
        parser.feed(page.decode("utf-8"))
        parser.close()
    except ValueError as exc:
        raise ActivationError(f"invalid viewer HTML: {exc}") from exc
    return parser.paths


def inspect_archive(
    archive_file: BinaryIO,
    *,
    sha: str,
    archive_name: str,
    max_members: int = 512,
    max_file_bytes: int = 32 * 1024 * 1024,
    max_total_bytes: int = 256 * 1024 * 1024,
) -> InspectedArchive:
    seen: set[str] = set()
    total_size = 0
    files: dict[str, str] = {}
    modes: dict[str, int] = {}
    viewer_assets: set[str] = set()
    members: list[tarfile.TarInfo] = []
    manifest: dict[str, object] | None = None
    try:
        archive_file.seek(0)
        with tarfile.open(fileobj=archive_file, mode="r:gz") as tar:
            while True:
                member = tar.next()
                if member is None:
                    break
                if len(members) >= max_members:
                    raise ActivationError("archive exceeds configured member-count bound")
                if member.name in seen:
                    raise ActivationError(f"duplicate archive member: {member.name}")
                seen.add(member.name)
                if not _is_relative_safe_storage_path(member.name):
                    raise ActivationError(f"unsafe archive member path: {member.name}")
                if not member.isfile() and not member.isdir():
                    raise ActivationError(f"unsupported archive member type: {member.name}")
                if member.isdir():
                    if not _safe_mode(member.mode):
                        raise ActivationError(f"unsafe archive member mode: {member.name}")
                    members.append(member)
                    continue
                if not _safe_mode(member.mode):
                    raise ActivationError(f"unsafe archive member mode: {member.name}")
                if member.size < 0 or member.size > max_file_bytes:
                    raise ActivationError(f"archive member exceeds per-file bound: {member.name}")
                total_size += member.size
                if total_size > max_total_bytes:
                    raise ActivationError("archive exceeds configured expanded-size bound")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise ActivationError(f"cannot read archive member: {member.name}")
                data = extracted.read(max_file_bytes + 1)
                if len(data) != member.size:
                    raise ActivationError(f"archive member size changed while reading: {member.name}")
                if member.name == MANIFEST_PATH:
                    try:
                        loaded = json.loads(data.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ActivationError(f"invalid release manifest: {exc}") from exc
                    if not isinstance(loaded, dict):
                        raise ActivationError("release manifest must be a JSON object")
                    manifest = loaded
                else:
                    rel = member.name.removeprefix("storage-monitor/")
                    files[rel] = hashlib.sha256(data).hexdigest()
                    modes[rel] = stat.S_IMODE(member.mode)
                    if rel in VIEWER_PAGES:
                        viewer_assets.update(f"viewer/{path}" for path in viewer_asset_paths(data))
                members.append(member)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ActivationError(f"invalid gzip tar artifact: {exc}") from exc

    if manifest is None:
        raise ActivationError("release manifest is required")
    _validate_manifest(manifest, sha=sha, archive_name=archive_name, files=files)
    missing_assets = viewer_assets.difference(files)
    if missing_assets:
        raise ActivationError(f"archive missing viewer assets: {', '.join(sorted(missing_assets))}")
    runtime_files = [
        (path, modes[path], files[path])
        for path in sorted(files.keys() - PACKAGED_DOCUMENTATION)
    ]
    runtime_digest = hashlib.sha256(json.dumps(runtime_files, separators=(",", ":")).encode("utf-8")).hexdigest()
    return InspectedArchive(tuple(members), files, modes, runtime_digest)


def _validate_manifest(manifest: dict[str, object], *, sha: str, archive_name: str, files: dict[str, str]) -> None:
    expected_scalars = {
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "application_name": APPLICATION_NAME,
        "schema_version": SCHEMA_VERSION,
        "archive": archive_name,
        "source_sha": sha,
    }
    for key, value in expected_scalars.items():
        if manifest.get(key) != value:
            raise ActivationError(f"manifest {key} mismatch")
    included = manifest.get("included_paths")
    manifest_files = manifest.get("files")
    if not isinstance(included, list) or not all(isinstance(item, str) for item in included):
        raise ActivationError("manifest included_paths must be a list of paths")
    if len(included) != len(set(included)) or included != sorted(included):
        raise ActivationError("manifest included_paths must be sorted")
    if not isinstance(manifest_files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest_files.items()):
        raise ActivationError("manifest files must map path to sha256")
    included_set = set(included)
    if not REQUIRED_RELEASE_FILES.issubset(included_set):
        missing = sorted(REQUIRED_RELEASE_FILES - included_set)
        raise ActivationError(f"manifest missing required runtime files: {', '.join(missing)}")
    if included_set != set(files):
        raise ActivationError("manifest included path set does not match archive regular files")
    if set(manifest_files) != set(files):
        raise ActivationError("manifest file hash path set does not match archive regular files")
    for rel, actual_digest in files.items():
        expected = manifest_files[rel]
        validate_digest(expected, f"manifest digest for {rel}")
        if expected != actual_digest:
            raise ActivationError(f"manifest hash mismatch for {rel}")
