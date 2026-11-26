"""Central per-server snapshot/state persistence with atomic current manifest."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import logging
import os
import pathlib
import re
import tempfile
from typing import Any, Dict, Iterator, Mapping, Optional, TextIO
from uuid import uuid4

from collector.ids import SERVER_ID_RE
from collector import snapshot

_LOG = logging.getLogger(__name__)

SNAPSHOT_BASENAME_RE = re.compile(r"^snapshot-[A-Za-z0-9_.-]+\.json$")
STATE_BASENAME_RE = re.compile(r"^state-[A-Za-z0-9_.-]+\.json$")
# The older release's names are excluded from automatic cleanup.
_MANAGED_GENERATION = r"(?:state-v2-[a-f0-9]{32}|snapshot-v2-[a-f0-9]{64})"
MANAGED_GENERATION_RE = re.compile(rf"{_MANAGED_GENERATION}\.json")
STAGED_WRITE_RE = re.compile(rf"\.(?:{_MANAGED_GENERATION}|current|previous)\.json\.[a-z0-9_]{{8}}\.tmp")
Manifest = dict[str, Optional[str]]
EMPTY_MANIFEST: Manifest = {"snapshot": None, "state": None}
STATE_DEFAULTS = {
    "snapshot_availability": "absent",
    "latest_pull_status": "not_installed",
    "latest_scan_result": "failed",
    "configuration_sync": "unknown",
    "active_job": None,
    "last_error_code": None,
    "last_error_message": None,
    "last_error_unix": None,
}
ENUMS = {
    "snapshot_availability": {"available", "absent"},
    "latest_pull_status": {"succeeded", "unreachable", "invalid_snapshot", "not_installed"},
    "latest_scan_result": {"complete", "partial", "failed"},
    "configuration_sync": {"in_sync", "drifted", "unknown"},
}
ERROR_CODE_RE = re.compile(r"^[A-Z0-9_:-]{1,128}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_TS = 4_102_444_800


class StoreLockedError(BlockingIOError):
    pass


class _IncompleteGeneration(ValueError):
    pass


class AtomicWriteDurabilityUncertain(Exception):
    pass


@dataclass(frozen=True)
class ApplyResult:
    accepted: bool
    error_code: Optional[str] = None
    message: Optional[str] = None


SnapshotRevision = tuple[str, int, int, int, int, int]


@dataclass(frozen=True)
class OpenSnapshot:
    revision: SnapshotRevision
    stream: TextIO

    def read(self) -> Dict[str, Any]:
        return _decode_json(self.stream, "snapshot")


class CentralStore:
    def __init__(self, state_root: str | pathlib.Path):
        self.root = pathlib.Path(state_root)
        if not self.root.is_absolute():
            raise ValueError("state root must be absolute")
        _ensure_dir(self.root, 0o700)
        if self.root.is_symlink():
            raise ValueError("state root must not be a symlink")
        _fsync_dir(self.root.parent)

    def commit_snapshot(self, valid: snapshot.ValidatedSnapshot) -> ApplyResult:
        sid = self._server_id(valid.server_id)
        state_patch = {
            "snapshot_availability": "available",
            "latest_pull_status": "succeeded",
            "latest_scan_result": _scan_result(valid.payload),
            "configuration_sync": valid.config_sync,
            "last_error_code": None,
            "last_error_message": None,
            "last_error_unix": None,
        }
        try:
            with self._locked(sid):
                _, durability_uncertain = self._commit_pair(
                    sid, state_updates=state_patch,
                    snapshot_name=f"snapshot-v2-{valid.digest}.json",
                    snapshot_payload=valid.payload,
                )
            if durability_uncertain:
                return ApplyResult(True, "DURABILITY_UNCERTAIN", "current manifest visible but directory fsync failed")
            return ApplyResult(True)
        except BlockingIOError:
            raise
        except Exception as exc:
            return ApplyResult(False, "WRITE_ERROR", _safe_message(str(exc)))

    def has_snapshot_digest(self, server_id: str, digest: str) -> bool:
        sid = self._server_id(server_id)
        directory = self._server_dir(sid)
        if not directory.exists():
            return False
        with _generation_lock(directory, fcntl.LOCK_SH):
            manifest, _, _ = self._load_committed(sid)
            return manifest["snapshot"] == f"snapshot-v2-{digest}.json"

    def update_state(self, server_id: str, **updates: Any) -> Dict[str, Any]:
        sid = self._server_id(server_id)
        with self._locked(sid):
            state, _ = self._commit_pair(sid, state_updates=updates)
            return state

    def load_snapshot(self, server_id: str) -> Optional[Dict[str, Any]]:
        with self.open_snapshot(server_id) as selected:
            return selected.read() if selected is not None else None

    @contextmanager
    def open_snapshot(self, server_id: str) -> Iterator[OpenSnapshot | None]:
        sid = self._server_id(server_id)
        directory = self._server_dir(sid)
        if (directory / "snapshot.json").is_symlink():
            raise ValueError("snapshot file must not be a symlink")
        stream = None
        if directory.exists():
            with _generation_lock(directory, fcntl.LOCK_SH):
                manifest, _, _ = self._load_committed(sid)
                name = manifest["snapshot"]
                if name is not None:
                    path = directory / name
                    _reject_symlink(path, "snapshot")
                    stream = path.open(encoding="utf-8")
        if stream is None:
            yield None
            return
        # The open descriptor pins the selected snapshot while its name may be pruned.
        with stream:
            info = os.fstat(stream.fileno())
            revision = (name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            yield OpenSnapshot(revision, stream)

    def load_state(self, server_id: str) -> Dict[str, Any]:
        sid = self._server_id(server_id)
        d = self._server_dir(sid)
        legacy = d / "state.json"
        if legacy.is_symlink():
            raise ValueError("state file must not be a symlink")
        if not d.exists():
            return dict(STATE_DEFAULTS)
        with _generation_lock(d, fcntl.LOCK_SH):
            _, state, _ = self._load_committed(sid)
            return state

    def _server_id(self, server_id: str) -> str:
        if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id) or server_id in {".", ".."} or len(server_id) > 128:
            raise ValueError("server_id must be safe")
        return server_id

    def _server_dir(self, sid: str) -> pathlib.Path:
        path = self.root / sid
        if path.exists() and path.is_symlink():
            raise ValueError("server state directory must not be a symlink")
        return path

    def _ensure_server_dir(self, sid: str) -> pathlib.Path:
        d = self._server_dir(sid)
        _ensure_dir(d, 0o700)
        _fsync_dir(self.root)
        return d

    @contextmanager
    def _locked(self, sid: str) -> Iterator[None]:
        d = self._ensure_server_dir(sid)
        lock_path = d / "store.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StoreLockedError("store locked") from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _load_manifest(self, sid: str, filename: str) -> Optional[Manifest]:
        d = self._server_dir(sid)
        try:
            manifest = _read_json_file(d / filename, "manifest")
        except FileNotFoundError:
            return None
        if not isinstance(manifest, dict) or set(manifest) != {"snapshot", "state"}:
            raise _IncompleteGeneration("STORE_INCOHERENT: invalid manifest structure")
        for label, validate_name in (("snapshot", _safe_snapshot_basename), ("state", _safe_state_basename)):
            name = manifest[label]
            if name is not None:
                path = d / validate_name(name, f"manifest {label}")
                _reject_symlink(path, label)
                if not path.is_file():
                    raise _IncompleteGeneration(f"STORE_INCOHERENT: missing {label} generation")
        return manifest

    def _load_committed(self, sid: str) -> tuple[Manifest, Dict[str, Any], bool]:
        error = None
        try:
            manifest = self._load_manifest(sid, "current.json")
            if manifest is not None:
                return manifest, self._read_state(sid, manifest), False
        except (_IncompleteGeneration, FileNotFoundError) as exc:
            error = exc

        previous = self._load_manifest(sid, "previous.json")
        if previous is not None:
            return previous, self._read_state(sid, previous), True
        if error is not None:
            raise error
        return EMPTY_MANIFEST, dict(STATE_DEFAULTS), False

    def _read_state(self, sid: str, manifest: Manifest) -> Dict[str, Any]:
        name = manifest["state"]
        if name is None:
            return dict(STATE_DEFAULTS)
        state = _read_json_file(self._server_dir(sid) / name, "state")
        if isinstance(state, dict):
            state.pop("freshness", None)  # Older generations persisted this derived value.
        return _validate_state(state, sid)

    def _commit_pair(
        self, sid: str, *, state_updates: Mapping[str, Any],
        snapshot_name: Optional[str] = None, snapshot_payload: Optional[Mapping[str, Any]] = None,
    ) -> tuple[Dict[str, Any], bool]:
        d = self._server_dir(sid)
        current, current_state, recovered = self._load_committed(sid)
        new_state = _validate_state({**current_state, **state_updates}, sid)
        new_snapshot_name = snapshot_name or current["snapshot"]
        same_snapshot = new_snapshot_name == current["snapshot"]
        same_state = new_state == current_state
        if same_snapshot and same_state and not recovered:
            # A retry may see a previous manifest rename whose directory sync failed.
            if current != EMPTY_MANIFEST:
                try:
                    _fsync_dir(d)
                except OSError:
                    return current_state, True
            return current_state, False

        try:
            previous = self._load_manifest(sid, "previous.json") or EMPTY_MANIFEST
        except _IncompleteGeneration:
            previous = EMPTY_MANIFEST
        # Discard abandoned staging files before starting another attempt.
        _prune_generations(d, current, previous)

        if not same_snapshot and snapshot_payload is not None:
            _write_json_atomic(d / _safe_snapshot_basename(new_snapshot_name, "new snapshot"), snapshot_payload)
        state_name = current["state"]
        if not same_snapshot or not same_state:
            state_name = f"state-v2-{uuid4().hex}.json"
            _write_json_atomic(d / state_name, new_state)
        manifest = {"snapshot": new_snapshot_name, "state": state_name}

        if current not in (EMPTY_MANIFEST, previous):
            _write_json_atomic(d / "previous.json", current)
        try:
            _write_json_atomic(d / "current.json", manifest)
        except AtomicWriteDurabilityUncertain:
            if not self._visible_manifest_matches(sid, manifest):
                raise
            return new_state, True

        _prune_generations(d, manifest, current)
        return new_state, False

    def _visible_manifest_matches(self, sid: str, intended: Manifest) -> bool:
        try:
            manifest = self._load_manifest(sid, "current.json")
            if manifest is None or manifest != intended:
                return False
            d = self._server_dir(sid)
            if manifest["snapshot"] is not None:
                _read_json_file(d / manifest["snapshot"], "snapshot")
            self._read_state(sid, manifest)
            return True
        except Exception:
            return False


@contextmanager
def _generation_lock(directory: pathlib.Path, operation: int) -> Iterator[None]:
    # Publication is atomic. Only cleanup must wait for readers of old names.
    fd = os.open(directory / "generations.lock", os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, operation)
        yield
    finally:
        os.close(fd)


def _prune_generations(directory: pathlib.Path, *manifests: Manifest) -> None:
    keep = {name for manifest in manifests for name in manifest.values() if name is not None}
    try:
        obsolete = [
            path for path in directory.iterdir()
            if path.name not in keep and (MANAGED_GENERATION_RE.fullmatch(path.name) or STAGED_WRITE_RE.fullmatch(path.name))
        ]
        if not obsolete:
            return
        # A previous attempt may have published a visible but unsynced current pointer.
        _fsync_dir(directory)
        with _generation_lock(directory, fcntl.LOCK_EX):
            for path in obsolete:
                path.unlink(missing_ok=True)
            _fsync_dir(directory)
    except OSError as exc:
        _LOG.warning("Unable to prune storage generations for server %s (errno=%s)", directory.name, exc.errno)


def _scan_result(payload: Mapping[str, Any]) -> str:
    statuses = {root.get("status") for root in payload.get("selected_roots", []) if isinstance(root, Mapping) and root.get("status") != "skipped"}
    if statuses <= {"complete"}:
        return "complete"
    return "partial"


def _validate_state(state: Any, server_id: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    result = dict(STATE_DEFAULTS)
    result.update(state)
    unknown = set(result) - set(STATE_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown state key(s): {', '.join(sorted(unknown))}")
    for key, allowed in ENUMS.items():
        if result[key] not in allowed:
            raise ValueError(f"{key} has invalid state value")
    active = result["active_job"]
    if active is not None:
        _validate_active_job(active, server_id)
    _validate_error_fields(result)
    return result


def _validate_error_fields(result: Mapping[str, Any]) -> None:
    code = result["last_error_code"]
    message = result["last_error_message"]
    ts = result["last_error_unix"]
    if code is None and message is None and ts is None:
        return
    if code is None or message is None:
        raise ValueError("last_error fields must include code and message together")
    if not isinstance(code, str) or not ERROR_CODE_RE.match(code):
        raise ValueError("last_error_code is invalid")
    if not isinstance(message, str) or len(message) > 200:
        raise ValueError("last_error_message is invalid")
    if "/" in message or "Traceback" in message or "PRIVATE KEY" in message:
        raise ValueError("last_error_message contains sensitive detail")
    if ts is not None and (not isinstance(ts, int) or isinstance(ts, bool) or ts < 0 or ts >= MAX_TS):
        raise ValueError("last_error_unix is invalid")


def _validate_active_job(active: Any, server_id: Optional[str]) -> None:
    allowed_keys = {"id", "server_id", "kind", "state", "actor", "requested_unix", "started_unix", "finished_unix", "result_code"}
    if not isinstance(active, dict) or set(active) - allowed_keys:
        raise ValueError("active_job has unknown keys")
    required = {"id", "server_id", "kind", "state", "actor", "requested_unix"}
    missing = required - set(active)
    if missing:
        raise ValueError(f"active_job missing required field(s): {', '.join(sorted(missing))}")
    for key in ("id", "server_id", "actor"):
        if not isinstance(active.get(key), str) or not SAFE_ID_RE.match(active[key]):
            raise ValueError(f"active_job {key} must be safe bounded identifier")
    if server_id is not None and active["server_id"] != server_id:
        raise ValueError("active_job server_id must match outer server_id")
    if active.get("kind") != "rescan":
        raise ValueError("active_job kind must be rescan")
    state = active.get("state")
    if state not in {"running", "succeeded", "failed"}:
        raise ValueError("active_job state must be running|succeeded|failed")
    requested = _job_timestamp(active.get("requested_unix"), "requested_unix", required=True)
    started = _job_timestamp(active.get("started_unix"), "started_unix", required=False)
    finished = _job_timestamp(active.get("finished_unix"), "finished_unix", required=False)
    result_code = active.get("result_code")
    if result_code is not None and (not isinstance(result_code, str) or not ERROR_CODE_RE.match(result_code)):
        raise ValueError("active_job result_code must be null or safe bounded code")
    if started is not None and started < requested:
        raise ValueError("active_job started_unix must be >= requested_unix")
    if finished is not None:
        if started is None:
            raise ValueError("active_job finished_unix requires started_unix")
        if finished < started:
            raise ValueError("active_job finished_unix must be >= started_unix")
    if state == "running":
        if started is None:
            raise ValueError("active_job running state requires started_unix")
        if finished is not None:
            raise ValueError("active_job running state must not have finished_unix")
        if result_code is not None:
            raise ValueError("active_job running state must not have result_code")
    else:
        if started is None or finished is None:
            raise ValueError("active_job terminal state requires started_unix and finished_unix")
        if result_code is None:
            raise ValueError("active_job terminal state requires result_code")


def _job_timestamp(value: Any, label: str, *, required: bool) -> Optional[int]:
    if value is None:
        if required:
            raise ValueError(f"active_job {label} is required")
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value >= MAX_TS:
        raise ValueError(f"active_job {label} timestamp must be bounded integer")
    return value


def _write_json_atomic(path: pathlib.Path, data: Mapping[str, Any]) -> None:
    directory = path.parent
    if directory.is_symlink() or path.is_symlink():
        raise ValueError("final path must not be a symlink")
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(directory))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
        try:
            _fsync_dir(directory)
        except Exception as exc:
            raise AtomicWriteDurabilityUncertain(str(exc)) from exc
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json_file(path: pathlib.Path, label: str) -> Any:
    _reject_symlink(path, label)
    with path.open(encoding="utf-8") as stream:
        return _decode_json(stream, label)


def _decode_json(stream: TextIO, label: str) -> Any:
    try:
        return json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _IncompleteGeneration(f"STORE_INCOHERENT: invalid {label} JSON") from exc


def _reject_symlink(path: pathlib.Path, label: str) -> None:
    if path.is_symlink():
        raise ValueError(f"STORE_INCOHERENT: {label} file is a symlink")


def _safe_snapshot_basename(value: Any, label: str) -> str:
    return _typed_basename(value, label, SNAPSHOT_BASENAME_RE)


def _safe_state_basename(value: Any, label: str) -> str:
    return _typed_basename(value, label, STATE_BASENAME_RE)


def _typed_basename(value: Any, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.match(value) or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"STORE_INCOHERENT: unsafe {label} basename")
    return value


def _ensure_dir(path: pathlib.Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if path.is_symlink():
        raise ValueError("directory must not be a symlink")
    os.chmod(path, mode)


def _fsync_dir(path: pathlib.Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _safe_message(message: str) -> str:
    text = str(message).splitlines()[0]
    text = re.sub(r"/[^\s]+", "[path]", text)
    text = text.replace("Traceback", "error")
    if "PRIVATE KEY" in text or "ssh-" in text:
        text = "sensitive error redacted"
    return text[:200]
