"""Polling service for central collection of remote storage-viz snapshots."""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Mapping, Protocol

from collector.inventory import Server
from collector import snapshot
from collector.scan_telemetry import empty_status
from collector import duration_history
from collector.store import CentralStore, STATE_DEFAULTS, StoreLockedError
from collector.transport import ACTIVE_SCAN_STATES, TransportError, status_tuple, validate_status_envelope

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 900
MAX_PARALLEL_POLLS = 4
MAX_PARALLEL_SNAPSHOT_PULLS = 2
ACTIVE_TELEMETRY_INTERVAL_SECONDS = 5
IDLE_TELEMETRY_INTERVAL_SECONDS = 60
LOCAL_SCAN_CADENCE_SECONDS = 6 * 60 * 60
FRESHNESS_GRACE_SECONDS = 60 * 60
FRESH_SNAPSHOT_SECONDS = LOCAL_SCAN_CADENCE_SECONDS + FRESHNESS_GRACE_SECONDS
SCHEDULED_TIMER_DELAY_SECONDS = 6 * 60
ROOT_SCAN_CADENCE_SECONDS = {"ssd": 3600, "hdd": 3 * 3600, "mixed": 3 * 3600, "unknown": 3 * 3600}
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OVERVIEW_ROOT_FIELDS = (
    "mount_id", "capacity_id", "major_minor", "mount_source", "fstype", "storage_media", "block_media",
    "storage_media_confidence", "block_media_confidence", "scan_finished_unix",
)
OVERVIEW_MOUNT_FIELDS = (
    "mount_id", "path", "mountpoint", "df_total", "df_used", "df_avail", "df_use_pct", "df_observed_unix",
    "storage_media", "block_media", "storage_media_confidence", "block_media_confidence",
)
# Central release artifacts do not package agent.mount_policy.
REMOTE_FSTYPES = frozenset({
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "ceph",
    "fuse.ceph", "fuse.rclone", "fuse.davfs", "glusterfs", "lustre", "gpfs", "9p",
})


def _overview_rows(value: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {field: row.get(field) for field in fields}
        for row in value
        if isinstance(row, Mapping)
    ]


def _overview_snapshot(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    mounts = _overview_rows(payload.get("mounts"), OVERVIEW_MOUNT_FIELDS)
    visible_mount_ids = {row.get("mount_id") for row in mounts}
    selected_roots = [
        row
        for row in _overview_rows(payload.get("selected_roots"), OVERVIEW_ROOT_FIELDS)
        if row.get("mount_id") in visible_mount_ids
    ]
    roots = payload.get("selected_roots") or []
    legacy_success = (
        any(root.get("status") == "complete" for root in roots)
        and all(root.get("status") in {"complete", "skipped"} and root.get("scan_finished_unix") is None for root in roots)
    )
    remote_mount_paths = {
        mount["path"]
        for mount in payload.get("mounts", [])
        if mount.get("fstype", "").lower() in REMOTE_FSTYPES
    }
    users = [
        {
            "uid": user["uid"],
            "name": user["name"],
            "bytes": sum(amount for path, amount in user["by_mount"].items() if path not in remote_mount_paths),
        }
        for user in payload.get("users", [])
    ]
    return {
        "server_id": payload.get("server_id"),
        "scan_generation": payload.get("scan_generation"),
        "scan_finished_unix": payload.get("scan_finished_unix"),
        "scan_duration_sec": payload.get("scan_duration_sec"),
        "estimate_duration_sec": payload.get("scan_duration_sec") if legacy_success else None,
        "selected_roots": selected_roots,
        "mounts": mounts,
        "users": users,
    }


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def record_completed_duration(history: Mapping[str, Any], status: Mapping[str, Any]) -> dict[str, Any] | None:
    plan = status.get("scan_plan")
    duration = _count(status.get("last_duration_sec"))
    finished = status.get("finished_unix")
    if finished is None:
        finished = status.get("last_finished_unix")
    if status.get("state") != "complete" or plan not in duration_history.PLANS or duration is None or not _count(finished):
        return None
    entries = history.get(plan, [])
    if any(recorded == finished for recorded, _ in entries):
        return None
    updated = sorted([*entries, [finished, duration]])[-duration_history.LIMIT:]
    if updated == sorted(entries):
        return None
    return {**history, plan: [list(entry) for entry in updated]}


def conservative_estimate(history: Mapping[str, Any], plan: str, agent_estimate: Any) -> int | None:
    candidates = [duration for _, duration in history.get(plan, [])]
    if _count(agent_estimate) is not None:
        candidates.append(agent_estimate)
    return max(candidates, default=None)


class Clock(Protocol):
    def time(self) -> float: ...


class Transport(Protocol):
    def fetch_status(self, server: Server) -> Mapping[str, Any]: ...
    def fetch_snapshot(self, server: Server, expected_status: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], bytes]: ...
    def scan_active_state(self, server: Server) -> str: ...
    def start_rescan(self, server: Server) -> None: ...


