"""Per-server central store: ``STATE_DIR/servers/<sid>/record.json`` plus ``captures/``.

Only the server's own actor calls ``commit``, so the store takes no lock.
A capture is written before the record that names it, and both are atomic,
so a crash leaves either the old record or the new one with its capture.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import logging
import os
import pathlib
import re
import tempfile
import types
import typing
from typing import Any, Collection, Mapping, Optional, Tuple

from collector import contract, jobs, update
from collector.errors import CaptureGone, StoreUnavailable
from collector.health import LINK_STATES, Data, Link
from collector.ids import SERVER_ID_RE, VOLUME_ID_RE, SHA256_RE
from collector.jobs import Job
from collector.update import Update

_LOG = logging.getLogger(__name__)

RECORD_SCHEMA = "storage-viz.server-record/3"
RECORD_KEYS = frozenset({"schema", "server_id", "manifest", "manifest_sha256", "accepted", "rejected", "link", "job",
                         "last_job", "update", "durations"})
DURATION_PLANS = ("full", "ssd")
DURATION_LIMIT = 5


@dataclass(frozen=True)
class Record:
    link: Link = Link()
    data: Data = Data()
    job: Optional[Job] = None
    last_job: Optional[Job] = None
    update: Update = Update()
    durations: Mapping[str, Any] = field(default_factory=dict)
    """Recent completed scan durations per plan: {"full": [[finished_unix, seconds], ...]}."""


class ServerStore:
    def __init__(self, state_root: str | os.PathLike, server_id: str) -> None:
        if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id) or server_id in {".", ".."}:
            raise ValueError("server id is unsafe")
        self.state_root = pathlib.Path(state_root)
        self.server_id = server_id
        self.directory = self.state_root / "servers" / server_id
        self.captures = self.directory / "captures"
        self.record_path = self.directory / "record.json"

    def load(self) -> Optional[Record]:
        """The committed record, or None when the server has none yet."""
        try:
            _reject_symlinks(self.state_root / "servers", self.directory, self.record_path)
            if not self.record_path.exists():
                return None
            raw = json.loads(self.record_path.read_bytes())
            return _parse_record(raw, self.server_id)
        except StoreUnavailable:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise StoreUnavailable(f"record unreadable: {type(exc).__name__}") from exc

    def read_capture(self, volume_id: str, sha: str) -> bytes:
        path = self._capture_path(volume_id, sha)
        try:
            _reject_symlinks(path)
            return path.read_bytes()
        except OSError as exc:
            raise StoreUnavailable("capture unreadable") from exc

    def open_capture(self, volume_id: str, sha: str):
        path = self._capture_path(volume_id, sha)
        try:
            _reject_symlinks(self.state_root / "servers", self.directory, self.captures, path)
            return path.open("rb")
        except FileNotFoundError as exc:
            # Pruning follows the durable commit. An HTTP reader may still hold the previous view.
            record = self.load()
            if record and record.data.accepted.get(volume_id) != sha:
                raise CaptureGone() from exc
            raise StoreUnavailable("accepted capture is missing") from exc
        except (OSError, ValueError) as exc:
            raise StoreUnavailable("capture unreadable") from exc

    def commit(self, record: Record, capture: Optional[Tuple[str, str, bytes]] = None, *, prune: bool = True) -> None:
        """Write the capture if any, then the record; with ``prune``, then delete captures the record does not name.
        ``capture`` is (volume_id, sha256, bytes exactly as fetched)."""
        try:
            for directory in (self.state_root / "servers", self.directory, self.captures):
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                _reject_symlinks(directory)
            if capture is not None:
                _write_atomic(self._capture_path(capture[0], capture[1]), capture[2])
            _write_atomic(self.record_path, _encode_record(record, self.server_id))
            if prune:
                self._prune(record)
        except (OSError, ValueError) as exc:
            raise StoreUnavailable(f"record not writable: {type(exc).__name__}") from exc

    def _capture_path(self, volume_id: str, sha: str) -> pathlib.Path:
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
            raise ValueError("capture name is not a sha256")
        return self.captures / f"{sha}.json.gz"

    def _prune(self, record: Record) -> None:
        keep = {self._capture_path(volume_id, sha).name for volume_id, sha in record.data.accepted.items()}
        for path in self.captures.iterdir():
            if path.name not in keep:
                path.unlink(missing_ok=True)


def _reject_symlinks(*paths: pathlib.Path) -> None:
    for path in paths:
        if path.is_symlink():
            raise ValueError("store path must not be a symlink")


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _encode_job(job: Optional[Job]):
    # Keep /3 records readable if the dashboard rolls back to the first receipt-based release.
    if job is None:
        return None
    return dict(asdict(job), protocol="receipt", baseline_run=None, invocation=None, agent_run=None, generation=None)


def _encode_record(record: Record, server_id: str) -> bytes:
    data = record.data
    manifest = data.manifest
    value = {
        "schema": RECORD_SCHEMA,
        "server_id": server_id,
        "manifest": manifest.raw if manifest else None,
        # The sha of the manifest bytes the host wrote; the verbatim object no longer hashes to it.
        "manifest_sha256": manifest.sha256 if isinstance(manifest, contract.HostManifest) else None,
        "accepted": dict(data.accepted),
        "rejected": data.rejected,
        "link": asdict(record.link),
        "job": _encode_job(record.job),
        "last_job": _encode_job(record.last_job),
        "update": asdict(record.update),
        "durations": record.durations,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


UPDATE_FIELDS_SINCE_M3B = {"requested_host_unix": None, "requests": 0, "probation_manifest": None, "trial": None,
                           "verified": None, "seen": None}


def _upgrade_update(raw: Any) -> Any:
    """Records before M3b have none of the update-request fields: no update request was ever sent."""
    if isinstance(raw, dict) and isinstance(raw.get("update"), dict):
        return {**raw, "update": {**UPDATE_FIELDS_SINCE_M3B, **raw["update"]}}
    return raw


def _parse_record(raw: Any, server_id: str) -> Record:
    raw = _upgrade_update(raw)
    if not isinstance(raw, dict) or set(raw) != RECORD_KEYS:
        raise ValueError("record keys differ from the schema")
    if raw["schema"] != RECORD_SCHEMA or raw["server_id"] != server_id:
        raise ValueError("record belongs to another schema or server")
    accepted = raw["accepted"]
    if not isinstance(accepted, dict) or not all(
            VOLUME_ID_RE.fullmatch(key) and isinstance(sha, str) and SHA256_RE.fullmatch(sha)
            for key, sha in accepted.items()):
        raise ValueError("record accepted is malformed")
    rejected = raw["rejected"]
    if not isinstance(rejected, dict) or not all(SHA256_RE.fullmatch(key) and isinstance(value, dict) for key, value in rejected.items()):
        raise ValueError("record rejected is malformed")
    if raw["manifest"] is not None and not isinstance(raw["manifest"], dict):
        raise ValueError("record manifest is malformed")
    if raw["manifest_sha256"] is not None:
        manifest = contract.parse_manifest_object(raw["manifest"], raw["manifest_sha256"])
    else:
        if raw["manifest"] is not None:
            raise ValueError("missing manifest digest")
        manifest = None
    active_states = frozenset(jobs.STATES) - jobs.TERMINAL
    return Record(
        link=_dataclass(Link, raw["link"], LINK_STATES),
        data=Data(manifest=manifest, accepted=accepted, rejected=rejected),
        job=_dataclass(Job, raw["job"], active_states) if raw["job"] is not None else None,
        last_job=_dataclass(Job, raw["last_job"], jobs.TERMINAL) if raw["last_job"] is not None else None,
        update=_dataclass(Update, raw["update"], update.STATES),
        durations=_parse_durations(raw["durations"]),
    )


def _dataclass(kind, value: Any, states: Collection[str]):
    """Build ``kind`` from a JSON object whose keys, field types and state match its definition."""
    if kind is Job and isinstance(value, dict) and "protocol" in value:
        # Installed /3 records include the former correlation fields even for receipt jobs.
        if value["protocol"] != "receipt" and value.get("state") not in jobs.TERMINAL:
            raise ValueError("unsupported active job protocol")
        value = {k: v for k, v in value.items() if k not in
                 {"protocol", "baseline_run", "invocation", "agent_run", "generation"}}
    hints = typing.get_type_hints(kind)
    if not isinstance(value, dict) or set(value) != set(hints):
        raise ValueError(f"record {kind.__name__} is malformed")
    for name, hint in hints.items():
        if not _has_type(value[name], hint):
            raise ValueError(f"record {kind.__name__}.{name} is malformed")
    if value["state"] not in states:
        raise ValueError(f"record {kind.__name__}.state is not allowed here")
    return kind(**value)


def _has_type(value: Any, hint: Any) -> bool:
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        return any(_has_type(value, arg) for arg in typing.get_args(hint))
    if hint in (int, str, type(None)):
        return type(value) is hint
    raise TypeError(f"record field type {hint} has no check")


def _parse_durations(value: Any) -> dict:
    """The shipped ETA history rule: at most 5 [finished_unix, seconds] pairs per plan, oldest first."""
    if not isinstance(value, dict) or not set(value) <= set(DURATION_PLANS):
        raise ValueError("durations are malformed")
    result = {}
    for plan, entries in value.items():
        if not isinstance(entries, list) or len(entries) > DURATION_LIMIT:
            raise ValueError("durations are malformed")
        previous = 0
        for entry in entries:
            if not (isinstance(entry, list) and len(entry) == 2 and all(type(x) is int for x in entry)
                    and entry[0] > previous and entry[1] >= 0):
                raise ValueError("durations are malformed")
            previous = entry[0]
        result[plan] = [list(entry) for entry in entries]
    return result
