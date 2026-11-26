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
from collector.errors import StoreUnavailable
from collector.health import LEGACY_VOLUME, LINK_STATES, Data, LegacyPublication, Link
from collector.ids import SERVER_ID_RE, VOLUME_ID_RE
from collector.jobs import Job
from collector.update import Update

_LOG = logging.getLogger(__name__)

RECORD_SCHEMA = "storage-viz.server-record/3"
RECORD_KEYS = frozenset({"schema", "server_id", "manifest", "manifest_sha256", "accepted", "rejected", "link", "job",
                         "last_job", "update", "durations"})
RECORD_V2_SCHEMA = "storage-viz.server-record/2"
"""M2b's record. Read and upgraded, never written (design §2.3)."""
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
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
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise ValueError("capture name is not a sha256")
        # SHIM(M2b..M5c) S3: a v1 snapshot is plain JSON, so it keeps a .json name.
        return self.captures / (f"{sha}.json" if volume_id == LEGACY_VOLUME else f"{sha}.json.gz")

    def _prune(self, record: Record) -> None:
        keep = {self._capture_path(volume_id, sha).name for volume_id, sha in record.data.accepted.items()}
        for path in self.captures.iterdir():
            if path.name not in keep:
                path.unlink(missing_ok=True)

    def import_legacy(self) -> Optional[Tuple[Record, Optional[Tuple[str, str, bytes]]]]:
        """SHIM(M2b..M9) R1, S6: seed a new record from the old store, read only.

        Takes the old store's current snapshot with its verified digest, its last job and
        ``scan-duration-history/<sid>.json``, so the cutover downloads nothing again and the
        ETA keeps its history. Runs only while no record exists, so a crash before the
        record commit just repeats it.
        """
        durations = _old_durations(self.state_root / "scan-duration-history" / f"{self.server_id}.json")
        old = self.state_root / self.server_id
        try:
            manifest = _old_manifest(old)
        except (OSError, ValueError) as exc:
            _LOG.warning("old store for %s not imported: %s", self.server_id, type(exc).__name__)
            manifest = None
        if manifest is None:
            return (Record(durations=durations), None) if durations else None
        capture = None
        data = Data()
        last_job = None
        try:
            if manifest.get("snapshot"):
                capture, data = _old_capture(old / manifest["snapshot"], self.server_id)
            if manifest.get("state"):
                last_job = _old_job(json.loads((old / manifest["state"]).read_bytes()).get("active_job"))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            _LOG.warning("old store for %s not imported: %s", self.server_id, type(exc).__name__)
            return (Record(durations=durations), None) if durations else None
        return Record(data=data, last_job=last_job, durations=durations), capture


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


