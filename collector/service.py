"""Polling service for central collection of remote storage-viz snapshots."""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Mapping, Protocol

from collector.inventory import Server
from collector import snapshot
from collector.store import CentralStore
from collector.transport import ACTIVE_SCAN_STATES, TransportError, status_tuple, validate_status_envelope

DEFAULT_POLL_INTERVAL_SECONDS = 900
LOCAL_SCAN_CADENCE_SECONDS = 6 * 60 * 60
FRESHNESS_GRACE_SECONDS = 60 * 60
FRESH_SNAPSHOT_SECONDS = LOCAL_SCAN_CADENCE_SECONDS + FRESHNESS_GRACE_SECONDS
SERVER_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
OVERVIEW_ROOT_FIELDS = (
    "mount_id", "capacity_id", "major_minor", "storage_media", "block_media",
    "storage_media_confidence", "block_media_confidence",
)
OVERVIEW_MOUNT_FIELDS = (
    "mount_id", "path", "mountpoint", "df_total", "df_used", "df_avail", "df_use_pct",
    "storage_media", "block_media", "storage_media_confidence", "block_media_confidence",
)


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
    return {
        "server_id": payload.get("server_id"),
        "selected_roots": _overview_rows(payload.get("selected_roots"), OVERVIEW_ROOT_FIELDS),
        "mounts": _overview_rows(payload.get("mounts"), OVERVIEW_MOUNT_FIELDS),
    }


class Clock(Protocol):
    def time(self) -> float: ...


class Transport(Protocol):
    def fetch_status(self, server: Server) -> Mapping[str, Any]: ...
    def fetch_snapshot(self, server: Server, expected_status: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], bytes]: ...
    def scan_active_state(self, server: Server) -> str: ...
    def start_rescan(self, server: Server) -> None: ...


