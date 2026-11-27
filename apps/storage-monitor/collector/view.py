"""Immutable actor publications with host-clock judgments evaluated at read time."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any, Mapping, Optional, Tuple

from collector import judge
from collector.contract import HostManifest
from collector.health import Data, Link
from collector.inventory import Server
from collector.jobs import TERMINAL, Job
from collector.update import Update


def encode(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class ObservationView:
    status: Mapping[str, Any]
    """The most recently observed activity, including its error code if observation failed."""
    received_monotonic: float


@dataclass(frozen=True)
class ServerView:
    server: Server
    summary: bytes
    publication: Optional[Publication] = None
    last_job: Optional[Job] = None
    link: Link = Link()
    data: Data = Data()
    job: Optional[Job] = None
    """The active job, else the last one."""
    durations: Mapping[str, Any] = field(default_factory=dict)
    observation: Optional[ObservationView] = None
    host_clock: Optional[Tuple[int, float]] = None
    """(host unix time, central monotonic time of receipt) from the last successful observe."""
    error: Optional[str] = None
    transport: Optional[str] = None
    """``gate`` or ``legacy`` (S2): who answered the last observe, else the inventory's choice."""
    update: Update = Update()
    release_sha: Optional[str] = None
    """The agent release the host runs: the gate's report, else the accepted manifest's."""


def build_view(server: Server, record, pub: Optional[Publication], observation: Optional[ObservationView],
               host_clock: Optional[Tuple[int, float]], error: Optional[str], transport: Optional[str] = None,
               release_sha: Optional[str] = None) -> ServerView:
    manifest = record.data.manifest
    if release_sha is None and isinstance(manifest, HostManifest):
        release_sha = manifest.raw["agent"]["release_sha"]
    summary = encode({"server_id": server.id, "display_name": server.display_name, "order": server.order,
                      "status": "ok", "link": {"state": record.link.state, "last_ok_unix": record.link.last_ok_unix,
                      "error": record.link.error, "transport": transport or server.transport},
                      "agent": {"release_sha": release_sha, "matches_desired": bool(record.update.desired and
                               release_sha == record.update.desired), "update": record.update.state, "last_error": record.update.last_error},
                      "revision": (pub or EMPTY_PUBLICATION).revision})[:-1]
    return ServerView(
        server=server, summary=summary, publication=pub, last_job=record.last_job, link=record.link, data=record.data,
        job=record.job or record.last_job, durations=record.durations,
        observation=observation, host_clock=host_clock, error=error, transport=transport,
        update=record.update, release_sha=release_sha,
    )


def host_now(view: ServerView, now_monotonic: float) -> Optional[int]:
    return judge.host_now(*view.host_clock, now_monotonic) if view.host_clock else None


def active_job(view: ServerView) -> Optional[Job]:
    return view.job if view.job is not None and view.job.state not in TERMINAL else None


@dataclass(frozen=True)
class Publication:
    revision: str
    users_top: bytes
    detail: bytes
    captures: Mapping[str, dict]
    manifest: Optional[HostManifest]
    legacy_bytes: Optional[bytes]
    volumes: tuple[bytes, ...]