@dataclass(frozen=True)
class ScanObservation:
    started_at: float
    status: Mapping[str, Any]


@dataclass(frozen=True)
class VerifiedPublication:
    status: tuple[Any, ...]
    freshness_deadline: int | None


@dataclass(frozen=True)
class _DeferredSnapshotPull:
    status: Mapping[str, Any]


class _DaemonWorkQueue:
    def __init__(self, name: str, limit: int) -> None:
        self._name = name
        self._limit = limit
        self._lock = threading.Lock()
        self._pending: dict[str, Callable[[], Any]] = {}
        self._scheduled: set[str] = set()
        self._workers = 0

    def submit(self, server_id: str, task: Callable[[], Any]) -> None:
        with self._lock:
            if server_id in self._pending:
                self._pending[server_id] = task
                return
            if server_id in self._scheduled:
                return
            self._scheduled.add(server_id)
            self._pending[server_id] = task
            if self._workers < min(self._limit, len(self._scheduled)):
                worker = threading.Thread(target=self._drain, name=self._name, daemon=True)
                try:
                    worker.start()
                except Exception:
                    del self._pending[server_id]
                    self._scheduled.remove(server_id)
                    raise
                self._workers += 1

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._workers -= 1
                    return
                server_id = next(iter(self._pending))
                task = self._pending.pop(server_id)
            try:
                task()
            except Exception:
                logger.exception("%s failed for %s", self._name, server_id)
            finally:
                with self._lock:
                    self._scheduled.remove(server_id)


