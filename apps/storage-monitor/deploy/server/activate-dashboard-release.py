#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import BinaryIO, Callable, Iterable, Mapping, Sequence


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
APPLICATION_NAME = "storage-monitor"
SCHEMA_VERSION = 1
ARTIFACT_FORMAT_VERSION = 1
MANIFEST_PATH = "storage-monitor/RELEASE-MANIFEST.json"
REQUIRED_RUNTIME_FILES = frozenset(
    {
        "viewer/serve.py",
        "viewer/app.js",
        "viewer/data-client.js",
        "viewer/debug.html",
        "viewer/echarts.min.js",
        "viewer/index.html",
        "viewer/overview.js",
        "viewer/selection.js",
        "viewer/styles.css",
        "viewer/tables.js",
        "viewer/treemap.js",
        "viewer/users-chart.js",
        "collector/__init__.py",
        "collector/inventory.py",
        "collector/jobs.py",
        "collector/service.py",
        "collector/snapshot.py",
        "collector/store.py",
        "collector/transport.py",
        "config/servers.example.yaml",
        "docs/schema-v1.md",
        "deploy/direct_proxy.py",
    }
)


class ActivationError(RuntimeError):
    """Raised for fail-closed activation errors."""


@dataclass(frozen=True)
class ActivationConfig:
    release_root: Path = Path("/srv/storage-viz-dashboard/releases")
    app_path: Path = Path("/opt/storage-viz-dashboard")
    state_path: Path = Path("/var/lib/storage-viz-dashboard/activation-state.json")
    lock_path: Path = Path("/var/lib/storage-viz-dashboard/activation.lock")
    incoming_dir: Path = Path("/var/lib/storage-viz-dashboard/incoming")
    max_input_bytes: int = 128 * 1024 * 1024
    max_archive_bytes: int = 128 * 1024 * 1024
    max_members: int = 512
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    keep_releases: int = 5
    restart_argv: tuple[str, ...] | None = None
    health_argv: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for field in ("release_root", "app_path", "state_path", "lock_path", "incoming_dir"):
            value = getattr(self, field)
            if not isinstance(value, Path):
                object.__setattr__(self, field, Path(value))


@dataclass(frozen=True)
class PreparedArchive:
    staged_file: BinaryIO
    staged_path: Path
    archive_name: str
    metadata: dict[str, object]
    manifest: dict[str, object]
    members: tuple[tarfile.TarInfo, ...]
    files: dict[str, str]
    digest: str


@dataclass(frozen=True)
class StartState:
    kind: str
    previous: str | None = None
    legacy_backup: str | None = None


@contextlib.contextmanager
def _activation_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _validate_sha(value: str, label: str = "sha") -> None:
    if not SHA_RE.fullmatch(value):
        raise ActivationError(f"invalid {label}: expected 40 lowercase hex characters")


def _validate_digest(value: str, label: str = "digest") -> None:
    if not DIGEST_RE.fullmatch(value):
        raise ActivationError(f"invalid {label}: expected 64 lowercase hex characters")


