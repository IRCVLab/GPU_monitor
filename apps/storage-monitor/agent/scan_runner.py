#!/usr/bin/env python3
"""Local hstscan orchestrator for immutable storage-viz snapshots.

The C scanner remains the byte walker.  This module is intentionally a small
policy and publication layer: load strict local config, select safe roots from
mountinfo, invoke hstscan with explicit argv/no shell, enrich/validate JSON,
and publish immutable generation snapshots plus last-good status atomically.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from agent.block_media import BlockMediaResolver, MediaResult
from agent import mount_policy

SCHEMA_VERSION = 1
DEFAULT_CONFIG = "/etc/storage-viz/scanner.yaml"
DEFAULT_DATA_DIR = "/var/lib/storage-viz"
DEFAULT_RUN_DIR = "/run/storage-viz"
ALLOWED_KINDS = frozenset({"directory", "file", "symlink", "other"})
BOUNDED_COUNTER_LIMIT = 10**18
_ALLOWED_KEYS = frozenset({
    "server_id",
    "scanner_path",
    "data_dir",
    "run_dir",
    "threads",
    "prune_home_mb",
    "prune_data_mb",
    "top",
    "stale_days",
})
_FORBIDDEN_KEYS = frozenset({
    "targets",
    "include_mounts",
    "exclude_mounts",
    "/",
    "mounts",
    "mountpoints",
    "scan_roots",
    "root",
    "roots",
    "paths",
    "path",
    "include_paths",
    "exclude_paths",
    "include",
    "exclude",
})
_SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_CAPACITY_ID_RE = re.compile(r"^dev-(0|[1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})$")
_MEDIA_VALUES = frozenset({"ssd", "hdd", "mixed", "unknown"})
_MEDIA_CONFIDENCE_VALUES = frozenset({"resolved", "unresolved"})


@dataclass(frozen=True)
class ScannerConfig:
    server_id: str
    scanner_path: str
    data_dir: pathlib.Path
    run_dir: pathlib.Path
    threads: int
    prune_home_mb: int
    prune_data_mb: int
    top: int
    stale_days: int
    config_digest: str


@dataclass(frozen=True)
class CompletedScan:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class RunResult:
    status: str
    generation: Optional[str] = None
    snapshot_path: Optional[pathlib.Path] = None
    status_path: Optional[pathlib.Path] = None
    error: Optional[str] = None


def _canonical_json(data: Mapping[str, Any]) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_config(path: os.PathLike[str] | str) -> ScannerConfig:
    cfg_path = pathlib.Path(path)
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"config must be strict JSON-compatible YAML parsed by json: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    keys = set(raw)
    forbidden = keys & _FORBIDDEN_KEYS
    if forbidden:
        raise ValueError(f"forbidden config key(s): {', '.join(sorted(forbidden))}")
    unknown = keys - _ALLOWED_KEYS
    if unknown:
        raise ValueError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    for required in ("server_id", "scanner_path"):
        if required not in raw:
            raise ValueError(f"missing required config key: {required}")

    server_id = _string(raw["server_id"], "server_id")
    if not _SERVER_ID_RE.match(server_id):
        raise ValueError("server_id must match ^[A-Za-z0-9_.-]+$")
    scanner_path = _absolute_path(_string(raw["scanner_path"], "scanner_path"), "scanner_path")
    data_dir = pathlib.Path(_absolute_path(_string(raw.get("data_dir", DEFAULT_DATA_DIR), "data_dir"), "data_dir"))
    run_dir = pathlib.Path(_absolute_path(_string(raw.get("run_dir", DEFAULT_RUN_DIR), "run_dir"), "run_dir"))
    threads = _int_range(raw.get("threads", 2), "threads", 1, 64)
    prune_home_mb = _int_range(raw.get("prune_home_mb", 50), "prune_home_mb", 0, 10_000_000)
    prune_data_mb = _int_range(raw.get("prune_data_mb", 100), "prune_data_mb", 0, 10_000_000)
    top = _int_range(raw.get("top", 200), "top", 0, 100_000)
    stale_days = _int_range(raw.get("stale_days", 180), "stale_days", 0, 100_000)
    digest = hashlib.sha256(_canonical_json(raw)).hexdigest()
    return ScannerConfig(server_id, scanner_path, data_dir, run_dir, threads, prune_home_mb, prune_data_mb, top, stale_days, digest)


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _absolute_path(value: str, name: str) -> str:
    if not pathlib.PurePosixPath(value).is_absolute():
        raise ValueError(f"{name} must be absolute")
    return value


def _int_range(value: Any, name: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
        raise ValueError(f"{name} must be an integer in [{lo}, {hi}]")
    return value


def try_lock_fd(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            return False
        raise


def verified_root_directory_paths(
    entries: Sequence[mount_policy.MountEntry],
    *,
    lstat: Callable[[str], os.stat_result] = os.lstat,
) -> Tuple[str, ...]:
    """Return verified root-backed directories eligible for policy synthesis.

    scan_runner owns the only live filesystem probe. The only synthesized path
    is exact /data, and it is eligible only when no exact /data mountinfo entry
    exists and lstat proves /data is a real directory on the root mount device.
    """

    candidate = "/data"
    if any(entry.mountpoint == candidate for entry in entries):
        return ()
    root_entry = next((entry for entry in entries if entry.mountpoint == "/"), None)
    if root_entry is None:
        return ()
    root_device = _parse_major_minor(root_entry.major_minor)
    if root_device is None:
        return ()
    try:
        candidate_stat = lstat(candidate)
    except OSError:
        return ()
    mode = getattr(candidate_stat, "st_mode", 0)
    if not stat.S_ISDIR(mode):
        return ()
    try:
        candidate_device = (os.major(candidate_stat.st_dev), os.minor(candidate_stat.st_dev))
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ()
    if candidate_device != root_device:
        return ()
    return (candidate,)


def _parse_major_minor(value: str) -> Optional[Tuple[int, int]]:
    try:
        major_s, minor_s = value.split(":", 1)
        major = int(major_s, 10)
        minor = int(minor_s, 10)
    except (AttributeError, ValueError):
        return None
    if major < 0 or minor < 0:
        return None
    return major, minor


def run_once(
    config_path: os.PathLike[str] | str = DEFAULT_CONFIG,
    *,
    mountinfo_reader: Callable[[], str] = mount_policy.read_mountinfo,
    scanner_runner: Callable[..., Any] = subprocess.run,
    media_resolver: Optional[Any] = None,
    clock: Callable[[], float] = time.time,
    lstat: Callable[[str], os.stat_result] = os.lstat,
) -> RunResult:
    cfg = load_config(config_path)
    data_dir = cfg.data_dir
    snapshots_dir = data_dir / "snapshots"
    run_dir = cfg.run_dir
    try:
        _ensure_managed_dir(data_dir)
        _ensure_managed_dir(snapshots_dir)
        _ensure_managed_dir(run_dir)
    except Exception as exc:
        return RunResult("failed", error=f"managed directory: {exc}")

    lock_path = run_dir / "scan.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        if not try_lock_fd(lock_fd):
            return RunResult("lock-conflict", error="scan already running")

        raw_path = run_dir / "hstscan.raw.json"

        try:
            entries = mount_policy.parse_mountinfo(mountinfo_reader())
            verified_root_directories = verified_root_directory_paths(entries, lstat=lstat)
            selected = mount_policy.select_scan_roots(entries, root_directory_paths=verified_root_directories)
        except Exception as exc:
            return RunResult("failed", error=f"mountinfo: {exc}")
        if not selected.selected:
            return RunResult("failed", error="no safe roots selected")
        media_by_major_minor = _resolve_media_by_major_minor(selected, media_resolver if media_resolver is not None else BlockMediaResolver())

        argv = _scanner_argv(cfg, raw_path, selected.selected)
        try:
            completed = scanner_runner(argv, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except Exception as exc:
            _unlink_quiet(raw_path)
            return RunResult("failed", error=f"scanner: {exc}")
        if getattr(completed, "returncode", 1) != 0:
            _unlink_quiet(raw_path)
            return RunResult("failed", error=f"scanner exited {getattr(completed, 'returncode', 'unknown')}")

        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            _validate_raw_payload(raw)
            payload, snapshot_status = _enrich_payload(raw, selected, media_by_major_minor)
            started_unix = raw["scan_started_unix"]
            finished_unix = int(clock())
            scan_duration_sec = finished_unix - started_unix
            if scan_duration_sec < 0:
                raise ValueError("scan_finished_unix precedes scan_started_unix")
            generation = _generation_name(cfg.server_id, started_unix)
            payload["server_id"] = cfg.server_id
            payload["scan_started_unix"] = started_unix
            payload["scan_finished_unix"] = finished_unix
            payload["scan_duration_sec"] = scan_duration_sec
            payload["scan_generation"] = generation
            payload["config_digest"] = cfg.config_digest
        except Exception as exc:
            _unlink_quiet(raw_path)
            return RunResult("failed", error=f"invalid scanner output: {exc}")
        finally:
            _unlink_quiet(raw_path)

        snapshot_path = snapshots_dir / f"{generation}.json"
        if snapshot_path.exists():
            # _unique_generation avoids this; keep immutable publication strict.
            return RunResult("failed", generation=generation, error="generation already exists")
        status_path = data_dir / "scan-status.json"
        try:
            _write_json_atomic(snapshot_path, payload)
            digest = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
            byte_size = snapshot_path.stat().st_size
            if not _HEX64_RE.match(digest):
                return RunResult("failed", generation=generation, error="internal digest failure")
            status = {
                "generation": snapshot_path.name,
                "byte_size": byte_size,
                "sha256": digest,
                "scan_finished_unix": finished_unix,
                "server_id": cfg.server_id,
                "config_digest": cfg.config_digest,
                "status": snapshot_status,
            }
            retained_generations = _retained_generation_order(status_path, snapshot_path.name, snapshots_dir, keep=2)
            status["retained_generations"] = retained_generations
            _publish_status_atomic(status_path, status)
        except Exception as exc:
            return RunResult("failed", generation=generation, snapshot_path=snapshot_path if snapshot_path.exists() else None, error=f"publication: {exc}")
        try:
            _prune_unretained_snapshots(snapshots_dir, retained_generations)
        except Exception as exc:
            return RunResult("failed", generation=generation, snapshot_path=snapshot_path, status_path=status_path, error=f"retention: {exc}")
        return RunResult(snapshot_status, generation, snapshot_path, status_path)
    finally:
        try:
            os.close(lock_fd)
        except OSError:
            pass


def _scanner_argv(cfg: ScannerConfig, out_path: pathlib.Path, roots: Sequence[mount_policy.SelectedRoot]) -> List[str]:
    argv = [
        cfg.scanner_path,
        "--threads", str(cfg.threads),
        "--prune-home", str(cfg.prune_home_mb),
        "--prune-data", str(cfg.prune_data_mb),
        "--top", str(cfg.top),
        "--stale-days", str(cfg.stale_days),
        "--out", str(out_path),
    ]
    for root in roots:
        argv.extend(["--target", root.mountpoint, root.entry.major_minor])
    return argv


def _validate_raw_payload(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("top-level JSON must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION or isinstance(raw.get("schema_version"), bool):
        raise ValueError("unsupported schema_version")
    _require_string(raw.get("hostname"), "hostname")
    _require_string(raw.get("scanner_version"), "scanner_version")
    if not isinstance(raw.get("run_as_root"), bool):
        raise ValueError("run_as_root must be boolean")
    _require_int(raw.get("scan_started_unix"), "scan_started_unix")
    mounts = raw.get("mounts")
    if not isinstance(mounts, list):
        raise ValueError("mounts must be a list")
    for mount in mounts:
        _validate_mount(mount)
    for key in ("top_files", "stale"):
        rows = raw.get(key, [])
        if not isinstance(rows, list):
            raise ValueError(f"{key} must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{key} rows must be objects")
            _validate_file_row(row, f"{key} row")
    _validate_users(raw.get("users", []))
    _validate_blocked(raw.get("blocked", []))


def _validate_mount(mount: Any) -> None:
    if not isinstance(mount, dict):
        raise ValueError("mount entry must be an object")
    _require_abs_path(mount.get("path"), "mount.path")
    _require_string(mount.get("fstype"), "mount.fstype")
    for key in ("df_total", "df_used", "df_avail"):
        _require_nonnegative_int(mount.get(key), f"mount.{key}")
    for key in ("scanned_bytes", "scanned_files", "scanned_dirs", "errors"):
        _require_bounded_counter(mount.get(key), f"mount.{key}")
    _require_percent_int(mount.get("df_use_pct"), "mount.df_use_pct")
    tree = mount.get("tree")
    _ensure_tree_kind(tree)
    if mount["scanned_bytes"] != tree["bytes"]:
        raise ValueError("mount.scanned_bytes must equal tree.bytes")
    if mount["scanned_files"] != tree["files"]:
        raise ValueError("mount.scanned_files must equal tree.files")


def _validate_file_row(row: Dict[str, Any], label: str) -> None:
    _validate_or_default_kind(row, "file", label)
    _require_abs_path(row.get("path"), f"{label}.path")
    _require_bounded_counter(row.get("bytes"), f"{label}.bytes")
    _require_nonnegative_int(row.get("uid"), f"{label}.uid")
    _require_string(row.get("owner"), f"{label}.owner")
    _require_int(row.get("mtime"), f"{label}.mtime")
    if label.startswith("stale"):
        _require_nonnegative_int(row.get("age_days"), f"{label}.age_days")
    elif "age_days" in row:
        _require_nonnegative_int(row.get("age_days"), f"{label}.age_days")


def _validate_users(users: Any) -> None:
    if not isinstance(users, list):
        raise ValueError("users must be a list")
    for user in users:
        if not isinstance(user, dict):
            raise ValueError("users rows must be objects")
        _require_nonnegative_int(user.get("uid"), "user.uid")
        _require_string(user.get("name"), "user.name")
        _require_bounded_counter(user.get("bytes"), "user.bytes")
        _require_bounded_counter(user.get("files"), "user.files")
        by_mount = user.get("by_mount")
        if not isinstance(by_mount, dict):
            raise ValueError("user.by_mount must be an object")
        for mount_path, value in by_mount.items():
            _require_abs_path(mount_path, "user.by_mount path")
            _require_bounded_counter(value, "user.by_mount bytes")


def _validate_blocked(blocked: Any) -> None:
    if not isinstance(blocked, list):
        raise ValueError("blocked must be a list")
    for row in blocked:
        if not isinstance(row, dict):
            raise ValueError("blocked rows must be objects")
        path = row.get("path")
        if path is not None and not isinstance(path, str):
            raise ValueError("blocked.path must be a string when present")
        if isinstance(path, str) and len(path) > 4096:
            raise ValueError("blocked.path is too long")
        _require_bounded_string(row.get("reason"), "blocked.reason", 127)


def _require_string(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")


def _require_bounded_string(value: Any, label: str, max_len: int) -> None:
    _require_string(value, label)
    if len(value) > max_len:
        raise ValueError(f"{label} is too long")


def _require_int(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")


def _require_nonnegative_int(value: Any, label: str) -> None:
    _require_int(value, label)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")


def _require_percent_int(value: Any, label: str) -> None:
    _require_int(value, label)
    if value < 0 or value > 100:
        raise ValueError(f"{label} must be in [0, 100]")


def _require_bounded_counter(value: Any, label: str) -> None:
    _require_nonnegative_int(value, label)
    if value >= BOUNDED_COUNTER_LIMIT:
        raise ValueError(f"{label} must be less than {BOUNDED_COUNTER_LIMIT}")


def _require_abs_path(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.startswith("/") or _normalize_blocked_path(value) is None:
        raise ValueError(f"{label} must be an absolute path")


def _resolve_media_by_major_minor(selection: mount_policy.SelectionResult, media_resolver: Any) -> Dict[str, MediaResult]:
    resolved: Dict[str, MediaResult] = {}
    ordered: List[str] = []
    for root in selection.selected:
        ordered.append(root.entry.major_minor)
    for skipped in selection.skipped:
        if skipped.entry is not None:
            ordered.append(skipped.entry.major_minor)
    for major_minor in ordered:
        if major_minor in resolved:
            continue
        fallback_capacity_id = _capacity_id_from_major_minor(major_minor)
        try:
            result = media_resolver.resolve(major_minor)
        except Exception:
            result = _unknown_media(fallback_capacity_id)
        resolved[major_minor] = _safe_media_result(result, fallback_capacity_id)
    return resolved


def _capacity_id_from_major_minor(major_minor: Any) -> Optional[str]:
    if not isinstance(major_minor, str):
        return None
    match = re.match(r"^(\d+):(\d+)$", major_minor)
    if not match:
        return None
    major_s, minor_s = match.groups()
    if len(major_s) > 10 or len(minor_s) > 10:
        return None
    major = int(major_s)
    minor = int(minor_s)
    if major == 0 and minor == 0:
        return None
    capacity_id = f"dev-{major}-{minor}"
    if len(capacity_id) > 31 or not _CAPACITY_ID_RE.match(capacity_id):
        return None
    return capacity_id


def _unknown_media(capacity_id: Optional[str] = None) -> MediaResult:
    return MediaResult(capacity_id, "unknown", "unresolved")


def _safe_media_result(result: Any, fallback_capacity_id: Optional[str]) -> MediaResult:
    try:
        capacity_id = getattr(result, "capacity_id")
        media = getattr(result, "media")
        confidence = getattr(result, "confidence")
    except Exception:
        return _unknown_media(fallback_capacity_id)
    if capacity_id is not None and (
        not isinstance(capacity_id, str)
        or len(capacity_id) > 31
        or not _CAPACITY_ID_RE.match(capacity_id)
        or capacity_id == "dev-0-0"
    ):
        return _unknown_media(fallback_capacity_id)
    if capacity_id is not None and fallback_capacity_id is not None and capacity_id != fallback_capacity_id:
        return _unknown_media(fallback_capacity_id)
    if not isinstance(media, str) or not isinstance(confidence, str):
        return _unknown_media(fallback_capacity_id)
    if media not in _MEDIA_VALUES or confidence not in _MEDIA_CONFIDENCE_VALUES:
        return _unknown_media(fallback_capacity_id)
    if (media == "unknown") != (confidence == "unresolved"):
        return _unknown_media(fallback_capacity_id)
    return MediaResult(capacity_id, media, confidence)


def _media_fields(result: MediaResult) -> Dict[str, Any]:
    fields = {
        "storage_media": result.media,
        "storage_media_confidence": result.confidence,
    }
    if result.capacity_id is not None:
        fields["capacity_id"] = result.capacity_id
    return fields


def _enrich_payload(raw: Mapping[str, Any], selection: mount_policy.SelectionResult, media_by_major_minor: Mapping[str, MediaResult]) -> Tuple[Dict[str, Any], str]:
    mounts = raw["mounts"]
    roots = selection.selected
    roots_by_path = {root.mountpoint: root for root in roots}
    enriched_mounts: List[Dict[str, Any]] = []
    for mount in mounts:
        if not isinstance(mount, dict):
            raise ValueError("mount entry must be an object")
        path = mount.get("path")
        if path not in roots_by_path:
            continue
        root = roots_by_path[path]
        linked = dict(mount)
        linked["mount_id"] = _logical_mount_id(root)
        linked["scan_root"] = root.mountpoint
        linked["fstype"] = root.entry.fstype
        linked.update(_media_fields(media_by_major_minor.get(root.entry.major_minor, _unknown_media())))
        _ensure_tree_kind(linked.get("tree"))
        enriched_mounts.append(linked)

    blocked_counts = _blocked_counts_by_scan_root(raw.get("blocked", []), [root.mountpoint for root in roots])
    selected_roots: List[Dict[str, Any]] = []
    complete_count = 0
    any_failed = False
    for root in roots:
        mount = next((m for m in enriched_mounts if m.get("scan_root") == root.mountpoint), None)
        if mount is None:
            selected_roots.append(_root_record(root, "failed", "MISSING_RAW_MOUNT", media=media_by_major_minor.get(root.entry.major_minor)))
            any_failed = True
            continue
        errors = _nonnegative_int(mount.get("errors", 0), "errors")
        blocked_count = blocked_counts.get(root.mountpoint, 0)
        status = "complete" if errors == 0 else "partial"
        if status == "complete":
            complete_count += 1
        else:
            any_failed = True
        selected_roots.append(_root_record(
            root,
            status,
            "EACCES" if errors else None,
            media=media_by_major_minor.get(root.entry.major_minor),
            scanned_bytes=_nonnegative_int(mount.get("scanned_bytes", 0), "scanned_bytes"),
            scanned_files=_nonnegative_int(mount.get("scanned_files", 0), "scanned_files"),
            scanned_dirs=_nonnegative_int(mount.get("scanned_dirs", 0), "scanned_dirs"),
            blocked_count=blocked_count,
            error_count=errors,
        ))

    emitted_scan_roots = {record["scan_root"] for record in selected_roots}
    for skipped in selection.skipped:
        record = _skipped_record(skipped, media_by_major_minor.get(skipped.entry.major_minor) if skipped.entry is not None else None)
        if record["scan_root"] in emitted_scan_roots:
            continue
        selected_roots.append(record)
        emitted_scan_roots.add(record["scan_root"])

    if complete_count < 1:
        raise ValueError("at least one selected root must complete")

    payload = dict(raw)
    payload["selected_roots"] = selected_roots
    payload["mounts"] = enriched_mounts
    for row_key in ("top_files", "stale"):
        rows = payload.get(row_key, [])
        for row in rows:
            if isinstance(row, dict):
                row.setdefault("kind", "file")
    status = "partial" if any_failed else "complete"
    return payload, status


def _blocked_counts_by_scan_root(blocked: Any, scan_roots: Sequence[str]) -> Dict[str, int]:
    counts = {_normalize_abs_path(root): 0 for root in scan_roots}
    ordered_roots = sorted(counts, key=lambda root: (len(root), root), reverse=True)
    if not isinstance(blocked, list):
        return counts
    for item in blocked:
        if not isinstance(item, dict):
            continue
        path = _normalize_blocked_path(item.get("path"))
        if path is None:
            continue
        for root in ordered_roots:
            if _path_contains(root, path):
                counts[root] += 1
                break
    return counts


def _normalize_blocked_path(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    return _normalize_abs_path(value)


def _normalize_abs_path(path: str) -> str:
    normalized = os.path.normpath(path)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return normalized


def _path_contains(root: str, path: str) -> bool:
    if root == "/":
        return path == "/" or path.startswith("/")
    return path == root or path.startswith(root + "/")


def _logical_mount_id(root: mount_policy.SelectedRoot) -> str:
    source_id = str(root.entry.mount_id)
    if root.reason == "root-directory" and root.source_mountpoint == "/":
        if root.mountpoint == "/data":
            return f"{source_id}-root-data"
        raise ValueError("unsupported root-backed logical root")
    return source_id


def _root_record(
    root: mount_policy.SelectedRoot,
    status: str,
    error_code: Optional[str],
    *,
    scanned_bytes: int = 0,
    scanned_files: int = 0,
    scanned_dirs: int = 0,
    blocked_count: int = 0,
    error_count: int = 0,
    media: Optional[MediaResult] = None,
) -> Dict[str, Any]:
    entry = root.entry
    record = {
        "mount_id": _logical_mount_id(root),
        "major_minor": entry.major_minor,
        "mount_source": entry.source,
        "mount_root": entry.root,
        "mountpoint": root.source_mountpoint,
        "scan_root": root.mountpoint,
        "fstype": entry.fstype,
        "status": status,
        "scanned_bytes": scanned_bytes,
        "scanned_files": scanned_files,
        "scanned_dirs": scanned_dirs,
        "blocked_count": blocked_count,
        "error_count": error_count,
        "error_code": error_code,
    }
    record.update(_media_fields(media or _unknown_media()))
    return record


def _skipped_record(skipped: mount_policy.SkippedMount, media: Optional[MediaResult] = None) -> Dict[str, Any]:
    entry = skipped.entry
    reason = _bounded_reason(skipped.reason)
    if entry is not None:
        record = {
            "mount_id": _skipped_mount_id(skipped),
            "major_minor": entry.major_minor,
            "mount_source": entry.source,
            "mount_root": entry.root,
            "mountpoint": entry.mountpoint,
            "scan_root": skipped.mountpoint,
            "fstype": entry.fstype,
            "status": "skipped",
            "scanned_bytes": 0,
            "scanned_files": 0,
            "scanned_dirs": 0,
            "blocked_count": 0,
            "error_count": 0,
            "error_code": reason,
        }
        record.update(_media_fields(media or _unknown_media()))
        return record
    record = {
        "mount_id": str(skipped.mount_id),
        "major_minor": "0:0",
        "mount_source": "unknown",
        "mount_root": "/",
        "mountpoint": skipped.mountpoint,
        "scan_root": skipped.mountpoint,
        "fstype": "unknown",
        "status": "skipped",
        "scanned_bytes": 0,
        "scanned_files": 0,
        "scanned_dirs": 0,
        "blocked_count": 0,
        "error_count": 0,
        "error_code": reason,
    }
    record.update(_media_fields(media or _unknown_media()))
    return record


def _skipped_mount_id(skipped: mount_policy.SkippedMount) -> str:
    safe_path = re.sub(r"[^A-Za-z0-9_.-]+", "-", skipped.mountpoint).strip("-") or "root"
    safe_reason = re.sub(r"[^A-Za-z0-9_.-]+", "-", skipped.reason).strip("-") or "skipped"
    return f"{skipped.mount_id}-skipped-{safe_path}-{safe_reason}"[:127]


def _bounded_reason(reason: str) -> str:
    reason = str(reason or "skipped")
    return reason[:127] or "skipped"


def _ensure_tree_kind(node: Any) -> None:
    if not isinstance(node, dict):
        raise ValueError("mount tree must be an object")
    _require_string(node.get("name"), "tree.name")
    _validate_or_default_kind(node, "directory", "tree node")
    _require_bounded_counter(node.get("bytes"), "tree.bytes")
    _require_bounded_counter(node.get("files"), "tree.files")
    _require_nonnegative_int(node.get("uid"), "tree.uid")
    _require_int(node.get("mtime"), "tree.mtime")
    other_bytes = node.get("other_bytes", 0)
    _require_bounded_counter(other_bytes, "tree.other_bytes")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise ValueError("tree children must be a list")
    child_bytes = 0
    for child in children:
        if not isinstance(child, dict):
            raise ValueError("tree children must be objects")
        _ensure_tree_kind(child)
        child_bytes += child["bytes"]
    if children and node["bytes"] != child_bytes + other_bytes:
        raise ValueError("tree bytes must equal child bytes plus other_bytes")
    if not children and other_bytes != 0:
        raise ValueError("tree leaf other_bytes must be zero")


def _validate_or_default_kind(row: Dict[str, Any], default_kind: str, label: str) -> None:
    kind = row.get("kind")
    if kind is None:
        row["kind"] = default_kind
        return
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"{label} has invalid kind")
    if default_kind == "file" and kind != "file":
        raise ValueError(f"{label} kind must be file")
    if default_kind == "directory" and kind != "directory":
        raise ValueError(f"{label} kind must be directory")


def _nonnegative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    if value >= BOUNDED_COUNTER_LIMIT:
        raise ValueError(f"{name} must be less than {BOUNDED_COUNTER_LIMIT}")
    return value


def _generation_name(server_id: str, started_unix: int) -> str:
    return f"{server_id}-{started_unix}-v1"


def _ensure_managed_dir(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o750)
    if path.is_symlink():
        raise ValueError(f"final managed directory is a symlink: {path}")
    if not path.is_dir():
        raise ValueError(f"managed path is not a directory: {path}")


def _mkdir_private(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o750)


def _write_json_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    data = json.dumps(payload, sort_keys=True, indent=2, separators=(",", ": ")).encode("utf-8") + b"\n"
    _write_bytes_atomic(path, data)


def _publish_status_atomic(path: pathlib.Path, payload: Mapping[str, Any]) -> None:
    prior_bytes: Optional[bytes]
    try:
        prior_bytes = path.read_bytes()
    except FileNotFoundError:
        prior_bytes = None
    except OSError:
        prior_bytes = None
    try:
        _write_json_atomic(path, payload)
    except Exception as original_exc:
        rollback_exc: Optional[Exception] = None
        try:
            if prior_bytes is not None:
                _write_bytes_atomic(path, prior_bytes)
            elif path.exists():
                path.unlink()
                _fsync_dir(path.parent)
        except Exception as exc:
            rollback_exc = exc
        if rollback_exc is not None:
            raise RuntimeError(f"{original_exc}; rollback failed: {rollback_exc}") from original_exc
        raise original_exc


def _write_bytes_atomic(path: pathlib.Path, data: bytes) -> None:
    parent = path.parent
    _mkdir_private(parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    try:
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(parent)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        _unlink_quiet(tmp_name)
        raise


def _fsync_dir(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _retained_generation_order(status_path: pathlib.Path, current_name: str, snapshots_dir: pathlib.Path, keep: int) -> List[str]:
    prior: List[str] = []
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        status = {}
    if isinstance(status, dict):
        retained = status.get("retained_generations")
        if isinstance(retained, list):
            prior.extend(name for name in retained if isinstance(name, str))
        generation = status.get("generation")
        if isinstance(generation, str):
            prior.append(generation)
    ordered: List[str] = []
    for name in [current_name, *prior]:
        if name in ordered:
            continue
        if (snapshots_dir / name).exists():
            ordered.append(name)
        if len(ordered) >= keep:
            break
    return ordered


def _prune_unretained_snapshots(snapshots_dir: pathlib.Path, retained_generations: Sequence[str]) -> None:
    retained = set(retained_generations)
    for path in snapshots_dir.glob("*.json"):
        if path.name not in retained:
            _unlink_quiet(path)
    _fsync_dir(snapshots_dir)


def _unlink_quiet(path: os.PathLike[str] | str) -> None:
    try:
        pathlib.Path(path).unlink()
    except FileNotFoundError:
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run hstscan and publish an immutable local storage-viz snapshot")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    result = run_once(args.config)
    if result.status in ("complete", "partial"):
        print(result.snapshot_path)
        return 0
    if result.status == "lock-conflict":
        print(result.error or "lock conflict", file=sys.stderr)
        return 75
    print(result.error or "scan failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