class PollService:
    def __init__(self, servers: list[Server] | tuple[Server, ...], central_store: CentralStore, transport: Transport, *, clock: Clock | None = None, poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self.servers = tuple(s for s in servers if s.enabled)
        self._by_id = {s.id: s for s in self.servers}
        self.store = central_store
        self.transport = transport
        self.clock = clock or time
        self._verified_publications: dict[str, VerifiedPublication] = {}
        self._last_observed_status: dict[str, tuple[Any, ...]] = {}
        self._locks = {s.id: threading.RLock() for s in self.servers}
        self._summary_lock = threading.RLock()
        self._snapshot_pull_slots = threading.BoundedSemaphore(MAX_PARALLEL_SNAPSHOT_PULLS)
        self._background_polls = _DaemonWorkQueue("storage-viz-status-reader", MAX_PARALLEL_POLLS)
        self._background_pulls = _DaemonWorkQueue("storage-viz-snapshot-pull", MAX_PARALLEL_SNAPSHOT_PULLS)
        self._telemetry_cache: dict[str, ScanObservation] = {}
        self._telemetry_threads: dict[str, threading.Thread] = {}
        self._refresh_threads: dict[str, threading.Thread] = {}
        self._duration_history = {server.id: self._load_duration_history(server.id) for server in self.servers}
        self._overview_cache = {
            server.id: _overview_snapshot(self.store.load_snapshot(server.id))
            for server in self.servers
        }
        if not isinstance(poll_interval_seconds, int) or not 600 <= poll_interval_seconds <= 900:
            raise ValueError("poll interval must be between 600 and 900 seconds")
        self.poll_interval_seconds = poll_interval_seconds

    def poll_once(self) -> dict[str, dict[str, Any]]:
        return {server.id: self._poll_fleet_server(server.id) for server in self.servers}

    def poll_background_once(self) -> None:
        for server in self.servers:
            self._background_polls.submit(server.id, partial(self._poll_fleet_server, server.id, defer_snapshot=True))

    def _poll_fleet_server(self, server_id: str, *, defer_snapshot: bool = False) -> dict[str, Any]:
        try:
            return self.poll_server(server_id, defer_snapshot=defer_snapshot)
        except Exception:
            logger.exception("regular poll failed for %s", server_id)
            overview = self._overview_cache.get(server_id)
            now = int(self.clock.time())
            return {
                **STATE_DEFAULTS,
                "snapshot_availability": "available" if overview is not None else "absent",
                "freshness": _snapshot_freshness(overview, now, self.poll_interval_seconds),
                "latest_pull_status": "invalid_snapshot",
                "last_error_code": "POLL_FAILED",
                "last_error_unix": now,
            }

    def poll_server(self, server_id: str, *, defer_snapshot: bool = False,
                    observed_publication: Mapping[str, Any] | None = None) -> dict[str, Any]:
        server = self._require_server(server_id)
        lock = self._locks[server.id]
        if not lock.acquire(blocking=False):
            return self.store.load_state(server.id)
        try:
            result = self._poll_server_locked(
                server, defer_snapshot=defer_snapshot, observed_publication=observed_publication,
            )
        except StoreLockedError:
            return self.store.load_state(server.id)
        finally:
            lock.release()
        if isinstance(result, _DeferredSnapshotPull):
            self._background_pulls.submit(server.id, partial(self._finish_deferred_pull, server, result.status))
            return self.store.load_state(server.id)
        return result

    def _poll_server_locked(self, server: Server, *, defer_snapshot: bool = False,
                            observed_publication: Mapping[str, Any] | None = None) -> dict[str, Any] | _DeferredSnapshotPull:
        try:
            committed_finished = (self._overview_cache.get(server.id) or {}).get("scan_finished_unix")
            if observed_publication is not None and committed_finished is not None and observed_publication["scan_finished_unix"] <= committed_finished:
                observed_publication = None
            envelope = observed_publication if observed_publication is not None else self.transport.fetch_status(server)
            status = validate_status_envelope(envelope, server)
        except TransportError as exc:
            if exc.code in {"UNREACHABLE", "TIMEOUT"}:
                return self._mark_unreachable(server.id, exc)
            return self._mark_invalid(server.id, exc)
        except Exception as exc:
            return self._mark_invalid(server.id, TransportError("BAD_STATUS", "status envelope is invalid"))

        self._last_observed_status[server.id] = status_tuple(status)
        if status["status"] == "failed":
            self._verified_publications.pop(server.id, None)
            return self._mark_failed_status(server, status)

        verified = self._matching_publication(server.id, status)
        if verified:
            return self._mark_unchanged(server, status, verified.freshness_deadline)

        if defer_snapshot:
            return _DeferredSnapshotPull(status)
        return self._transfer_publication(server, status, recheck_stale=observed_publication is not None)

    def _matching_publication(self, server_id: str, status: Mapping[str, Any]) -> VerifiedPublication | None:
        verified = self._verified_publications.get(server_id)
        if verified and verified.status == status_tuple(status) and self.store.has_snapshot_digest(server_id, status["sha256"]):
            return verified
        return None

    def _finish_deferred_pull(self, server: Server, status: Mapping[str, Any]) -> None:
        with self._locks[server.id]:
            if self._last_observed_status.get(server.id) != status_tuple(status):
                return
            overview = self._overview_cache.get(server.id) or {}
            finished = overview.get("scan_finished_unix")
            if isinstance(finished, int) and finished > status["scan_finished_unix"]:
                return
            if self._matching_publication(server.id, status):
                return
            self._transfer_publication(server, status, recheck_stale=True)

    def _transfer_publication(self, server: Server, status: Mapping[str, Any], *, recheck_stale: bool) -> dict[str, Any]:
        try:
            with self._snapshot_pull_slots:
                return self._pull_snapshot(server, status, recheck_stale=recheck_stale)
        except TransportError:
            return self._poll_server_locked(server)

    def _pull_snapshot(self, server: Server, status: Mapping[str, Any], *, recheck_stale: bool) -> dict[str, Any]:
        try:
            fetched_status, data = self.transport.fetch_snapshot(server, expected_status=status)
            fetched_status = validate_status_envelope(fetched_status, server)
        except TransportError as exc:
            if recheck_stale and exc.code in {"RACE", "UNREACHABLE"}:
                raise
            if exc.code in {"UNREACHABLE", "TIMEOUT"}:
                return self._mark_unreachable(server.id, exc)
            return self._mark_invalid(server.id, exc)
        except Exception:
            return self._mark_invalid(server.id, TransportError("INVALID", "snapshot fetch failed"))

        desired = snapshot.DesiredServer(server_id=server.id, config_digest=server.scanner_digest)
        try:
            validated = snapshot.validate_download(fetched_status, data, desired)
        except Exception as exc:
            return self._mark_invalid(server.id, TransportError("INVALID", str(exc)))

        deadline = _snapshot_freshness_deadline(validated.payload, self.poll_interval_seconds)
        overview = _overview_snapshot(validated.payload)
        with self._summary_lock:
            result = self.store.commit_snapshot(validated, freshness=_freshness_at(deadline, int(self.clock.time())))
            if not result.accepted:
                return self._mark_invalid(server.id, TransportError(result.error_code or "WRITE_ERROR", result.message or "download apply failed"))
            self._overview_cache[server.id] = overview
            self._verified_publications[server.id] = VerifiedPublication(status_tuple(fetched_status), deadline)
            return self.store.load_state(server.id)

    def manual_rescan(self, server_id: str) -> dict[str, Any]:
        server = self._require_server(server_id)
        lock = self._locks[server.id]
        lock.acquire()
        try:
            return self._manual_rescan_locked(server)
        finally:
            lock.release()

    def scan_active_state(self, server_id: str) -> str:
        server = self._require_server(server_id)
        return self.transport.scan_active_state(server)

    def remote_scan_is_active(self, server_id: str) -> bool:
        return self.scan_active_state(server_id) in ACTIVE_SCAN_STATES

    def _manual_rescan_locked(self, server: Server) -> dict[str, Any]:
        try:
            state = self.transport.scan_active_state(server)
            if state in ACTIVE_SCAN_STATES:
                return self._mark_rescan_failed(server.id, TransportError("ACTIVE_JOB", "remote scan already active"))
            self.transport.start_rescan(server)
        except TransportError as exc:
            if exc.code == "RESCAN_FAILED":
                return self._mark_rescan_failed(server.id, exc)
            return self._mark_unreachable(server.id, exc)
        except Exception:
            return self._mark_unreachable(server.id, TransportError("UNREACHABLE", "rescan failed"))
        return self._poll_server_locked(server)


    def _load_duration_history(self, server_id: str) -> dict[str, Any]:
        try:
            return duration_history.load(duration_history.path_for(self.store.root, server_id))
        except ValueError:
            logger.exception("scan duration history unavailable for %s", server_id)
            return {}

    def _record_duration(self, server_id: str, status: Mapping[str, Any]) -> None:
        updated = record_completed_duration(self._duration_history[server_id], status)
        if updated is None:
            return
        try:
            duration_history.save(duration_history.path_for(self.store.root, server_id), updated)
        except (OSError, ValueError):
            logger.exception("could not persist scan duration history for %s", server_id)
            return
        self._duration_history[server_id] = updated

    def _status_with_history(self, server_id: str, status: Mapping[str, Any] | None) -> dict[str, Any]:
        result = empty_status()
        overview = self._overview_cache.get(server_id) or {}
        result.update(last_finished_unix=overview.get("scan_finished_unix"),
                      last_duration_sec=overview.get("scan_duration_sec"),
                      estimate_duration_sec=overview.get("estimate_duration_sec"),
                      snapshot_generation=overview.get("scan_generation"))
        if status:
            result.update({k: v for k, v in status.items() if v is not None})
            if status.get("scan_plan") and "estimate_duration_sec" in status:
                result["estimate_duration_sec"] = status["estimate_duration_sec"]
        plan = result["scan_plan"]
        if plan in duration_history.PLANS:
            result["estimate_duration_sec"] = conservative_estimate(
                self._duration_history.get(server_id, {}), plan, result["estimate_duration_sec"],
            )
        result["snapshot_generation"] = overview.get("scan_generation")
        result["snapshot_finished_unix"] = overview.get("scan_finished_unix")
        return result

    def scan_status_for_api(self, server_id: str) -> dict[str, Any]:
        server = self._require_server(server_id)
        observation = self._telemetry_cache.get(server.id)
        return self._status_with_history(server.id, observation.status if observation else None)

    def poll_scan_status_once(self) -> None:
        now = self.clock.time()
        for server in self.servers:
            worker = self._telemetry_threads.get(server.id)
            if worker is not None and worker.is_alive():
                continue
            observation = self._telemetry_cache.get(server.id)
            if observation is not None:
                elapsed = now - observation.started_at
                if 0 <= elapsed < ACTIVE_TELEMETRY_INTERVAL_SECONDS:
                    continue
                if 0 <= elapsed < IDLE_TELEMETRY_INTERVAL_SECONDS:
                    job = self.store.load_state(server.id).get("active_job") or {}
                    if observation.status.get("state") != "running" and job.get("state") not in {"requested", "running"}:
                        continue
            worker = threading.Thread(target=self._collect_scan_status, args=(server, now), daemon=True)
            self._telemetry_threads[server.id] = worker
            worker.start()

    def _collect_scan_status(self, server: Server, started_at: float) -> None:
        status: dict[str, Any]
        publication = None
        try:
            if hasattr(self.transport, "fetch_scan_status"):
                observation = self.transport.fetch_scan_status(server)
                status = dict(observation.status)
                publication = observation.publication
            else:
                active = self.transport.scan_active_state(server)
                status = {"state": "running" if active in ACTIVE_SCAN_STATES else "idle", "progress_available": False}
            status["observed_unix"] = int(self.clock.time())
        except Exception as exc:
            status = {"state": "unknown", "error_code": getattr(exc, "code", "TELEMETRY_UNAVAILABLE"), "progress_available": False}
        self._telemetry_cache[server.id] = ScanObservation(started_at, status)
        self._record_duration(server.id, status)
        published = status.get("published_generation")
        if not published and status.get("state") in {"complete", "partial"}:
            published = status.get("generation")
        overview = self._overview_cache.get(server.id) or {}
        worker = self._refresh_threads.get(server.id)
        if published and published != overview.get("scan_generation") and (worker is None or not worker.is_alive()):
            worker = threading.Thread(
                target=self.poll_server, args=(server.id,), kwargs={"observed_publication": publication}, daemon=True
            )
            self._refresh_threads[server.id] = worker
            worker.start()

    def server_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        with self._summary_lock:
            for server in self.servers:
                overview = self._overview_cache.get(server.id)
                state = self.store.load_state(server.id)
                summaries.append({
                    "id": server.id,
                    "display_name": server.display_name,
                    "order": server.order,
                    "snapshot_availability": state["snapshot_availability"],
                    "freshness": _snapshot_freshness(overview, int(self.clock.time()), self.poll_interval_seconds),
                    "latest_pull_status": state["latest_pull_status"],
                    "latest_scan_result": state["latest_scan_result"],
                    "configuration_sync": state["configuration_sync"],
                    "mount_count": len(overview.get("mounts", [])) if isinstance(overview, Mapping) else 0,
                    "overview_snapshot": overview,
                    "active_job": state.get("active_job"),
                    "scan_status": self.scan_status_for_api(server.id),
                })
        return summaries

    def overview_snapshot_for_api(self, server_id: str) -> Mapping[str, Any] | None:
        return self._overview_cache.get(self._require_server(server_id).id)

    def open_snapshot_for_api(self, server_id: str):
        server = self._require_server(server_id)
        return self.store.open_snapshot(server.id)

    def load_snapshot_for_api(self, server_id: str) -> Mapping[str, Any] | None:
        server = self._require_server(server_id)
        return self.store.load_snapshot(server.id)

    def load_state_for_api(self, server_id: str) -> dict[str, Any]:
        server = self._require_server(server_id)
        return self.store.load_state(server.id)

    def run_forever(self, *, stop, wait) -> None:
        while not stop():
            started = int(self.clock.time())
            self.poll_once()
            elapsed = max(0, int(self.clock.time()) - started)
            wait(max(0, self.poll_interval_seconds - elapsed))

    def _require_server(self, server_id: str) -> Server:
        if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id) or server_id in {".", ".."}:
            raise ValueError("server id is invalid")
        try:
            return self._by_id[server_id]
        except KeyError as exc:
            raise ValueError("server id is not in enabled inventory") from exc

    def _snapshot_state(self, server_id: str) -> dict[str, str]:
        current = self.store.load_snapshot(server_id)
        return {
            "snapshot_availability": "available" if current is not None else "absent",
            "freshness": _snapshot_freshness(current, int(self.clock.time()), self.poll_interval_seconds),
        }

    def _mark_unreachable(self, server_id: str, exc: TransportError) -> dict[str, Any]:
        return self.store.update_state(
            server_id,
            **self._snapshot_state(server_id),
            latest_pull_status="unreachable",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_invalid(self, server_id: str, exc: TransportError) -> dict[str, Any]:
        self._verified_publications.pop(server_id, None)
        return self.store.update_state(
            server_id,
            **self._snapshot_state(server_id),
            latest_pull_status="invalid_snapshot",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_failed_status(self, server: Server, status: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.update_state(
            server.id,
            **self._snapshot_state(server.id),
            latest_pull_status="succeeded",
            latest_scan_result="failed",
            configuration_sync=_config_sync(status, server),
            last_error_code="REMOTE_SCAN_FAILED",
            last_error_message="remote scan failed",
            last_error_unix=int(self.clock.time()),
        )

    def _mark_rescan_failed(self, server_id: str, exc: TransportError) -> dict[str, Any]:
        return self.store.update_state(
            server_id,
            **self._snapshot_state(server_id),
            latest_scan_result="failed",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_unchanged(self, server: Server, status: Mapping[str, Any], deadline: int | None) -> dict[str, Any]:
        return self.store.update_state(
            server.id,
            snapshot_availability="available",
            freshness=_freshness_at(deadline, int(self.clock.time())),
            latest_pull_status="succeeded",
            latest_scan_result=status["status"],
            configuration_sync=_config_sync(status, server),
            last_error_code=None,
            last_error_message=None,
            last_error_unix=None,
        )


def _snapshot_freshness(payload, now: int, polling_interval: int) -> str:
    return _freshness_at(_snapshot_freshness_deadline(payload, polling_interval), now)


def _freshness_at(deadline: int | None, now: int) -> str:
    if deadline is None:
        return "unknown"
    return "stale" if now > deadline else "fresh"


def _snapshot_freshness_deadline(payload, polling_interval: int) -> int | None:
    finished = payload.get("scan_finished_unix") if payload else None
    if not isinstance(finished, int) or isinstance(finished, bool):
        return None
    deadline = finished + FRESH_SNAPSHOT_SECONDS
    for root in payload.get("selected_roots", ()):
        root_finished = root.get("scan_finished_unix")
        if root_finished is not None:
            cadence = ROOT_SCAN_CADENCE_SECONDS.get(root.get("storage_media"), ROOT_SCAN_CADENCE_SECONDS["unknown"])
            deadline = min(deadline, root_finished + cadence + polling_interval + SCHEDULED_TIMER_DELAY_SECONDS)
    return deadline


def _config_sync(status: Mapping[str, Any], server: Server) -> str:
    return "in_sync" if status.get("config_digest") == server.scanner_digest else "drifted"
