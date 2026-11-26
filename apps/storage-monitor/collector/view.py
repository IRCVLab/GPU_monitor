"""Immutable per-server views and the v1 responses rendered from them.

``build_view`` is pure. HTTP renders a response from one ``ServerView`` without
a lock; the time-dependent judgments take the reader's clock as an argument.
The v1 renderers are SHIM(M2b..M8) S5: they keep today's response shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, List, Mapping, Optional, Tuple

from storage_fstypes import REMOTE_FSTYPES
from collector import judge
from collector.contract import CAPTURE_SCHEMA, HostManifest, Volume, VolumeCapture, snapshot_response
from collector.health import Data, Link, data_state
from collector.inventory import CapacityThresholds, Server
from collector.jobs import TERMINAL, Job
from collector.live_capacity import CapacitySample, LiveCapacity
from collector.legacy import empty_status

ETA_PLANS = ("full", "ssd")
OVERVIEW_ROOT_FIELDS = (
    "mount_id", "capacity_id", "major_minor", "mount_source", "fstype", "storage_media", "block_media",
    "storage_media_confidence", "block_media_confidence", "scan_finished_unix",
    "cadence_sec", "identity", "diagnostic", "data_state",
)
OVERVIEW_MOUNT_FIELDS = (
    "mount_id", "path", "mountpoint", "df_total", "df_used", "df_avail", "df_use_pct",
    "storage_media", "block_media", "storage_media_confidence", "block_media_confidence",
)
V1_JOB_STATE = {
    "dispatching": "running", "pending": "running", "running": "running", "reconciling": "running",
    "collecting": "running", "succeeded": "succeeded", "failed": "failed", "unconfirmed": "failed",
}


def encode(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class Snapshot:
    """One accepted capture, pre-rendered once: everything but the live-capacity mounts."""
    overview: Mapping[str, Any]
    scan_result: str
    rest: bytes
    """The v1 snapshot response without ``mounts``, as a JSON object."""
    mounts_base: Optional[Mapping[str, Any]]
    """What the live overlay needs, or None when the payload has no mounts."""


@dataclass(frozen=True)
class ObservationView:
    status: Mapping[str, Any]
    """v1 scan-status fields of the last observe; state ``unknown`` with an error code when it failed."""
    received_monotonic: float


@dataclass(frozen=True)
class ServerView:
    server: Server
    link: Link = Link()
    data: Data = Data()
    snapshot: Optional[Snapshot] = None
    job: Optional[Job] = None
    """The active job, else the last one."""
    durations: Mapping[str, Any] = field(default_factory=dict)
    observation: Optional[ObservationView] = None
    host_clock: Optional[Tuple[int, float]] = None
    """(host unix time, central monotonic time of receipt) from the last successful observe."""
    error: Optional[str] = None


def build_view(server: Server, record, snapshot: Optional[Snapshot], observation: Optional[ObservationView],
               host_clock: Optional[Tuple[int, float]], error: Optional[str]) -> ServerView:
    return ServerView(
        server=server, link=record.link, data=record.data, snapshot=snapshot,
        job=record.job or record.last_job, durations=record.durations,
        observation=observation, host_clock=host_clock, error=error,
    )


def render_snapshot(payload: Mapping[str, Any]) -> Snapshot:
    rest = encode(snapshot_response({key: value for key, value in payload.items() if key != "mounts"}))
    base = None
    if "mounts" in payload:
        base = {"mounts": payload["mounts"], "selected_roots": payload.get("selected_roots", []),
                "scan_finished_unix": payload.get("scan_finished_unix")}
    return Snapshot(overview_snapshot(payload), scan_result(payload), rest, base)


def volumes_payload(server_id: str, manifest: HostManifest, captures: Mapping[str, VolumeCapture],
                    states: Mapping[str, str]) -> dict:
    """SHIM(M5a..M8) S5: a v2 host as one v1 snapshot, so today's viewer reads it as a v1 host.

    One v1 mount per volume, with the manifest's live capacity in place of scan-time df (A9).
    Each volume keeps its own ``mount_id`` (the volume id) and scan root, so an ambiguous
    volume is never merged with another. A volume without an accepted capture is a skipped
    root with its ``data_state`` and ``diagnostic``.
    """
    held = [captures[volume.volume_id] for volume in manifest.volumes if volume.volume_id in captures]
    started = min(capture.scan_started_unix for capture in held)
    finished = max(capture.scan_finished_unix for capture in held)
    selected_roots, mounts, top_files, stale = [], [], [], []
    users: dict = {}
    for volume in manifest.volumes:
        capture = captures.get(volume.volume_id)
        state = states.get(volume.volume_id, "none")
        if capture is None:
            selected_roots.append({**_volume_root(volume, state, volume.roots[0].scan_root), "status": "skipped"})
            continue
        path, tree = _volume_tree(volume, capture)
        names = {row["uid"]: row["name"] for root in capture.roots for row in root.users}
        totals = {key: sum(root.counts[key] for root in capture.roots) for key in ("bytes", "files", "dirs", "errors")}
        selected_roots.append({
            **_volume_root(volume, state, path), "status": capture.outcome,
            "scanned_bytes": totals["bytes"], "scanned_files": totals["files"], "scanned_dirs": totals["dirs"],
            "error_count": totals["errors"], "scan_finished_unix": capture.scan_finished_unix,
        })
        mounts.append({
            "path": path, "mount_id": volume.volume_id, "scan_root": path, "fstype": volume.fstype,
            **_capacity(volume),
            "scanned_bytes": totals["bytes"], "scanned_files": totals["files"], "scanned_dirs": totals["dirs"],
            "errors": totals["errors"], "tree": tree, "storage_media": volume.media,
            "storage_media_confidence": volume.media_confidence, "diagnostic": volume.diagnostic, "data_state": state,
        })
        for root in capture.roots:
            for row in root.users:
                user = users.setdefault(row["uid"], {"uid": row["uid"], "name": row["name"], "bytes": 0, "files": 0, "by_mount": {}})
                user["bytes"] += row["bytes"]
                user["files"] += row["files"]
                user["by_mount"][path] = user["by_mount"].get(path, 0) + row["bytes"]
            top_files.extend({**row, "kind": "file", "owner": names.get(row["uid"], str(row["uid"]))} for row in root.top_files)
            stale.extend({**row, "kind": _kind(row["kind"]), "owner": names.get(row["uid"], str(row["uid"]))} for row in root.stale)
    return {
        "schema_version": 1, "hostname": manifest.hostname, "server_id": server_id, "scanner_version": CAPTURE_SCHEMA,
        "scan_started_unix": started, "scan_finished_unix": finished, "scan_duration_sec": finished - started,
        # Changes with every manifest, so the viewer refetches when capacity or a capture changes.
        "scan_generation": f"{server_id}-{manifest.seq}-v2", "run_as_root": False,
        "selected_roots": selected_roots, "mounts": mounts,
        "users": sorted(users.values(), key=lambda user: (-user["bytes"], user["uid"])),
        "top_files": sorted(top_files, key=lambda row: -row["bytes"]), "stale": stale, "blocked": [],
        "config_digest": manifest.config_digest, "mount_table": manifest.mount_table,
    }


def _volume_root(volume: Volume, state: str, path: str) -> dict:
    return {
        "mount_id": volume.volume_id, "major_minor": None, "mount_source": None, "mount_root": None,
        "mountpoint": volume.mountpoint, "scan_root": path, "fstype": volume.fstype,
        "scanned_bytes": 0, "scanned_files": 0, "scanned_dirs": 0, "blocked_count": 0, "error_count": 0,
        "error_code": None, "storage_media": volume.media, "storage_media_confidence": volume.media_confidence,
        "cadence_sec": volume.cadence_sec, "identity": volume.identity, "diagnostic": volume.diagnostic,
        "data_state": state,
    }


def _capacity(volume: Volume) -> dict:
    capacity = volume.capacity
    if capacity is None:
        return {"df_total": None, "df_used": None, "df_avail": None, "df_use_pct": None, "capacity_observed_unix": None}
    return {"df_total": capacity.total, "df_used": capacity.used, "df_avail": capacity.avail, "df_use_pct": None,
            "capacity_observed_unix": capacity.observed_unix}


def _kind(kind: str) -> str:
    return "directory" if kind == "dir" else kind


def _node(node: Mapping[str, Any]) -> dict:
    """A v2 tree node in the v1 spelling: hstscan's ``directory`` for the design's ``dir``."""
    result = {**node, "kind": _kind(node["kind"])}
    if "children" in node:
        result["children"] = [_node(child) for child in node["children"]]
    return result


def _volume_tree(volume: Volume, capture: VolumeCapture) -> Tuple[str, dict]:
    """The volume's one v1 tree: its root's tree, or a node over several roots named by their paths."""
    if len(capture.roots) == 1:
        root = capture.roots[0]
        return root.scan_root, {**_node(root.tree), "name": root.scan_root}
    base = volume.mountpoint.rstrip("/")
    children = []
    for root in capture.roots:
        name = root.scan_root[len(base) + 1:] if root.scan_root.startswith(base + "/") else root.scan_root
        children.append({**_node(root.tree), "name": name})
    return volume.mountpoint, {
        "name": volume.mountpoint, "kind": "directory", "bytes": sum(child["bytes"] for child in children),
        "files": sum(child["files"] for child in children), "uid": 0,
        "mtime": max(child["mtime"] for child in children), "other_bytes": 0, "children": children,
    }


def scan_result(payload: Mapping[str, Any]) -> str:
    statuses = {root.get("status") for root in payload.get("selected_roots", [])
                if isinstance(root, Mapping) and root.get("status") != "skipped"}
    return "complete" if statuses <= {"complete"} else "partial"


def _overview_rows(value: Any, fields: Tuple[str, ...]) -> List[dict]:
    if not isinstance(value, list):
        return []
    return [{name: row.get(name) for name in fields} for row in value if isinstance(row, Mapping)]


def overview_snapshot(payload: Mapping[str, Any]) -> dict:
    mounts = _overview_rows(payload.get("mounts"), OVERVIEW_MOUNT_FIELDS)
    visible = {row.get("mount_id") for row in mounts}
    selected_roots = [row for row in _overview_rows(payload.get("selected_roots"), OVERVIEW_ROOT_FIELDS)
                      if row.get("mount_id") in visible]
    roots = payload.get("selected_roots") or []
    legacy_success = (
        any(root.get("status") == "complete" for root in roots)
        and all(root.get("status") in {"complete", "skipped"} and root.get("scan_finished_unix") is None for root in roots)
    )
    remote = {mount["path"] for mount in payload.get("mounts", []) if mount.get("fstype", "").lower() in REMOTE_FSTYPES}
    users = [
        {"uid": user["uid"], "name": user["name"],
         "bytes": sum(amount for path, amount in user["by_mount"].items() if path not in remote)}
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


def host_now(view: ServerView, now_monotonic: float) -> Optional[int]:
    return judge.host_now(*view.host_clock, now_monotonic) if view.host_clock else None


# ------------------------------------------------------------------ v1 renderers (S5)

def job_v1(job: Optional[Job], server_id: str) -> Optional[dict]:
    if job is None:
        return None
    return {
        "id": job.request_id, "server_id": server_id, "kind": "rescan",
        "state": V1_JOB_STATE[job.state], "phase": job.state, "actor": job.actor,
        "requested_unix": job.requested_unix, "started_unix": job.requested_unix,
        "finished_unix": job.finished_unix, "result_code": job.result_code,
    }


def active_job(view: ServerView) -> Optional[Job]:
    return view.job if view.job is not None and view.job.state not in TERMINAL else None


def latest_pull_status(view: ServerView) -> str:
    if view.link.state in ("unreachable", "auth_failed"):
        return "unreachable"
    if view.link.state in ("protocol_error", "identity_mismatch") or data_state(view.data) == "stale_rejected":
        return "invalid_snapshot"
    if view.link.state == "ok" or view.snapshot is not None:
        return "succeeded"
    return "not_installed"


def latest_scan_result(view: ServerView) -> str:
    manifest = view.data.manifest
    if manifest is not None and manifest.scan_failed():
        return "failed"
    return view.snapshot.scan_result if view.snapshot is not None else "failed"


def configuration_sync(view: ServerView) -> str:
    manifest = view.data.manifest
    if manifest is None:
        return "unknown"
    return "in_sync" if manifest.config_digest == view.server.scanner_digest else "drifted"


def scan_status_v1(view: ServerView, now_monotonic: float) -> dict:
    result = empty_status()
    overview = view.snapshot.overview if view.snapshot is not None else {}
    result.update(last_finished_unix=overview.get("scan_finished_unix"),
                  last_duration_sec=overview.get("scan_duration_sec"),
                  estimate_duration_sec=overview.get("estimate_duration_sec"),
                  snapshot_generation=overview.get("scan_generation"))
    status = view.observation.status if view.observation is not None else None
    if status:
        result.update({key: value for key, value in status.items() if value is not None})
        if status.get("scan_plan") and "estimate_duration_sec" in status:
            result["estimate_duration_sec"] = status["estimate_duration_sec"]
    if result["scan_plan"] in ETA_PLANS:
        result["estimate_duration_sec"], result["estimate_max_duration_sec"] = judge.eta(
            view.durations, result["scan_plan"], result["estimate_duration_sec"])
    result["snapshot_generation"] = overview.get("scan_generation")
    result["snapshot_finished_unix"] = overview.get("scan_finished_unix")
    result["host_now_unix"] = host_now(view, now_monotonic)
    received = view.observation.received_monotonic if view.observation is not None else None
    result["activity"] = judge.activity(result, received, now_monotonic)
    result["disconnected"] = result["activity"] == "unknown" and result["state"] != "unknown"
    return result


def summary_v1(view: ServerView, now_monotonic: float) -> dict:
    overview = view.snapshot.overview if view.snapshot is not None else None
    row = {
        "id": view.server.id,
        "display_name": view.server.display_name,
        "order": view.server.order,
        "status": "error" if view.error else "ok",
        "snapshot_availability": "available" if view.snapshot is not None else "absent",
        "freshness": judge.freshness(overview, host_now(view, now_monotonic), judge.FRESHNESS_SLACK_SECONDS),
        "latest_pull_status": latest_pull_status(view),
        "latest_scan_result": latest_scan_result(view),
        "configuration_sync": configuration_sync(view),
        "mount_count": len(overview.get("mounts", [])) if overview else 0,
        "overview_snapshot": overview,
        "active_job": job_v1(view.job, view.server.id),
        "scan_status": scan_status_v1(view, now_monotonic),
    }
    if view.error:
        row["error"] = {"code": view.error}
    return row


def error_row_v1(server: Server, code: str) -> dict:
    """The row for a server whose view could not be rendered. Other rows are unaffected (C3)."""
    return {**summary_v1(ServerView(server=server, error=code), 0.0), "server_id": server.id}


def snapshot_v1_chunks(snapshot: Snapshot, sample: Optional[CapacitySample], thresholds: CapacityThresholds) -> List[bytes]:
    """The v1 snapshot body: pre-rendered bytes plus mounts judged with the live capacity sample."""
    if snapshot.mounts_base is None:
        return [snapshot.rest]
    projected = LiveCapacity.project_sample(snapshot.mounts_base, sample)
    mounts = snapshot_response({"mounts": projected["mounts"]}, thresholds)["mounts"]
    rest = snapshot.rest[1:]
    return [b'{"mounts":', encode(mounts), b"," if rest != b"}" else b"", rest]