def _encode_record(record: Record, server_id: str) -> bytes:
    data = record.data
    manifest = data.manifest
    value = {
        "schema": RECORD_SCHEMA,
        "server_id": server_id,
        "manifest": manifest.envelope if isinstance(manifest, LegacyPublication) else (manifest.raw if manifest else None),
        # The sha of the manifest bytes the host wrote; the verbatim object no longer hashes to it.
        "manifest_sha256": manifest.sha256 if isinstance(manifest, contract.HostManifest) else None,
        "accepted": dict(data.accepted),
        "rejected": data.rejected,
        "link": asdict(record.link),
        "job": asdict(record.job) if record.job else None,
        "last_job": asdict(record.last_job) if record.last_job else None,
        "update": asdict(record.update),
        "durations": record.durations,
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _upgrade_v2(raw: Any) -> Any:
    """A /2 record has no manifest_sha256 and its jobs no protocol: it only ever held v1 hosts."""
    if not isinstance(raw, dict) or raw.get("schema") != RECORD_V2_SCHEMA or "manifest_sha256" in raw:
        return raw
    jobs_v3 = {key: {**raw[key], "protocol": "invocation"} if isinstance(raw.get(key), dict) else raw.get(key)
               for key in ("job", "last_job") if key in raw}
    return {**raw, **jobs_v3, "schema": RECORD_SCHEMA, "manifest_sha256": None}


def _parse_record(raw: Any, server_id: str) -> Record:
    raw = _upgrade_v2(raw)
    if not isinstance(raw, dict) or set(raw) != RECORD_KEYS:
        raise ValueError("record keys differ from the schema")
    if raw["schema"] != RECORD_SCHEMA or raw["server_id"] != server_id:
        raise ValueError("record belongs to another schema or server")
    accepted = raw["accepted"]
    if not isinstance(accepted, dict) or not all(
            (key == LEGACY_VOLUME or VOLUME_ID_RE.fullmatch(key)) and isinstance(sha, str) and SHA_RE.fullmatch(sha)
            for key, sha in accepted.items()):
        raise ValueError("record accepted is malformed")
    rejected = raw["rejected"]
    if not isinstance(rejected, dict) or not all(SHA_RE.fullmatch(key) and isinstance(value, dict) for key, value in rejected.items()):
        raise ValueError("record rejected is malformed")
    if raw["manifest"] is not None and not isinstance(raw["manifest"], dict):
        raise ValueError("record manifest is malformed")
    if raw["manifest_sha256"] is not None:
        manifest = contract.parse_manifest_object(raw["manifest"], raw["manifest_sha256"])
    else:
        manifest = LegacyPublication(raw["manifest"]) if raw["manifest"] is not None else None
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


# ------------------------------------------------- SHIM(M2b..M9) old store import (R1, S6)

_OLD_SNAPSHOT_RE = re.compile(r"^snapshot-v2-([0-9a-f]{64})\.json$")
_OLD_STATE_RE = re.compile(r"^state-v2-[0-9a-f]{32}\.json$")


def _old_durations(path: pathlib.Path) -> dict:
    try:
        _reject_symlinks(path.parent, path)
        return _parse_durations(json.loads(path.read_bytes()))
    except (OSError, ValueError):
        return {}


def _old_manifest(old: pathlib.Path) -> Optional[dict]:
    if not old.is_dir() or old.is_symlink():
        return None
    for name in ("current.json", "previous.json"):
        path = old / name
        if not path.exists():
            continue
        try:
            manifest = json.loads(path.read_bytes())
        except ValueError:
            continue
        if not isinstance(manifest, dict) or set(manifest) != {"snapshot", "state"}:
            continue
        if manifest["snapshot"] is not None and not _OLD_SNAPSHOT_RE.fullmatch(manifest["snapshot"]):
            continue
        if manifest["state"] is not None and not _OLD_STATE_RE.fullmatch(manifest["state"]):
            continue
        if all((old / name).is_file() and not (old / name).is_symlink() for name in manifest.values() if name):
            return manifest
    return None


def _old_capture(path: pathlib.Path, server_id: str) -> Tuple[Tuple[str, str, bytes], Data]:
    sha = _OLD_SNAPSHOT_RE.fullmatch(path.name).group(1)
    data = path.read_bytes()
    payload = json.loads(data)
    if payload.get("server_id") != server_id:
        raise ValueError("old snapshot belongs to another server")
    roots = {root.get("status") for root in payload.get("selected_roots", []) if root.get("status") != "skipped"}
    # The old store kept the payload re-encoded, so its bytes no longer hash to the
    # digest the agent published. The digest is what tells the host's publication apart.
    manifest = {
        "generation": f"{payload['scan_generation']}.json",
        "sha256": sha,
        "byte_size": None,
        "server_id": server_id,
        "status": "complete" if roots <= {"complete"} else "partial",
        "scan_finished_unix": payload["scan_finished_unix"],
        "config_digest": payload["config_digest"],
    }
    return (LEGACY_VOLUME, sha, data), Data(manifest=LegacyPublication(manifest), accepted={LEGACY_VOLUME: sha})


def _old_job(active: Any) -> Optional[Job]:
    if not isinstance(active, dict):
        return None
    requested = active["requested_unix"]
    if active["state"] == "running":
        # The old release reconciled a job that a restart interrupted the same way.
        state, finished, code = "failed", requested, "INTERRUPTED"
    else:
        state, finished, code = active["state"], active.get("finished_unix") or requested, active.get("result_code")
    job = Job(
        request_id=str(active["id"]), actor=str(active["actor"]), requested_unix=requested,
        since_unix=finished, state=state, finished_unix=finished, result_code=code,
    )
    return _dataclass(Job, asdict(job), jobs.TERMINAL)