def publication(manifest, held, legacy_bytes=None, *, states=None, problems=None):
    """Merge the accepted capture rows once, before publishing the actor's view."""
    users, top, stale, heads = {}, [], [], {}
    for vid, (sha, capture) in held.items():
        heads[vid] = {"sha256": sha, "outcome": capture.outcome, "scan_finished_unix": capture.scan_finished_unix}
        for root in capture.roots:
            for row in root.users:
                user = users.setdefault(row["uid"], {"uid": row["uid"], "name": row["name"], "bytes": 0,
                                                      "files": 0, "by_volume": {}})
                subtotal = user["by_volume"].setdefault(vid, {"bytes": 0, "files": 0})
                for key in ("bytes", "files"):
                    subtotal[key] += row[key]
                    user[key] += row[key]
            for rows, dest in ((root.top_files, top), (root.stale, stale)):
                for row in rows:
                    dest.append({**row, "volume_id": vid, "binding": root.binding,
                                 "age_days": judge.age_days(row["mtime"], capture.scan_started_unix)})
    ordered = sorted(users.values(), key=lambda row: (-row["bytes"], row["uid"]))
    revision = f"{manifest.seq if manifest else 0}:" + hashlib.sha256(encode(heads)).hexdigest()[:12]
    host = {"hostname": manifest.hostname if manifest else None, "mount_table": manifest.mount_table if manifest else None}
    volumes = []
    if manifest:
        for vol, raw in zip(manifest.volumes, manifest.raw["volumes"]):
            vid = vol.volume_id
            state_id = "legacy" if legacy_bytes else vid
            attempt = raw["last_attempt"]
            volumes.append(encode({"volume_id": vid, "identity": vol.identity, "diagnostic": vol.diagnostic,
                                   "mountpoint": vol.mountpoint, "media": vol.media, "roots": raw["roots"],
                                   "data_state": (states or {}).get(state_id, "current" if vid in heads else "none"),
                                   "data_error": (problems or {}).get(state_id), "capture": heads.get(vid),
                                   "last_attempt": {k:attempt[k] for k in ("finished_unix", "outcome", "error_code")}
                                   if attempt else None})[:-1])
    return Publication(revision, encode([{k: r[k] for k in ("uid","name","bytes")} for r in ordered]),
                       encode({"users": ordered, "top_files": sorted(top, key=lambda r: -r["bytes"]),
                               "stale": sorted(stale, key=lambda r: -r["bytes"]), "host": host})[1:], heads, manifest, legacy_bytes, tuple(volumes))


EMPTY_PUBLICATION = publication(None, {})


def job_body(job):
    if job is None:
        return None
    return {key: getattr(job, key) for key in ("request_id", "state", "actor", "requested_unix", "finished_unix", "result_code")}


def activity_body(view, mono):
    obs = view.observation
    status = obs.status if obs else {}
    typical, longest = judge.eta(view.durations, status.get("scan_plan") or "full", status.get("estimate_duration_sec"))
    return {"activity": {"state": judge.activity(status, obs.received_monotonic if obs else None, mono),
                         "run_id": status.get("run_id"), "trigger": status.get("trigger"),
                         "started_unix": status.get("started_unix"), "observed_unix": status.get("observed_unix"),
                         "host_now_unix": host_now(view, mono),
                         "progress": {k: status.get(s) for k,s in (("group","group"),("roots_completed","roots_completed"),
                                      ("roots_total","roots_total"),("bytes","scanned_bytes"),("files","scanned_files"))}},
            "eta": {"typical_sec": typical, "longest_sec": longest}, "job": job_body(active_job(view)),
            "last_job": job_body(view.last_job)}


def server_bytes(view, mono, thresholds, *, detail=False):
    if view.error:
        return encode({"server_id": view.server.id, "status": "error", "error": {"code": view.error}})
    pub = view.publication or EMPTY_PUBLICATION
    manifest = pub.manifest
    now = host_now(view, mono)
    volumes = []
    freshness = []
    if manifest:
        for vol, prefix in zip(manifest.volumes, pub.volumes):
            head = pub.captures.get(vol.volume_id)
            capacity, severity = judge.volume_capacity(vol.capacity, now, thresholds)
            fresh = judge.volume_freshness(head and head["scan_finished_unix"], vol.cadence_sec, now)
            freshness.append(fresh)
            volumes.append(prefix + b"," + encode({"freshness": fresh, "capacity": capacity, "severity": severity})[1:])
    activity = activity_body(view, mono)
    dynamic = {"activity": activity["activity"], "eta": activity["eta"], "job": activity["job"],
               "freshness": judge.worst_freshness(*freshness) if freshness else "unknown"}
    fields = [view.summary, encode(dynamic)[1:-1], b'"volumes":[' + b",".join(volumes) + b"]",
              b'"users_top":' + pub.users_top]
    if detail:
        fields.append(pub.detail)
    return b",".join(fields) + (b"" if detail else b"}")
