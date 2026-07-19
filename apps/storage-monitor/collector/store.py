"""Central per-server snapshot/state persistence with atomic current manifest."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
import pathlib
import re
import tempfile
import time
from typing import Any, Dict, Iterator, Mapping, Optional

from collector import snapshot

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
SNAPSHOT_BASENAME_RE = re.compile(r"^snapshot-[A-Za-z0-9_.-]+\.json$")
STATE_BASENAME_RE = re.compile(r"^state-[A-Za-z0-9_.-]+\.json$")
STATE_DEFAULTS = {
    "snapshot_availability": "absent",
    "freshness": "unknown",
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
    "freshness": {"fresh", "stale", "unknown"},
    "latest_pull_status": {"succeeded", "unreachable", "invalid_snapshot", "not_installed"},
    "latest_scan_result": {"complete", "partial", "failed"},
    "configuration_sync": {"in_sync", "drifted", "unknown"},
}
ERROR_CODE_RE = re.compile(r"^[A-Z0-9_:-]{1,128}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_TS = 4_102_444_800


class AtomicWriteDurabilityUncertain(Exception):
    pass


@dataclass(frozen=True)
class ApplyResult:
    accepted: bool
    error_code: Optional[str] = None
    message: Optional[str] = None


class CentralStore:
    def __init__(self, state_root: str | pathlib.Path):
        self.root = pathlib.Path(state_root)
        if not self.root.is_absolute():
            raise ValueError("state root must be absolute")
        _ensure_dir(self.root, 0o700)
        if self.root.is_symlink():
            raise ValueError("state root must not be a symlink")
        _fsync_dir(self.root.parent)

    def apply_download(self, server_id: str, status: Mapping[str, Any], downloaded: bytes, desired: snapshot.DesiredServer) -> ApplyResult:
        sid = self._server_id(server_id)
        try:
            with self._locked(sid):
                try:
                    valid = snapshot.validate_download(status, downloaded, desired)
                    if valid.server_id != sid:
                        raise ValueError("validated server_id mismatch")
                except Exception as exc:
                    self._merge_state_best_effort(sid, {
                        "snapshot_availability": "available" if self._current_snapshot_name(sid) else "absent",
                        "latest_pull_status": "invalid_snapshot",
                        "latest_scan_result": "failed",
                        "configuration_sync": "unknown",
                        **_safe_error("INVALID", str(exc)),
                    })
                    return ApplyResult(False, "INVALID", _safe_message(str(exc)))
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
                    _, durability_uncertain = self._commit_pair(sid, snapshot_payload=valid.payload, state_updates=state_patch)
                    if durability_uncertain:
                        return ApplyResult(True, "DURABILITY_UNCERTAIN", "current manifest visible but directory fsync failed")
                    return ApplyResult(True)
                except Exception as exc:
                    return ApplyResult(False, "WRITE_ERROR", _safe_message(str(exc)))
        except BlockingIOError:
            raise
        except Exception as exc:
            return ApplyResult(False, "WRITE_ERROR", _safe_message(str(exc)))

    def update_state(self, server_id: str, **updates: Any) -> Dict[str, Any]:
        sid = self._server_id(server_id)
        with self._locked(sid):
            state, _ = self._commit_pair(sid, snapshot_payload=None, state_updates=updates)
            return state

    def load_snapshot(self, server_id: str) -> Optional[Dict[str, Any]]:
        sid = self._server_id(server_id)
        d = self._server_dir(sid)
        legacy = d / "snapshot.json"
        if legacy.is_symlink():
            raise ValueError("snapshot file must not be a symlink")
        manifest = self._load_manifest(sid, allow_missing=True)
        if manifest is None or manifest.get("snapshot") is None:
            return None
        return _read_json_file(d / _safe_snapshot_basename(manifest["snapshot"], "manifest snapshot"), "snapshot")

    def load_state(self, server_id: str) -> Dict[str, Any]:
        sid = self._server_id(server_id)
        d = self._server_dir(sid)
        legacy = d / "state.json"
        if legacy.is_symlink():
            raise ValueError("state file must not be a symlink")
        manifest = self._load_manifest(sid, allow_missing=True)
        if manifest is None or manifest.get("state") is None:
            return dict(STATE_DEFAULTS)
        return _validate_state(_read_json_file(d / _safe_state_basename(manifest["state"], "manifest state"), "state"), sid)

    def _server_id(self, server_id: str) -> str:
        if not isinstance(server_id, str) or not SERVER_ID_RE.match(server_id) or server_id in {".", ".."} or len(server_id) > 128:
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
            except OSError as exc:
                raise BlockingIOError("store locked") from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _current_snapshot_name(self, sid: str) -> Optional[str]:
        manifest = self._load_manifest(sid, allow_missing=True)
        if manifest is None:
            return None
        snap = manifest.get("snapshot")
        return snap if isinstance(snap, str) else None

    def _load_manifest(self, sid: str, *, allow_missing: bool) -> Optional[Dict[str, Any]]:
        d = self._server_dir(sid)
        path = d / "current.json"
        if path.is_symlink():
            raise ValueError("STORE_INCOHERENT: current manifest is a symlink")
        if not path.exists():
            if allow_missing:
                return None
            raise ValueError("STORE_INCOHERENT: missing current manifest")
        manifest = _read_json_file(path, "manifest")
        if not isinstance(manifest, dict) or set(manifest) != {"snapshot", "state"}:
            raise ValueError("STORE_INCOHERENT: invalid manifest structure")
        snap = manifest.get("snapshot")
        state = manifest.get("state")
        if snap is not None:
            _safe_snapshot_basename(snap, "manifest snapshot")
            _reject_symlink(d / snap, "snapshot")
        if state is not None:
            _safe_state_basename(state, "manifest state")
            _reject_symlink(d / state, "state")
        return manifest

    def _commit_pair(self, sid: str, *, snapshot_payload: Optional[Mapping[str, Any]], state_updates: Mapping[str, Any]) -> tuple[Dict[str, Any], bool]:
        d = self._ensure_server_dir(sid)
        current = self._load_manifest(sid, allow_missing=True) or {"snapshot": None, "state": None}
        current_state = dict(STATE_DEFAULTS)
        if current.get("state") is not None:
            current_state = _validate_state(_read_json_file(d / _safe_state_basename(current["state"], "manifest state"), "state"), sid)
        new_state = dict(current_state)
        new_state.update(state_updates)
        new_state = _validate_state(new_state, sid)
        if snapshot_payload is None:
            snapshot_name = current.get("snapshot")
        else:
            gen = _safe_generation(snapshot_payload.get("scan_generation"))
            snapshot_name = f"snapshot-{gen}.json"
            _write_json_atomic(d / snapshot_name, snapshot_payload)
        state_name = f"state-{time.time_ns()}.json"
        _write_json_atomic(d / state_name, new_state)
        manifest = {"snapshot": snapshot_name, "state": state_name}
        durability_uncertain = False
        try:
            _write_json_atomic(d / "current.json", manifest)
        except AtomicWriteDurabilityUncertain:
            if not self._visible_manifest_matches(sid, manifest):
                raise
            durability_uncertain = True
        return new_state, durability_uncertain

    def _visible_manifest_matches(self, sid: str, intended: Mapping[str, Any]) -> bool:
        try:
            manifest = self._load_manifest(sid, allow_missing=False)
            if manifest != intended:
                return False
            d = self._server_dir(sid)
            if intended.get("snapshot") is not None:
                _read_json_file(d / _safe_snapshot_basename(intended["snapshot"], "manifest snapshot"), "snapshot")
            if intended.get("state") is not None:
                _validate_state(_read_json_file(d / _safe_state_basename(intended["state"], "manifest state"), "state"), sid)
            return True
        except Exception:
            return False

    def _merge_state_best_effort(self, sid: str, updates: Mapping[str, Any]) -> None:
        try:
            self._commit_pair(sid, snapshot_payload=None, state_updates=updates)
        except Exception:
            pass


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
    if state not in {"requested", "running", "succeeded", "failed"}:
        raise ValueError("active_job state must be requested|running|succeeded|failed")
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
    if state == "requested":
        if started is not None or finished is not None:
            raise ValueError("active_job requested state must not have started/finished timestamps")
        if result_code is not None:
            raise ValueError("active_job requested state must not have result_code")
    elif state == "running":
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
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"STORE_INCOHERENT: invalid {label} JSON") from exc


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


def _safe_generation(value: Any) -> str:
    if not isinstance(value, str) or not SERVER_ID_RE.match(value) or len(value) > 180:
        raise ValueError("unsafe snapshot generation")
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


def _safe_error(code: str, message: str) -> Dict[str, Optional[str]]:
    return {"last_error_code": code, "last_error_message": _safe_message(message), "last_error_unix": None}


def _safe_message(message: str) -> str:
    text = str(message).splitlines()[0]
    text = re.sub(r"/[^\s]+", "[path]", text)
    text = text.replace("Traceback", "error")
    if "PRIVATE KEY" in text or "ssh-" in text:
        text = "sensitive error redacted"
    return text[:200]
