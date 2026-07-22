"""Bounded per-server manual rescan job coordination."""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Any, Mapping, Protocol

SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
SAFE_FIELD_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
ACTIVE_SCAN_STATES = frozenset({"active", "activating", "reloading"})
DEFAULT_COOLDOWN_SECONDS = 15 * 60
DEFAULT_MAX_CONCURRENT_JOBS = 2
MAX_AUDIT_EVENTS = 100


class Clock(Protocol):
    def time(self) -> float: ...


class Store(Protocol):
    def load_state(self, server_id: str) -> dict[str, Any]: ...
    def update_state(self, server_id: str, **updates: Any) -> dict[str, Any]: ...


class Service(Protocol):
    servers: tuple[Any, ...]
    store: Store
    def scan_active_state(self, server_id: str) -> str: ...
    def manual_rescan(self, server_id: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _ServerRef:
    id: str


class RescanJobManager:
    """Coordinates manual rescans without accepting arbitrary commands/paths."""

    def __init__(self, service: Service, *, clock: Clock | None = None, cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS, max_concurrent: int = DEFAULT_MAX_CONCURRENT_JOBS) -> None:
        self.service = service
        self.store = service.store
        self.clock = clock or time
        self.cooldown_seconds = _bounded_int(cooldown_seconds, "cooldown", 0, 24 * 60 * 60)
        self.max_concurrent = _bounded_int(max_concurrent, "max_concurrent", 1, 16)
        self._servers = {s.id: _ServerRef(s.id) for s in service.servers if getattr(s, "enabled", True)}
        self._lock = threading.RLock()
        self._active_global = 0
        self._last_requested: dict[str, int] = {}
        self._threads: list[threading.Thread] = []
        self._audit: list[dict[str, Any]] = []
        self._counter = 0
        self._reconcile_persisted_jobs()

    def request_rescan(self, server_id: str, actor: str) -> tuple[int, dict[str, Any]]:
        now = int(self.clock.time())
        sid = _safe_server_id(server_id)
        actor_id = _safe_field(actor or "anonymous")
        if sid is None or sid not in self._servers:
            event = self._record(now, actor_id, sid or "unknown", "UNKNOWN_SERVER", None)
            return 404, {"error": "UNKNOWN_SERVER", "audit": event}
        with self._lock:
            self._prune_threads_locked()
            current = self.store.load_state(sid).get("active_job")
            if isinstance(current, Mapping) and current.get("state") in {"requested", "running"}:
                event = self._record(now, actor_id, sid, "ACTIVE_JOB", _safe_field(current.get("id", "job")))
                return 409, {"error": "ACTIVE_JOB", "job": dict(current), "audit": event}
            try:
                remote_state = self.service.scan_active_state(sid)
            except Exception as exc:
                code = _safe_code(getattr(exc, "code", "ACTIVE_STATE_FAILED"))
                event = self._record(now, actor_id, sid, "ACTIVE_STATE_FAILED", None)
                return 503, {"error": "ACTIVE_STATE_FAILED", "code": code, "audit": event}
            if remote_state in ACTIVE_SCAN_STATES:
                event = self._record(now, actor_id, sid, "ACTIVE_JOB", None)
                return 409, {"error": "ACTIVE_JOB", "remote_active_state": _safe_field(remote_state), "audit": event}
            last = self._last_requested.get(sid)
            if last is not None and now - last < self.cooldown_seconds:
                event = self._record(now, actor_id, sid, "COOLDOWN", None)
                return 429, {"error": "COOLDOWN", "retry_after_seconds": self.cooldown_seconds - (now - last), "audit": event}
            if self._active_global >= self.max_concurrent:
                event = self._record(now, actor_id, sid, "GLOBAL_CONCURRENCY", None)
                return 429, {"error": "GLOBAL_CONCURRENCY", "audit": event}
            self._counter += 1
            job_id = _safe_field(f"rescan-{sid}-{now}-{self._counter}")
            job = {"id": job_id, "server_id": sid, "kind": "rescan", "state": "running", "actor": actor_id, "requested_unix": now, "started_unix": now, "finished_unix": None, "result_code": None}
            self.store.update_state(sid, active_job=job)
            self._last_requested[sid] = now
            self._active_global += 1
            event = self._record(now, actor_id, sid, "ACCEPTED", job_id)
            thread = threading.Thread(target=self._run, args=(sid, job), daemon=True)
            self._threads.append(thread)
            thread.start()
            return 202, {"status": "started", "job": job, "audit": event}

    def job_for(self, server_id: str) -> tuple[int, dict[str, Any]]:
        sid = _safe_server_id(server_id)
        if sid is None or sid not in self._servers:
            return 404, {"error": "UNKNOWN_SERVER"}
        return 200, {"job": self.store.load_state(sid).get("active_job")}

    def audit_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._audit]

    def wait_for_idle(self, timeout: float | None = None) -> None:
        deadline = None if timeout is None else time.time() + timeout
        for thread in list(self._threads):
            remaining = None if deadline is None else max(0.0, deadline - time.time())
            thread.join(remaining)
        with self._lock:
            self._prune_threads_locked()

    def active_thread_count(self) -> int:
        with self._lock:
            self._prune_threads_locked()
            return len(self._threads)

    def _reconcile_persisted_jobs(self) -> None:
        now = int(self.clock.time())
        for sid in self._servers:
            state = self.store.load_state(sid)
            active = state.get("active_job")
            if not isinstance(active, Mapping):
                continue
            requested = active.get("requested_unix")
            if isinstance(requested, int) and not isinstance(requested, bool) and now - requested < self.cooldown_seconds:
                self._last_requested[sid] = requested
            if active.get("state") not in {"requested", "running"}:
                continue
            started = active.get("started_unix")
            if not isinstance(started, int) or isinstance(started, bool):
                started = max(requested if isinstance(requested, int) and not isinstance(requested, bool) else now, now)
            terminal = {
                "id": _safe_field(active.get("id", f"rescan-{sid}-{now}")),
                "server_id": sid,
                "kind": "rescan",
                "state": "failed",
                "actor": _safe_field(active.get("actor", "unknown")),
                "requested_unix": requested if isinstance(requested, int) and not isinstance(requested, bool) else now,
                "started_unix": started,
                "finished_unix": max(now, started),
                "result_code": "INTERRUPTED",
            }
            self.store.update_state(sid, active_job=terminal)

    def _prune_threads_locked(self) -> None:
        self._threads = [thread for thread in self._threads if thread.is_alive()]

    def _run(self, sid: str, job: dict[str, Any]) -> None:
        result_code = "OK"
        state = "succeeded"
        try:
            result = self.service.manual_rescan(sid)
            if result.get("latest_pull_status") != "succeeded" or result.get("latest_scan_result") == "failed":
                state = "failed"
                result_code = _safe_code(result.get("last_error_code") or result.get("latest_pull_status") or "FAILED")
        except Exception:
            state = "failed"
            result_code = "RESCAN_FAILED"
        finished = int(self.clock.time())
        terminal = dict(job, state=state, finished_unix=finished, result_code=result_code)
        try:
            self.store.update_state(sid, active_job=terminal)
        finally:
            with self._lock:
                self._active_global = max(0, self._active_global - 1)
                self._prune_threads_locked()

    def _record(self, ts: int, actor: str, server_id: str, result_code: str, job_id: str | None) -> dict[str, Any]:
        event = {"timestamp_unix": ts, "actor": _safe_field(actor), "server_id": _safe_field(server_id), "result_code": _safe_field(result_code), "job_id": _safe_field(job_id or "none")}
        with self._lock:
            self._audit.append(event)
            del self._audit[:-MAX_AUDIT_EVENTS]
        return dict(event)


def _safe_server_id(value: Any) -> str | None:
    if not isinstance(value, str) or not SERVER_ID_RE.fullmatch(value) or value in {".", ".."}:
        return None
    return value


def _safe_field(value: Any) -> str:
    text = SAFE_FIELD_RE.sub("_", str(value))[:128].strip("._:-")
    return text or "unknown"


def _safe_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_:-]+", "_", str(value).upper())[:128].strip("_:-") or "FAILED"


def _bounded_int(value: Any, label: str, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
        raise ValueError(f"{label} must be an integer between {lo} and {hi}")
    return value