def _read_json_file(path: Path, max_bytes: int) -> dict[str, object]:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            metadata_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata_stat.st_mode):
                raise ActivationError("metadata must be a regular file")
            if metadata_stat.st_size > max_bytes:
                raise ActivationError("metadata exceeds configured input bound")
            raw = handle.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ActivationError("metadata exceeds configured input bound")
        data = json.loads(raw.decode("utf-8"))
    except ActivationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActivationError(f"invalid metadata JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ActivationError("metadata must be a JSON object")
    return data


def _validate_metadata(metadata: dict[str, object], *, sha: str, archive_name: str, digest: str) -> None:
    expected = {
        "application_name": APPLICATION_NAME,
        "schema_version": SCHEMA_VERSION,
        "source_sha": sha,
        "archive": archive_name,
        "sha256": digest,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ActivationError(f"metadata {key} mismatch")


def _copy_to_private_stage(
    config: ActivationConfig,
    source: BinaryIO,
    sha: str,
    *,
    overflow_message: str,
) -> tuple[BinaryIO, Path]:
    config.incoming_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{sha}.verified.", suffix=".tar.gz", dir=config.incoming_dir)
    staged_path = Path(tmp_name)
    staged_file = os.fdopen(fd, "w+b")
    total = 0
    try:
        os.fchmod(staged_file.fileno(), 0o600)
        with contextlib.suppress(FileNotFoundError):
            staged_path.unlink()
        _fsync_dir(config.incoming_dir)
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > min(config.max_input_bytes, config.max_archive_bytes):
                raise ActivationError(overflow_message)
            staged_file.write(chunk)
        staged_file.flush()
        os.fsync(staged_file.fileno())
        staged_file.seek(0)
        return staged_file, staged_path
    except BaseException:
        staged_file.close()
        raise


def _require_private_incoming_path(config: ActivationConfig, path: Path, label: str) -> None:
    try:
        resolved = path.resolve(strict=True)
        incoming = config.incoming_dir.resolve(strict=True)
        resolved.relative_to(incoming)
    except (FileNotFoundError, ValueError) as exc:
        raise ActivationError(f"{label} must be under private incoming directory") from exc


def _sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return _sha256_stream(handle)


def _sha256_stream(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _is_relative_safe_storage_path(name: str) -> bool:
    path = Path(name)
    if path.is_absolute() or name.startswith("/") or name == "storage-monitor":
        return False
    parts = path.parts
    if not parts or parts[0] != "storage-monitor" or any(part in ("", ".", "..") for part in parts):
        return False
    return True


def _safe_mode(mode: int) -> bool:
    permissions = stat.S_IMODE(mode)
    return permissions in {0o444, 0o555, 0o644, 0o755}


def _inspect_archive(config: ActivationConfig, archive_file: BinaryIO) -> tuple[dict[str, object], tuple[tarfile.TarInfo, ...], dict[str, str]]:
    if os.fstat(archive_file.fileno()).st_size > min(config.max_archive_bytes, config.max_input_bytes):
        raise ActivationError("artifact exceeds configured compressed size bound")

    seen: set[str] = set()
    total_size = 0
    files: dict[str, str] = {}
    members: list[tarfile.TarInfo] = []
    manifest: dict[str, object] | None = None
    try:
        archive_file.seek(0)
        with tarfile.open(fileobj=archive_file, mode="r:gz") as tar:
            while True:
                member = tar.next()
                if member is None:
                    break
                if len(members) >= config.max_members:
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
                if member.size < 0 or member.size > config.max_file_bytes:
                    raise ActivationError(f"archive member exceeds per-file bound: {member.name}")
                total_size += member.size
                if total_size > config.max_total_bytes:
                    raise ActivationError("archive exceeds configured expanded-size bound")
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise ActivationError(f"cannot read archive member: {member.name}")
                data = extracted.read(config.max_file_bytes + 1)
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
                members.append(member)
    except tarfile.TarError as exc:
        raise ActivationError(f"invalid gzip tar artifact: {exc}") from exc

    if manifest is None:
        raise ActivationError("release manifest is required")
    return manifest, tuple(members), files


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
    if included != sorted(included):
        raise ActivationError("manifest included_paths must be sorted")
    if not isinstance(manifest_files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in manifest_files.items()):
        raise ActivationError("manifest files must map path to sha256")
    included_set = set(included)
    if not REQUIRED_RUNTIME_FILES.issubset(included_set):
        missing = sorted(REQUIRED_RUNTIME_FILES - included_set)
        raise ActivationError(f"manifest missing required runtime files: {', '.join(missing)}")
    if included_set != set(files):
        raise ActivationError("manifest included path set does not match archive regular files")
    if set(manifest_files) != set(files):
        raise ActivationError("manifest file hash path set does not match archive regular files")
    for rel, actual_digest in files.items():
        expected = manifest_files[rel]
        _validate_digest(expected, f"manifest digest for {rel}")
        if expected != actual_digest:
            raise ActivationError(f"manifest hash mismatch for {rel}")


def _prepare_archive(
    config: ActivationConfig,
    *,
    sha: str,
    expected_digest: str,
    artifact_path: Path | None,
    metadata_path: Path,
    artifact_stdin: BinaryIO | None,
) -> PreparedArchive:
    _validate_sha(sha, "source sha")
    _validate_digest(expected_digest, "expected digest")
    staged_file: BinaryIO | None = None
    staged_path: Path | None = None
    metadata_path = Path(metadata_path)
    if artifact_path is None:
        if artifact_stdin is None:
            raise ActivationError("artifact path or artifact stdin is required")
        archive_name = f"storage-monitor-dashboard-{sha}.tar.gz"
        staged_file, staged_path = _copy_to_private_stage(
            config,
            artifact_stdin,
            sha,
            overflow_message="artifact stdin exceeds configured input bound",
        )
    else:
        artifact_path = Path(artifact_path)
        archive_name = artifact_path.name
        _require_private_incoming_path(config, artifact_path, "artifact")
    try:
        if staged_file is None:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                source_fd = os.open(artifact_path, flags)
            except OSError as exc:
                raise ActivationError(f"cannot open artifact safely: {exc}") from exc
            with os.fdopen(source_fd, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ActivationError("artifact must be a regular file")
                staged_file, staged_path = _copy_to_private_stage(
                    config,
                    source,
                    sha,
                    overflow_message="artifact exceeds configured input bound",
                )
        digest = _sha256_stream(staged_file)
        if digest != expected_digest:
            raise ActivationError("artifact digest mismatch")
        metadata = _read_json_file(metadata_path, config.max_input_bytes)
        _validate_metadata(metadata, sha=sha, archive_name=archive_name, digest=digest)
        manifest, members, files = _inspect_archive(config, staged_file)
        _validate_manifest(manifest, sha=sha, archive_name=archive_name, files=files)
        return PreparedArchive(staged_file, staged_path, archive_name, metadata, manifest, members, files, digest)
    except BaseException:
        if staged_file is not None:
            staged_file.close()
        raise


def _release_target(config: ActivationConfig, sha: str) -> Path:
    return config.release_root / sha / "storage-monitor"



def _valid_release_symlink(config: ActivationConfig, path: Path) -> Path:
    if not path.is_symlink():
        raise ActivationError("app path is not a symlink")
    try:
        target = path.resolve(strict=True)
        release_root = config.release_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ActivationError("app path symlink target is broken") from exc
    try:
        target.relative_to(release_root)
    except ValueError as exc:
        raise ActivationError("app path symlink target is outside release root") from exc
    if target.name != "storage-monitor" or not SHA_RE.fullmatch(target.parent.name):
        raise ActivationError("app path symlink target is not a release storage-monitor directory")
    return target


def _current_start_state(config: ActivationConfig) -> StartState:
    app = config.app_path
    if app.is_symlink():
        target = _valid_release_symlink(config, app)
        return StartState("symlink", previous=str(target))
    if app.exists():
        if not app.is_dir():
            raise ActivationError("app path exists but is not a directory or release symlink")
        return StartState("legacy")
    return StartState("absent")


def _real_legacy_layout(app_path: Path, *, require_proxy: bool = True) -> Path:
    """Return a canonical, narrowly validated legacy Storage application root."""
    if app_path.is_symlink():
        raise ActivationError("restored legacy app path must not be a symlink")
    try:
        app_stat = app_path.stat()
        canonical = app_path.resolve(strict=True)
    except OSError as exc:
        raise ActivationError("restored legacy app path is missing or broken") from exc
    if not stat.S_ISDIR(app_stat.st_mode):
        raise ActivationError("restored legacy app path must be a directory")
    if app_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
        raise ActivationError("restored legacy app path has unsafe mode")
    owner = app_stat.st_uid
    required = [
        canonical / "viewer",
        canonical / "viewer/serve.py",
    ]
    if require_proxy:
        required.extend((canonical / "deploy", canonical / "deploy/direct_proxy.py"))
    for path in required:
        if path.is_symlink():
            raise ActivationError(f"restored legacy path must not be a symlink: {path}")
        try:
            path_stat = path.stat()
            resolved = path.resolve(strict=True)
            resolved.relative_to(canonical)
        except (OSError, ValueError) as exc:
            raise ActivationError(f"restored legacy path is missing, broken, or external: {path}") from exc
        expected_directory = path.name in {"viewer", "deploy"}
        if expected_directory and not stat.S_ISDIR(path_stat.st_mode):
            raise ActivationError(f"restored legacy path must be a directory: {path}")
        if not expected_directory and not stat.S_ISREG(path_stat.st_mode):
            raise ActivationError(f"restored legacy path must be a regular file: {path}")
        if not expected_directory and not path_stat.st_mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH):
            raise ActivationError(f"restored legacy script is not readable: {path}")
        if path_stat.st_uid != owner:
            raise ActivationError(f"restored legacy path owner differs from app root: {path}")
        if path_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
            raise ActivationError(f"restored legacy path has unsafe mode: {path}")
    return canonical


def _legacy_proxy_recovery_root(config: ActivationConfig) -> Path:
    return config.state_path.parent / "legacy-proxy"


def _validated_managed_legacy_proxy(config: ActivationConfig, target: Path, digest: str) -> Path:
    _validate_digest(digest, "managed legacy proxy digest")
    root = _legacy_proxy_recovery_root(config)
    expected = root / digest / "direct_proxy.py"
    if target.resolve(strict=False) != expected.resolve(strict=False) or target.is_symlink():
        raise ActivationError("managed legacy proxy path mismatch")
    try:
        canonical_root = root.resolve(strict=True)
        canonical = target.resolve(strict=True)
        target_stat = target.stat()
        parent_stat = config.state_path.parent.stat()
        root_stat = root.stat()
        digest_dir_stat = expected.parent.stat()
        canonical.relative_to(canonical_root)
    except (OSError, ValueError) as exc:
        raise ActivationError("managed legacy proxy is missing, broken, or external") from exc
    if canonical != expected.resolve(strict=True) or not stat.S_ISREG(target_stat.st_mode):
        raise ActivationError("managed legacy proxy must be a regular file")
    if target_stat.st_uid != parent_stat.st_uid or target_stat.st_gid != parent_stat.st_gid:
        raise ActivationError("managed legacy proxy owner/group mismatch")
    for directory_stat in (root_stat, digest_dir_stat):
        if directory_stat.st_uid != parent_stat.st_uid or directory_stat.st_gid != parent_stat.st_gid:
            raise ActivationError("managed legacy proxy recovery directory owner/group mismatch")
        if stat.S_IMODE(directory_stat.st_mode) != 0o750:
            raise ActivationError("managed legacy proxy recovery directory mode must be 0750")
    if stat.S_IMODE(target_stat.st_mode) != 0o440:
        raise ActivationError("managed legacy proxy mode must be 0440")
    if _sha256_file(canonical) != digest:
        raise ActivationError("managed legacy proxy digest mismatch")
    return canonical


def prepare_legacy_proxy_recovery(config: ActivationConfig, source: Path) -> dict[str, object]:
    """Snapshot an approved legacy proxy into immutable deployment-owned storage."""
    source = Path(source)
    if source.is_symlink():
        raise ActivationError("legacy proxy source must not be a symlink")
    try:
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ActivationError(f"legacy proxy source is unavailable: {exc}") from exc
    try:
        source_stat = os.fstat(fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ActivationError("legacy proxy source must be a regular file")
        if source_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
            raise ActivationError("legacy proxy source has unsafe mode")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            payload = handle.read(config.max_file_bytes + 1)
        if len(payload) > config.max_file_bytes:
            raise ActivationError("legacy proxy source exceeds configured file bound")
    finally:
        os.close(fd)
    digest = hashlib.sha256(payload).hexdigest()
    state_parent = config.state_path.parent
    state_parent.mkdir(parents=True, exist_ok=True)
    owner = state_parent.stat()
    root = _legacy_proxy_recovery_root(config)
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(root, owner.st_uid, owner.st_gid)
    os.chmod(root, 0o750)
    digest_dir = root / digest
    digest_dir.mkdir(mode=0o750, exist_ok=True)
    os.chown(digest_dir, owner.st_uid, owner.st_gid)
    os.chmod(digest_dir, 0o750)
    target = digest_dir / "direct_proxy.py"
    if not target.exists():
        fd, temp_name = tempfile.mkstemp(prefix=".direct_proxy.", dir=digest_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
                os.fchmod(handle.fileno(), 0o440)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            _fsync_dir(digest_dir)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)
            raise
    canonical = _validated_managed_legacy_proxy(config, target, digest)
    return {
        "status": "legacy_proxy_prepared",
        "legacy_proxy_original_path": str(source.resolve(strict=True)),
        "managed_legacy_proxy_target": str(canonical),
        "managed_legacy_proxy_sha256": digest,
    }


def _validated_legacy_proxy_recovery(
    config: ActivationConfig,
    payload: Mapping[str, object],
) -> dict[str, str]:
    target = payload.get("managed_legacy_proxy_target")
    digest = payload.get("managed_legacy_proxy_sha256")
    original = payload.get("legacy_proxy_original_path")
    if not all(isinstance(value, str) and value for value in (target, digest, original)):
        raise ActivationError("legacy proxy recovery metadata is incomplete")
    managed = _validated_managed_legacy_proxy(config, Path(str(target)), str(digest))
    return {
        "legacy_proxy_original_path": str(Path(str(original)).resolve(strict=False)),
        "managed_legacy_proxy_target": str(managed),
        "managed_legacy_proxy_sha256": str(digest),
    }


def _extract_private(config: ActivationConfig, archive: PreparedArchive, sha: str) -> Path:
    target = _release_target(config, sha)
    if target.exists():
        _assert_existing_release_location(config, target, sha)
        _assert_existing_release_matches(target, archive.files)
        _chmod_tree_readonly(target)
        os.chmod(target.parent, 0o555)
        return target
    config.release_root.mkdir(parents=True, exist_ok=True)
    target_parent = target.parent
    tmp_parent = config.release_root / f".{sha}.extract.{os.getpid()}"
    if tmp_parent.exists():
        shutil.rmtree(tmp_parent)
    tmp_storage = tmp_parent / "storage-monitor"
    try:
        archive.staged_file.seek(0)
        with tarfile.open(fileobj=archive.staged_file, mode="r:gz") as tar:
            for member in archive.members:
                relative = Path(member.name).relative_to("storage-monitor")
                destination = tmp_storage / relative
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise ActivationError(f"cannot extract member: {member.name}")
                with destination.open("wb") as out:
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(destination, 0o555 if stat.S_IMODE(member.mode) & 0o111 else 0o444)
        _fsync_tree(tmp_storage)
        target_parent.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_parent, target_parent)
        _fsync_dir(target_parent.parent)
        _chmod_tree_readonly(target)
        os.chmod(target_parent, 0o555)
        return target
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(tmp_parent)
        raise


def _fsync_tree(root: Path) -> None:
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        for filename in files:
            with contextlib.suppress(OSError):
                with (current_path / filename).open("rb") as handle:
                    os.fsync(handle.fileno())
        _fsync_dir(current_path)


def _chmod_tree_readonly(root: Path) -> None:
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        if current_path.is_symlink():
            raise ActivationError("release tree contains a directory symlink")
        for filename in files:
            file_path = current_path / filename
            if file_path.is_symlink():
                raise ActivationError("release tree contains a file symlink")
            file_stat = file_path.stat()
            if file_stat.st_nlink != 1:
                raise ActivationError("release tree contains a hardlinked file")
            mode = file_stat.st_mode
            os.chmod(file_path, 0o555 if mode & 0o111 else 0o444)
        for dirname in dirs:
            directory = current_path / dirname
            if directory.is_symlink():
                raise ActivationError("release tree contains a directory symlink")
            os.chmod(directory, 0o555)
    os.chmod(root, 0o555)


def _assert_existing_release_matches(target: Path, expected_files: dict[str, str]) -> None:
    actual: dict[str, str] = {}
    for current, dirs, files in os.walk(target):
        current_path = Path(current)
        for name in dirs:
            if (current_path / name).is_symlink():
                raise ActivationError("existing release contains a directory symlink")
        for name in files:
            path = current_path / name
            rel = str(path.relative_to(target))
            if rel == "RELEASE-MANIFEST.json":
                continue
            if not path.is_file() or path.is_symlink():
                raise ActivationError("existing release contains unsafe nonregular file")
            if path.stat().st_nlink != 1:
                raise ActivationError("existing release contains a hardlinked file")
            actual[rel] = _sha256_file(path)
    if actual != expected_files:
        raise ActivationError("existing release content mismatch")


def _assert_existing_release_location(config: ActivationConfig, target: Path, sha: str) -> None:
    parent = target.parent
    if parent.is_symlink() or target.is_symlink():
        raise ActivationError("existing release path must not contain a symlinked release directory")
    try:
        canonical_root = config.release_root.resolve(strict=True)
        canonical_parent = parent.resolve(strict=True)
        canonical_target = target.resolve(strict=True)
    except OSError as exc:
        raise ActivationError("existing release path is missing or unreadable") from exc
    if (
        canonical_parent.parent != canonical_root
        or canonical_parent.name != sha
        or canonical_target.parent != canonical_parent
        or canonical_target.name != "storage-monitor"
    ):
        raise ActivationError("existing release path is outside the direct release root")


def _atomic_symlink(link_path: Path, target: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = link_path.parent / f".{link_path.name}.tmp.{os.getpid()}"
    with contextlib.suppress(FileNotFoundError):
        tmp.unlink()
    os.symlink(target, tmp)
    os.replace(tmp, link_path)
    _fsync_dir(link_path.parent)


def _timestamp() -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime()) + f".{os.getpid()}"


def _activate_symlink(config: ActivationConfig, target: Path, start_state: StartState) -> StartState:
    app = config.app_path
    if start_state.kind == "legacy":
        backup = app.with_name(f"{app.name}.legacy.{_timestamp()}")
        os.replace(app, backup)
        _fsync_dir(app.parent)
        try:
            _atomic_symlink(app, target)
        except Exception:
            os.replace(backup, app)
            _fsync_dir(app.parent)
            raise
        return StartState("legacy", legacy_backup=str(backup))
    _atomic_symlink(app, target)
    return start_state


def _restore_after_failure(config: ActivationConfig, activated_state: StartState, original_state: StartState) -> None:
    app = config.app_path
    if activated_state.kind == "legacy" and activated_state.legacy_backup:
        if app.is_symlink() or app.exists():
            if app.is_dir() and not app.is_symlink():
                shutil.rmtree(app)
            else:
                app.unlink()
        os.replace(activated_state.legacy_backup, app)
        _fsync_dir(app.parent)
        return
    if original_state.kind == "symlink" and original_state.previous:
        _atomic_symlink(app, Path(original_state.previous))
        return
    if original_state.kind == "absent":
        with contextlib.suppress(FileNotFoundError):
            if app.is_dir() and not app.is_symlink():
                shutil.rmtree(app)
            else:
                app.unlink()


def _call_restart(config: ActivationConfig, restart: Callable[..., object] | None, phase: str) -> None:
    if restart is not None:
        try:
            restart(phase)
        except TypeError:
            restart()
        return
    if not config.restart_argv:
        return
    result = subprocess.run(list(config.restart_argv), shell=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ActivationError(f"restart command failed: {result.stderr.strip()}")


def _call_health(config: ActivationConfig, health: Callable[[], object] | None) -> bool:
    if health is not None:
        return bool(health())
    if not config.health_argv:
        return True
    result = subprocess.run(list(config.health_argv), shell=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.returncode == 0


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchown(handle.fileno(), -1, path.parent.stat().st_gid)
            os.fchmod(handle.fileno(), 0o640)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise


def _read_state(path: Path, *, require_valid: bool = False) -> dict[str, object] | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        if require_valid:
            raise ActivationError("activation state is invalid or unreadable for rollback") from exc
        return None
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("activation state is not a regular file")
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if require_valid:
            raise ActivationError("activation state is invalid or unreadable for rollback") from exc
        return None
    if not isinstance(data, dict):
        if require_valid:
            raise ActivationError("activation state is invalid or unreadable for rollback")
        return None
    return data


def _rollback_state(path: Path, previous_state: dict[str, object] | None) -> None:
    if previous_state is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    _atomic_write_json(path, previous_state)


def _prune_releases(config: ActivationConfig, protected: Iterable[Path]) -> None:
    if config.keep_releases < 0 or not config.release_root.exists():
        return
    protected_resolved: set[Path] = set()
    for path in protected:
        with contextlib.suppress(FileNotFoundError):
            protected_resolved.add(path.resolve(strict=True))
            protected_resolved.add(path.parent.resolve(strict=True))
    candidates: list[Path] = []
    for child in config.release_root.iterdir():
        if not child.is_dir() or child.name.startswith(".") or not SHA_RE.fullmatch(child.name):
            continue
        storage = child / "storage-monitor"
        with contextlib.suppress(FileNotFoundError):
            if child.resolve(strict=True) in protected_resolved or storage.resolve(strict=True) in protected_resolved:
                continue
        candidates.append(child)
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    protected_release_dirs = {path for path in protected_resolved if path.parent == config.release_root.resolve()}
    keep_inactive = max(0, config.keep_releases - len(protected_release_dirs))
    for old in candidates[keep_inactive:]:
        _remove_readonly_release(config.release_root, old)
    _fsync_dir(config.release_root)


def _remove_readonly_release(release_root: Path, release: Path) -> None:
    canonical_root = release_root.resolve(strict=True)
    if release.is_symlink():
        raise ActivationError("release selected for pruning must not be a symlink")
    canonical = release.resolve(strict=True)
    if canonical.parent != canonical_root or SHA_RE.fullmatch(canonical.name) is None:
        raise ActivationError("release selected for pruning is outside the release root")
    for current, dirs, files in os.walk(canonical, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            entry = current_path / name
            if entry.is_symlink():
                raise ActivationError("release selected for pruning contains a symlink")
        for name in dirs:
            entry = current_path / name
            if entry.is_symlink():
                raise ActivationError("release selected for pruning contains a symlink")
            os.chmod(entry, 0o700)
        os.chmod(current_path, 0o700)
    shutil.rmtree(canonical)


def activate_release(
    config: ActivationConfig,
    *,
    sha: str,
    expected_digest: str,
    metadata_path: Path,
    artifact_path: Path | None = None,
    artifact_stdin: BinaryIO | None = None,
    restart: Callable[..., object] | None = None,
    health: Callable[[], object] | None = None,
    legacy_proxy_recovery: Mapping[str, object] | None = None,
) -> dict[str, object]:
    with _activation_lock(config.lock_path):
        archive = _prepare_archive(
            config,
            sha=sha,
            expected_digest=expected_digest,
            artifact_path=artifact_path,
            metadata_path=metadata_path,
            artifact_stdin=artifact_stdin,
        )
        try:
            original_state = _current_start_state(config)
            previous_state = None if original_state.kind == "legacy" else _read_state(config.state_path)
            recovery = None
            if legacy_proxy_recovery is not None:
                if original_state.kind != "legacy":
                    raise ActivationError("legacy proxy recovery metadata requires a legacy app start state")
                recovery = _validated_legacy_proxy_recovery(config, legacy_proxy_recovery)
            target = _extract_private(config, archive, sha)
        finally:
            archive.staged_file.close()
        activated_state = _activate_symlink(config, target, original_state)
        status = {
            "status": "active",
            "release": str(target),
            "source_sha": sha,
            "archive": archive.archive_name,
            "archive_digest": archive.digest,
            "previous": original_state.previous,
            "legacy_backup": activated_state.legacy_backup,
        }
        if recovery is not None:
            status.update(recovery)
        try:
            _call_restart(config, restart, "activate")
            if not _call_health(config, health):
                raise ActivationError("health check failed after activation")
            _atomic_write_json(config.state_path, status)
            protected = [target]
            if original_state.previous:
                protected.append(Path(original_state.previous))
            if activated_state.legacy_backup:
                protected.append(Path(activated_state.legacy_backup))
            _prune_releases(config, protected)
            return status
        except Exception as exc:
            _restore_after_failure(config, activated_state, original_state)
            _rollback_state(config.state_path, previous_state)
            try:
                _call_restart(config, restart, "rollback")
            except Exception as rollback_exc:
                failure_status = dict(previous_state or {})
                failure_status.update(
                    {
                        "status": "rollback_restart_failed",
                        "activation_error": str(exc),
                        "rollback_restart_error": str(rollback_exc),
                        "failed_release": str(target),
                        "failed_source_sha": sha,
                        "failed_archive_digest": archive.digest,
                        "restored": True,
                    }
                )
                status_write_error: Exception | None = None
                try:
                    _atomic_write_json(config.state_path, failure_status)
                except Exception as state_exc:
                    status_write_error = state_exc
                message = f"activation failed: {exc}; rollback restart failed: {rollback_exc}"
                if status_write_error is not None:
                    message += f"; failure status persistence failed: {status_write_error}"
                raise ActivationError(message) from rollback_exc
            if isinstance(exc, ActivationError):
                raise
            raise ActivationError(str(exc)) from exc


def prepare_release(
    config: ActivationConfig,
    *,
    sha: str,
    expected_digest: str,
    metadata_path: Path,
    artifact_path: Path | None = None,
    artifact_stdin: BinaryIO | None = None,
) -> dict[str, object]:
    """Validate and extract an immutable candidate without changing live state."""
    with _activation_lock(config.lock_path):
        archive = _prepare_archive(
            config,
            sha=sha,
            expected_digest=expected_digest,
            artifact_path=artifact_path,
            metadata_path=metadata_path,
            artifact_stdin=artifact_stdin,
        )
        try:
            target = _extract_private(config, archive, sha)
        finally:
            archive.staged_file.close()
        return {
            "status": "prepared",
            "candidate_release": str(target),
            "source_sha": sha,
            "archive_digest": archive.digest,
        }


def record_restored_legacy(
    config: ActivationConfig,
    *,
    managed_legacy_proxy_target: Path | None = None,
    managed_legacy_proxy_sha256: str | None = None,
    legacy_proxy_original_path: Path | None = None,
) -> dict[str, object]:
    """Persist launcher state after activation already restored a pre-state legacy app."""
    with _activation_lock(config.lock_path):
        external_proxy = managed_legacy_proxy_target is not None
        restored = _real_legacy_layout(config.app_path, require_proxy=not external_proxy)
        if external_proxy:
            if managed_legacy_proxy_sha256 is None or legacy_proxy_original_path is None:
                raise ActivationError("managed legacy proxy target, digest, and original path are required together")
            managed = _validated_managed_legacy_proxy(
                config,
                Path(managed_legacy_proxy_target),
                managed_legacy_proxy_sha256,
            )
            original = Path(legacy_proxy_original_path).resolve(strict=False)
        else:
            managed = restored / "deploy/direct_proxy.py"
            managed_legacy_proxy_sha256 = _sha256_file(managed)
            original = managed
        status: dict[str, object] = {
            "status": "rolled_back",
            "restored": str(restored),
            "restored_legacy_target": str(restored),
            "managed_legacy_proxy_target": str(managed),
            "managed_legacy_proxy_sha256": managed_legacy_proxy_sha256,
            "legacy_proxy_original_path": str(original),
        }
        _atomic_write_json(config.state_path, status)
        return status


def _clear_failed_candidate_fields(status: dict[str, object]) -> None:
    for key in (
        "failed_release",
        "failed_source_sha",
        "failed_archive_digest",
        "activation_error",
        "rollback_restart_error",
    ):
        status.pop(key, None)


def _replace_with_legacy_copy(app_path: Path, backup: Path) -> None:
    temp = app_path.with_name(f".{app_path.name}.restore.{os.getpid()}")
    with contextlib.suppress(FileNotFoundError):
        if temp.is_dir() and not temp.is_symlink():
            shutil.rmtree(temp)
        else:
            temp.unlink()
    try:
        shutil.copytree(backup, temp, symlinks=True, copy_function=shutil.copy2)
        _fsync_tree(temp)
        if app_path.is_symlink() or app_path.exists():
            if app_path.is_dir() and not app_path.is_symlink():
                shutil.rmtree(app_path)
            else:
                app_path.unlink()
        os.replace(temp, app_path)
        _fsync_dir(app_path.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            if temp.is_dir() and not temp.is_symlink():
                shutil.rmtree(temp)
            else:
                temp.unlink()
        raise



def rollback_to_state(
    config: ActivationConfig,
    *,
    restart: Callable[..., object] | None = None,
) -> dict[str, object]:
    with _activation_lock(config.lock_path):
        state = _read_state(config.state_path, require_valid=True)
        if not state:
            raise ActivationError("activation state is unavailable for rollback")
        previous = state.get("previous")
        legacy_backup = state.get("legacy_backup")
        if isinstance(previous, str) and previous:
            target = Path(previous)
            expected = _release_target(config, target.parent.name)
            if not SHA_RE.fullmatch(target.parent.name) or target != expected or not target.is_dir():
                raise ActivationError("previous release target is unavailable for rollback")
            _atomic_symlink(config.app_path, target)
            restored = str(target)
            status = dict(state)
            status.update(
                {
                    "status": "rolled_back",
                    "release": restored,
                    "current": restored,
                    "source_sha": target.parent.name,
                    "previous": None,
                    "legacy_backup": None,
                    "restored": restored,
                }
            )
            status.pop("archive_digest", None)
        elif isinstance(legacy_backup, str) and legacy_backup:
            backup = Path(legacy_backup)
            if not backup.is_dir():
                raise ActivationError("legacy backup is unavailable for rollback")
            _replace_with_legacy_copy(config.app_path, backup)
            managed_target = state.get("managed_legacy_proxy_target")
            managed_digest = state.get("managed_legacy_proxy_sha256")
            original_proxy = state.get("legacy_proxy_original_path")
            external_proxy = all(isinstance(value, str) and value for value in (managed_target, managed_digest, original_proxy))
            if external_proxy:
                managed = _validated_managed_legacy_proxy(config, Path(str(managed_target)), str(managed_digest))
                restored_path = _real_legacy_layout(config.app_path, require_proxy=False)
            else:
                restored_path = _real_legacy_layout(config.app_path)
                managed = restored_path / "deploy/direct_proxy.py"
                managed_digest = _sha256_file(managed)
                original_proxy = str(managed)
            restored = str(restored_path)
            status = dict(state)
            status.update(
                {
                    "status": "rolled_back",
                    "release": restored,
                    "current": restored,
                    "previous": None,
                    "legacy_backup": str(backup),
                    "protected_legacy_backup": str(backup),
                    "restored_legacy_target": restored,
                    "managed_legacy_proxy_target": str(managed),
                    "managed_legacy_proxy_sha256": managed_digest,
                    "legacy_proxy_original_path": original_proxy,
                    "restored": restored,
                }
            )
            status.pop("source_sha", None)
            status.pop("archive_digest", None)
        else:
            raise ActivationError("activation state has no previous release or legacy backup")
        _clear_failed_candidate_fields(status)
        _atomic_write_json(config.state_path, status)
        _call_restart(config, restart, "rollback")
        return status

def _bounded_status(payload: dict[str, object]) -> str:
    text = json.dumps(payload, sort_keys=True)
    if len(text) > 8192:
        raise ActivationError("status JSON exceeds output bound")
    return text


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Activate a bounded storage-monitor dashboard release")
    parser.add_argument("--sha")
    parser.add_argument("--expected-digest")
    parser.add_argument("--artifact")
    parser.add_argument("--artifact-stdin", action="store_true")
    parser.add_argument("--metadata")
    parser.add_argument("--release-root", default="/srv/storage-viz-dashboard/releases")
    parser.add_argument("--app-path", default="/opt/storage-viz-dashboard")
    parser.add_argument("--state-path", default="/var/lib/storage-viz-dashboard/activation-state.json")
    parser.add_argument("--lock-path", default="/var/lib/storage-viz-dashboard/activation.lock")
    parser.add_argument("--incoming-dir", default="/var/lib/storage-viz-dashboard/incoming")
    parser.add_argument("--max-input-bytes", type=int, default=ActivationConfig.max_input_bytes)
    parser.add_argument("--max-archive-bytes", type=int, default=ActivationConfig.max_archive_bytes)
    parser.add_argument("--max-members", type=int, default=ActivationConfig.max_members)
    parser.add_argument("--max-file-bytes", type=int, default=ActivationConfig.max_file_bytes)
    parser.add_argument("--max-total-bytes", type=int, default=ActivationConfig.max_total_bytes)
    parser.add_argument("--keep-releases", type=int, default=ActivationConfig.keep_releases)
    parser.add_argument("--rollback-state", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--prepare-legacy-proxy", action="store_true")
    parser.add_argument("--record-restored-legacy", action="store_true")
    parser.add_argument("--legacy-proxy-source")
    parser.add_argument("--legacy-proxy-recovery-target")
    parser.add_argument("--legacy-proxy-recovery-sha256")
    parser.add_argument("--legacy-proxy-original-path")
    parser.add_argument("--restart-argv", nargs="+")
    parser.add_argument("--health-argv", nargs="+")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = ActivationConfig(
        release_root=Path(args.release_root),
        app_path=Path(args.app_path),
        state_path=Path(args.state_path),
        lock_path=Path(args.lock_path),
        incoming_dir=Path(args.incoming_dir),
        max_input_bytes=args.max_input_bytes,
        max_archive_bytes=args.max_archive_bytes,
        max_members=args.max_members,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
        keep_releases=args.keep_releases,
        restart_argv=tuple(args.restart_argv) if args.restart_argv else None,
        health_argv=tuple(args.health_argv) if args.health_argv else None,
    )
    try:
        operation_modes = sum((args.rollback_state, args.prepare_only, args.prepare_legacy_proxy, args.record_restored_legacy))
        if operation_modes > 1:
            raise ActivationError("rollback, release preparation, legacy proxy preparation, and legacy recording modes are mutually exclusive")
        recovery_values = (
            args.legacy_proxy_recovery_target,
            args.legacy_proxy_recovery_sha256,
            args.legacy_proxy_original_path,
        )
        has_recovery = any(recovery_values)
        if has_recovery and not all(recovery_values):
            raise ActivationError("legacy proxy recovery target, digest, and original path are required together")
        recovery = None
        if has_recovery:
            recovery = {
                "managed_legacy_proxy_target": args.legacy_proxy_recovery_target,
                "managed_legacy_proxy_sha256": args.legacy_proxy_recovery_sha256,
                "legacy_proxy_original_path": args.legacy_proxy_original_path,
            }
        if args.rollback_state:
            status = rollback_to_state(config)
        elif args.prepare_legacy_proxy:
            if not args.legacy_proxy_source:
                raise ActivationError("--legacy-proxy-source is required for legacy proxy preparation")
            status = prepare_legacy_proxy_recovery(config, Path(args.legacy_proxy_source))
        elif args.record_restored_legacy:
            status = record_restored_legacy(
                config,
                managed_legacy_proxy_target=Path(args.legacy_proxy_recovery_target) if has_recovery else None,
                managed_legacy_proxy_sha256=args.legacy_proxy_recovery_sha256,
                legacy_proxy_original_path=Path(args.legacy_proxy_original_path) if has_recovery else None,
            )
        else:
            if not args.sha or not args.expected_digest or not args.metadata:
                raise ActivationError("--sha, --expected-digest, and --metadata are required for activation")
            common = dict(
                sha=args.sha,
                expected_digest=args.expected_digest,
                artifact_path=Path(args.artifact) if args.artifact else None,
                artifact_stdin=sys.stdin.buffer if args.artifact_stdin else None,
                metadata_path=Path(args.metadata),
            )
            if args.prepare_only:
                if recovery is not None:
                    raise ActivationError("legacy proxy recovery metadata is only valid for activation")
                status = prepare_release(config, **common)
            else:
                status = activate_release(config, legacy_proxy_recovery=recovery, **common)
    except ActivationError as exc:
        print(_bounded_status({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1
    print(_bounded_status(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