class PollService:
    def __init__(self, servers: list[Server] | tuple[Server, ...], central_store: CentralStore, transport: Transport, *, clock: Clock | None = None, poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self.servers = tuple(s for s in servers if s.enabled)
        self._by_id = {s.id: s for s in self.servers}
        self.store = central_store
        self.transport = transport
        self.clock = clock or time
        self._verified_status_tuples: dict[str, tuple[Any, ...]] = {}
        self._locks = {s.id: threading.RLock() for s in self.servers}
        if not isinstance(poll_interval_seconds, int) or not 600 <= poll_interval_seconds <= 900:
            raise ValueError("poll interval must be between 600 and 900 seconds")
        self.poll_interval_seconds = poll_interval_seconds

    def poll_once(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for server in self.servers:
            results[server.id] = self.poll_server(server.id)
        return results

    def poll_server(self, server_id: str) -> dict[str, Any]:
        server = self._require_server(server_id)
        lock = self._locks[server.id]
        if not lock.acquire(blocking=False):
            return self.store.load_state(server.id)
        try:
            return self._poll_server_locked(server)
        finally:
            lock.release()

    def _poll_server_locked(self, server: Server) -> dict[str, Any]:
        try:
            status = validate_status_envelope(self.transport.fetch_status(server), server)
        except TransportError as exc:
            if exc.code in {"UNREACHABLE", "TIMEOUT"}:
                return self._mark_unreachable(server.id, exc)
            return self._mark_invalid(server.id, exc)
        except Exception as exc:
            return self._mark_invalid(server.id, TransportError("BAD_STATUS", "status envelope is invalid"))

        if status["status"] == "failed":
            self._verified_status_tuples.pop(server.id, None)
            return self._mark_failed_status(server, status)

        current = self.store.load_snapshot(server.id)
        current_generation = f"{current.get('scan_generation')}.json" if current is not None else None
        current_status_tuple = status_tuple(status)
        if current is not None and status["generation"] == current_generation and self._verified_status_tuples.get(server.id) == current_status_tuple:
            return self._mark_unchanged(server, status, current)

        try:
            fetched_status, data = self.transport.fetch_snapshot(server, expected_status=status)
            fetched_status = validate_status_envelope(fetched_status, server)
        except TransportError as exc:
            if exc.code in {"UNREACHABLE", "TIMEOUT"}:
                return self._mark_unreachable(server.id, exc)
            return self._mark_invalid(server.id, exc)
        except Exception:
            return self._mark_invalid(server.id, TransportError("INVALID", "snapshot fetch failed"))

        desired = snapshot.DesiredServer(server_id=server.id, config_digest=server.scanner_digest)
        try:
            snapshot.validate_download(fetched_status, data, desired)
        except Exception as exc:
            return self._mark_invalid(server.id, TransportError("INVALID", str(exc)))

        result = self.store.apply_download(server.id, fetched_status, data, desired)
        if not result.accepted:
            return self._mark_invalid(server.id, TransportError(result.error_code or "WRITE_ERROR", result.message or "download apply failed"))
        self._verified_status_tuples[server.id] = status_tuple(fetched_status)
        self.store.update_state(server.id, freshness=self._freshness(server.id))
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


    def server_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for server in self.servers:
            snap = self.store.load_snapshot(server.id)
            state = self.store.load_state(server.id)
            summaries.append({
                "id": server.id,
                "display_name": server.display_name,
                "order": server.order,
                "snapshot_availability": state["snapshot_availability"],
                "freshness": state["freshness"],
                "latest_pull_status": state["latest_pull_status"],
                "latest_scan_result": state["latest_scan_result"],
                "configuration_sync": state["configuration_sync"],
                "mount_count": len(snap.get("mounts", [])) if isinstance(snap, Mapping) else 0,
                "overview_snapshot": _overview_snapshot(snap),
                "active_job": state.get("active_job"),
            })
        return summaries

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

    def _snapshot(self, server_id: str) -> Mapping[str, Any] | None:
        return self.store.load_snapshot(server_id)

    def _availability(self, server_id: str) -> str:
        return "available" if self._snapshot(server_id) is not None else "absent"

    def _freshness(self, server_id: str) -> str:
        current = self._snapshot(server_id)
        if current is None:
            return "unknown"
        finished = current.get("scan_finished_unix")
        if not isinstance(finished, int) or isinstance(finished, bool):
            return "unknown"
        return "fresh" if int(self.clock.time()) - finished <= FRESH_SNAPSHOT_SECONDS else "stale"

    def _mark_unreachable(self, server_id: str, exc: TransportError) -> dict[str, Any]:
        return self.store.update_state(
            server_id,
            snapshot_availability=self._availability(server_id),
            freshness=self._freshness(server_id),
            latest_pull_status="unreachable",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_invalid(self, server_id: str, exc: TransportError) -> dict[str, Any]:
        self._verified_status_tuples.pop(server_id, None)
        return self.store.update_state(
            server_id,
            snapshot_availability=self._availability(server_id),
            freshness=self._freshness(server_id),
            latest_pull_status="invalid_snapshot",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_failed_status(self, server: Server, status: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.update_state(
            server.id,
            snapshot_availability=self._availability(server.id),
            freshness=self._freshness(server.id),
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
            snapshot_availability=self._availability(server_id),
            freshness=self._freshness(server_id),
            latest_scan_result="failed",
            last_error_code=exc.code,
            last_error_message=str(exc),
            last_error_unix=int(self.clock.time()),
        )

    def _mark_unchanged(self, server: Server, status: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
        return self.store.update_state(
            server.id,
            snapshot_availability="available",
            freshness=self._freshness(server.id),
            latest_pull_status="succeeded",
            latest_scan_result=status["status"],
            configuration_sync=_config_sync(status, server),
            last_error_code=None,
            last_error_message=None,
            last_error_unix=None,
        )


def _config_sync(status: Mapping[str, Any], server: Server) -> str:
    return "in_sync" if status.get("config_digest") == server.scanner_digest else "drifted"
