"""Validation for downloaded immutable central storage snapshots."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any, Dict, Mapping, Optional, Set

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
GENERATION_RE = re.compile(r"^[A-Za-z0-9_.-]+-\d+-v1\.json$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_COUNTER = 10**18
MAX_TIME = 4_102_444_800
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_SELECTED_ROOTS = 256
MAX_MOUNTS = 256
MAX_USERS = 65_536
MAX_FILE_ROWS = 100_000
MAX_BLOCKED = 100_000
MAX_TREE_DEPTH = 128
MAX_TREE_NODES = 1_000_000
MAX_CHILDREN_PER_NODE = 16_384
MAX_STRING_BUDGET = 64 * 1024 * 1024
KINDS = frozenset({"directory", "file", "symlink", "other"})
ROOT_STATUSES = frozenset({"complete", "partial", "failed", "skipped"})
TREE_STATUSES = frozenset({"complete", "partial"})
STATUS_VALUES = frozenset({"complete", "partial"})
CAPACITY_ID_RE = re.compile(r"^dev-(0|[1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})$")
MEDIA_VALUES = frozenset({"ssd", "hdd", "mixed", "unknown"})
MEDIA_CONFIDENCE_VALUES = frozenset({"resolved", "unresolved"})
MEDIA_KEYS = ("capacity_id", "storage_media", "storage_media_confidence")


@dataclass(frozen=True)
class DesiredServer:
    server_id: str
    config_digest: Optional[str] = None


@dataclass(frozen=True)
class ValidatedSnapshot:
    server_id: str
    generation: str
    payload: Dict[str, Any]
    config_sync: str
    snapshot_availability: str = "available"


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.strings = 0

    def string(self, value: str, label: str) -> None:
        self.strings += len(value)
        if self.strings > MAX_STRING_BUDGET:
            raise ValueError(f"string budget exceeded at {label}")

    def node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_TREE_NODES:
            raise ValueError("tree node budget exceeded")


def validate_download(status: Mapping[str, Any], downloaded: bytes, desired: DesiredServer) -> ValidatedSnapshot:
    if not isinstance(status, Mapping):
        raise ValueError("status must be an object")
    server_id = _safe_id(desired.server_id, "desired server_id")
    desired_digest = _required_digest(desired.config_digest, "desired config_digest")
    generation = _generation_filename(status.get("generation"))
    if not isinstance(downloaded, (bytes, bytearray)):
        raise ValueError("downloaded snapshot must be bytes")
    if len(downloaded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot bytes exceed maximum")
    byte_size = _int(status.get("byte_size"), "byte_size", 0, min(MAX_COUNTER, MAX_SNAPSHOT_BYTES + 1))
    if byte_size != len(downloaded):
        raise ValueError("byte_size does not match downloaded bytes")
    sha = _required_digest(status.get("sha256"), "sha256")
    if hashlib.sha256(downloaded).hexdigest() != sha:
        raise ValueError("sha256 does not match downloaded bytes")
    if status.get("server_id") != server_id:
        raise ValueError("status server_id does not match configured server")
    status_value = status.get("status")
    if status_value not in STATUS_VALUES:
        raise ValueError("status.status must be complete or partial")
    status_finished = _int(status.get("scan_finished_unix"), "status scan_finished_unix", 0, MAX_TIME + 1)
    status_digest = _required_digest(status.get("config_digest"), "status config_digest")
    try:
        payload = json.loads(bytes(downloaded).decode("utf-8"))
    except Exception as exc:
        raise ValueError("snapshot payload must be a JSON object") from exc
    _validate_payload(payload, server_id, generation, status_finished, status_value, status_digest)
    config_sync = "in_sync" if status_digest == desired_digest else "drifted"
    return ValidatedSnapshot(server_id=server_id, generation=generation, payload=payload, config_sync=config_sync)


def _validate_payload(payload: Any, server_id: str, generation_filename: str, status_finished: int, status_value: str, status_digest: str) -> None:
    budget = _Budget()
    if not isinstance(payload, dict):
        raise ValueError("snapshot payload must be a JSON object")
    if payload.get("schema_version") != 1 or isinstance(payload.get("schema_version"), bool):
        raise ValueError("unsupported schema major version")
    for key in ("hostname", "scanner_version"):
        _string(payload.get(key), key, budget)
    if payload.get("server_id") != server_id:
        raise ValueError("payload server_id does not match configured server")
    payload_digest = _required_digest(payload.get("config_digest"), "payload config_digest")
    if payload_digest != status_digest:
        raise ValueError("payload config_digest must equal status config_digest")
    if not isinstance(payload.get("run_as_root"), bool):
        raise ValueError("run_as_root must be boolean")
    started = _int(payload.get("scan_started_unix"), "scan_started_unix", 0, MAX_TIME + 1)
    finished = _int(payload.get("scan_finished_unix"), "scan_finished_unix", 0, MAX_TIME + 1)
    duration = _int(payload.get("scan_duration_sec"), "scan_duration_sec", 0, 31_536_001)
    if finished < started:
        raise ValueError("scan_finished_unix precedes scan_started_unix")
    if finished != started + duration:
        raise ValueError("scan_finished_unix must equal started plus duration")
    if status_finished != finished:
        raise ValueError("status scan_finished_unix does not match payload")
    expected_generation = f"{server_id}-{started}-v1"
    if generation_filename != expected_generation + ".json":
        raise ValueError("generation must bind server_id, scan_started_unix, and v1")
    if payload.get("scan_generation") != expected_generation:
        raise ValueError("embedded scan_generation must bind server_id, scan_started_unix, and v1")

    roots = payload.get("selected_roots")
    if not isinstance(roots, list) or not roots:
        raise ValueError("selected_roots must be a non-empty array")
    if len(roots) > MAX_SELECTED_ROOTS:
        raise ValueError("selected_roots exceeds maximum")
    roots_by_id: Dict[str, Dict[str, Any]] = {}
    roots_by_scan_root = set()
    tree_roots: Set[str] = set()
    all_roots: Set[str] = set()
    complete_roots = 0
    problem_roots = 0
    for root in roots:
        _validate_root(root, budget)
        mid = root["mount_id"]
        scan_root = root["scan_root"]
        if mid in roots_by_id or scan_root in roots_by_scan_root:
            raise ValueError("selected roots must have unique mount_id and scan_root")
        roots_by_id[mid] = root
        roots_by_scan_root.add(scan_root)
        all_roots.add(scan_root)
        if root["status"] == "complete":
            complete_roots += 1
            tree_roots.add(scan_root)
        elif root["status"] == "partial":
            problem_roots += 1
            tree_roots.add(scan_root)
        elif root["status"] == "failed":
            problem_roots += 1
        if root["status"] in {"failed", "skipped"} and (root["scanned_bytes"] or root["scanned_files"] or root["scanned_dirs"]):
            raise ValueError("failed/skipped roots must have zero scanned totals")

    mounts = payload.get("mounts", [])
    if not isinstance(mounts, list):
        raise ValueError("mounts must be an array")
    if len(mounts) > MAX_MOUNTS:
        raise ValueError("mounts exceeds maximum")
    mounts_by_id: Dict[str, Dict[str, Any]] = {}
    for mount in mounts:
        _validate_mount_shape(mount, budget)
        mid = mount["mount_id"]
        if mid in mounts_by_id:
            raise ValueError("mount identities must be unique")
        if mid not in roots_by_id:
            raise ValueError("mount must reference a selected root")
        mounts_by_id[mid] = mount

    for mid, root in roots_by_id.items():
        mount = mounts_by_id.get(mid)
        if root["status"] in TREE_STATUSES:
            if mount is None:
                raise ValueError("complete or partial selected root must have mount")
            _validate_root_mount_link(root, mount)
        elif mount is not None:
            raise ValueError("failed/skipped selected root must not have mount")
    if complete_roots < 1:
        raise ValueError("snapshot must include at least one completed root")
    derived = "partial" if problem_roots else "complete"
    if status_value != derived:
        raise ValueError("status.status does not match selected root results")

    _validate_users(payload.get("users", []), tree_roots, budget)
    _validate_file_rows(payload.get("top_files", []), "top_files", stale=False, tree_roots=tree_roots, budget=budget)
    _validate_file_rows(payload.get("stale", []), "stale", stale=True, tree_roots=tree_roots, budget=budget)
    _validate_blocked(payload.get("blocked", []), all_roots, budget)


def _validate_root(root: Any, budget: _Budget) -> None:
    if not isinstance(root, dict):
        raise ValueError("selected root must be an object")
    _safe_id(_string(root.get("mount_id"), "mount_id", budget), "mount_id")
    _string(root.get("major_minor"), "major_minor", budget)
    if not re.match(r"^\d+:\d+$", root["major_minor"]):
        raise ValueError("major_minor must be N:N")
    for key in ("mount_source", "fstype"):
        _bounded_string(root.get(key), key, 512, budget)
    for key in ("mount_root", "mountpoint", "scan_root"):
        _abs_path(root.get(key), key, budget)
    status = root.get("status")
    if status not in ROOT_STATUSES:
        raise ValueError("selected root has invalid status")
    for key in ("scanned_bytes", "scanned_files", "scanned_dirs", "blocked_count", "error_count"):
        _counter(root.get(key), key)
    error_code = root.get("error_code")
    if error_code is not None:
        _bounded_string(error_code, "error_code", 127, budget)
    _validate_media_fields(root, "selected root", budget)


def _validate_mount_shape(mount: Any, budget: _Budget) -> None:
    if not isinstance(mount, dict):
        raise ValueError("mount must be an object")
    for key in ("path", "scan_root"):
        _abs_path(mount.get(key), f"mount.{key}", budget)
    _safe_id(_string(mount.get("mount_id"), "mount.mount_id", budget), "mount.mount_id")
    _bounded_string(mount.get("fstype"), "mount.fstype", 128, budget)
    for key in ("df_total", "df_used", "df_avail", "scanned_bytes", "scanned_files", "scanned_dirs", "errors"):
        _counter(mount.get(key), f"mount.{key}")
    _int(mount.get("df_use_pct"), "mount.df_use_pct", 0, 101)
    if mount["df_used"] + mount["df_avail"] > mount["df_total"]:
        raise ValueError("mount df values violate total invariant")
    tree = mount.get("tree")
    _validate_tree(tree, budget, depth=0)
    if tree["bytes"] != mount["scanned_bytes"] or tree["files"] != mount["scanned_files"]:
        raise ValueError("mount tree totals must match scanned totals")
    _validate_media_fields(mount, "mount", budget)


def _validate_root_mount_link(root: Mapping[str, Any], mount: Mapping[str, Any]) -> None:
    if mount["scan_root"] != root["scan_root"]:
        raise ValueError("mount scan_root must match selected root")
    if mount["path"] != root["scan_root"]:
        raise ValueError("mount path must match selected root scan_root")
    if mount["fstype"] != root["fstype"]:
        raise ValueError("mount fstype must match selected root fstype")
    for key in ("scanned_bytes", "scanned_files", "scanned_dirs"):
        if root[key] != mount[key]:
            raise ValueError(f"selected root {key} must equal linked mount")
    if root["error_count"] != mount["errors"]:
        raise ValueError("selected root error_count must equal mount errors")
    for key in MEDIA_KEYS:
        if (key in root) != (key in mount):
            raise ValueError("root and linked mount media fields must both be omitted or present")
        if key in root and root[key] != mount[key]:
            raise ValueError(f"root and linked mount {key} must match")


def _validate_media_fields(record: Mapping[str, Any], label: str, budget: _Budget) -> None:
    present = {key for key in MEDIA_KEYS if key in record}
    if not present:
        return
    if "storage_media" not in present or "storage_media_confidence" not in present:
        raise ValueError(f"{label} media fields require storage_media and storage_media_confidence")
    if "capacity_id" in present:
        capacity_id = _bounded_string(record.get("capacity_id"), f"{label}.capacity_id", 31, budget)
        if not CAPACITY_ID_RE.match(capacity_id) or capacity_id == "dev-0-0":
            raise ValueError(f"{label}.capacity_id must match dev-major-minor")
    media = _bounded_string(record.get("storage_media"), f"{label}.storage_media", 7, budget)
    confidence = _bounded_string(record.get("storage_media_confidence"), f"{label}.storage_media_confidence", 10, budget)
    if media not in MEDIA_VALUES:
        raise ValueError(f"{label}.storage_media has invalid value")
    if confidence not in MEDIA_CONFIDENCE_VALUES:
        raise ValueError(f"{label}.storage_media_confidence has invalid value")
    if media == "unknown" and confidence != "unresolved":
        raise ValueError(f"{label} unknown storage_media must pair with unresolved confidence")
    if media in {"ssd", "hdd", "mixed"} and confidence != "resolved":
        raise ValueError(f"{label} concrete storage_media must pair with resolved confidence")


def _validate_tree(node: Any, budget: _Budget, *, depth: int) -> None:
    budget.node()
    if depth > MAX_TREE_DEPTH:
        raise ValueError("tree depth exceeds maximum")
    if not isinstance(node, dict):
        raise ValueError("tree node must be an object")
    _bounded_string(node.get("name"), "tree.name", 4096, budget)
    if node.get("kind") not in KINDS:
        raise ValueError("tree node has invalid kind")
    _counter(node.get("bytes"), "tree.bytes")
    _counter(node.get("files"), "tree.files")
    _counter(node.get("uid"), "tree.uid")
    _int(node.get("mtime"), "tree.mtime", 0, MAX_TIME + 1)
    other = _counter(node.get("other_bytes", 0), "tree.other_bytes")
    children = node.get("children", [])
    if not isinstance(children, list):
        raise ValueError("tree children must be an array")
    if len(children) > MAX_CHILDREN_PER_NODE:
        raise ValueError("tree children exceeds maximum")
    child_bytes = 0
    child_files = 0
    for child in children:
        _validate_tree(child, budget, depth=depth + 1)
        child_bytes += child["bytes"]
        child_files += child["files"]
        if child_files > node["files"]:
            raise ValueError("tree child files exceed parent files")
    if children and node["bytes"] != child_bytes + other:
        raise ValueError("tree bytes must equal child bytes plus other_bytes")
    if node["files"] < child_files:
        raise ValueError("tree files must be >= child files")
    if not children and other != 0:
        raise ValueError("tree leaf other_bytes must be zero")


def _validate_users(users: Any, valid_mount_paths: Set[str], budget: _Budget) -> None:
    if not isinstance(users, list):
        raise ValueError("users must be an array")
    if len(users) > MAX_USERS:
        raise ValueError("users exceeds maximum")
    for row in users:
        if not isinstance(row, dict):
            raise ValueError("users rows must be objects")
        _counter(row.get("uid"), "user.uid")
        _bounded_string(row.get("name"), "user.name", 128, budget)
        _counter(row.get("bytes"), "user.bytes")
        _counter(row.get("files"), "user.files")
        by_mount = row.get("by_mount")
        if not isinstance(by_mount, dict):
            raise ValueError("user.by_mount must be an object")
        by_mount_sum = 0
        for path, value in by_mount.items():
            _abs_path(path, "user.by_mount path", budget)
            if path not in valid_mount_paths:
                raise ValueError("user.by_mount path must reference tree-producing mounted root")
            by_mount_sum += _counter(value, "user.by_mount bytes")
            if by_mount_sum >= MAX_COUNTER:
                raise ValueError("user.bytes by_mount sum exceeds bound")
        if row["bytes"] != by_mount_sum:
            raise ValueError("user.bytes must equal sum of by_mount values")


def _validate_file_rows(rows: Any, label: str, *, stale: bool, tree_roots: Set[str], budget: _Budget) -> None:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be an array")
    if len(rows) > MAX_FILE_ROWS:
        raise ValueError(f"{label} exceeds maximum")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label} rows must be objects")
        path = _abs_path(row.get("path"), f"{label}.path", budget)
        if not any(_path_contains(root, path) for root in tree_roots):
            raise ValueError(f"{label}.path must be equal to or descendant of tree-producing selected scan_root")
        if row.get("kind") != "file":
            raise ValueError(f"{label}.kind must be file")
        _counter(row.get("bytes"), f"{label}.bytes")
        _counter(row.get("uid"), f"{label}.uid")
        _bounded_string(row.get("owner"), f"{label}.owner", 128, budget)
        _int(row.get("mtime"), f"{label}.mtime", 0, MAX_TIME + 1)
        if stale:
            _counter(row.get("age_days"), f"{label}.age_days")
        elif "age_days" in row:
            _counter(row.get("age_days"), f"{label}.age_days")


def _validate_blocked(blocked: Any, all_roots: Set[str], budget: _Budget) -> None:
    if not isinstance(blocked, list):
        raise ValueError("blocked must be an array")
    if len(blocked) > MAX_BLOCKED:
        raise ValueError("blocked exceeds maximum")
    for row in blocked:
        if not isinstance(row, dict):
            raise ValueError("blocked rows must be objects")
        if "reason" not in row:
            raise ValueError("blocked.reason is required")
        _bounded_string(row.get("reason"), "blocked.reason", 127, budget)
        path = row.get("path")
        if path is not None:
            if not isinstance(path, str):
                raise ValueError("blocked.path must be a string when present")
            _display_path(path, "blocked.path", budget)
            if path.startswith("/"):
                _abs_path(path, "blocked.path", budget)
                if not any(_path_contains(root, path) for root in all_roots):
                    raise ValueError("blocked.path absolute canonical path must be under selected root")


def _generation_filename(value: Any) -> str:
    if not isinstance(value, str) or "/" in value or "\\" in value or value in {".", ".."} or not GENERATION_RE.match(value):
        raise ValueError("generation must be a safe immutable basename")
    return value


def _required_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not HEX64_RE.match(value):
        raise ValueError(f"{label} must be lowercase 64-hex")
    return value


def _safe_id(value: str, label: str) -> str:
    if not SAFE_ID_RE.match(value) or value in {".", ".."} or len(value) > 128:
        raise ValueError(f"{label} must be safe")
    return value


def _string(value: Any, label: str, budget: _Budget) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    if _has_control(value):
        raise ValueError(f"{label} contains control characters")
    budget.string(value, label)
    return value


def _bounded_string(value: Any, label: str, max_len: int, budget: _Budget) -> str:
    value = _string(value, label, budget)
    if len(value) > max_len:
        raise ValueError(f"{label} is too long")
    return value


def _display_path(value: str, label: str, budget: _Budget) -> str:
    if _has_control(value) or len(value) > 4096:
        raise ValueError(f"{label} is unsafe")
    budget.string(value, label)
    return value


def _abs_path(value: Any, label: str, budget: _Budget) -> str:
    value = _bounded_string(value, label, 4096, budget)
    if not value.startswith("/"):
        raise ValueError(f"{label} must be absolute path")
    if os.path.normpath(value) != value:
        raise ValueError(f"{label} must be canonical POSIX path")
    return value


def _path_contains(root: str, path: str) -> bool:
    return path == root or (root == "/" and path.startswith("/")) or path.startswith(root.rstrip("/") + "/")


def _has_control(value: str) -> bool:
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _int(value: Any, label: str, lo: int, hi_exclusive: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    if value < lo or value >= hi_exclusive:
        raise ValueError(f"{label} must be in [{lo}, {hi_exclusive})")
    return value


def _counter(value: Any, label: str) -> int:
    return _int(value, label, 0, MAX_COUNTER)
