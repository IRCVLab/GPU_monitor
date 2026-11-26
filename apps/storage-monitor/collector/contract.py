"""The parse boundary for v2 host data (design §2.2), plus the v1 response judgments.

``parse_manifest`` and ``parse_capture`` turn host bytes into frozen domain values or
raise ``ContractError`` with one reason from ``REASONS``. Nothing past them re-checks
shapes (I21). The v1 snapshot keeps its own validator in ``collector/snapshot.py`` (S3).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import zlib
from typing import Any, Callable, Dict, FrozenSet, Iterable, Mapping, Optional, Pattern, Tuple

from collector import judge
from collector.ids import BINDING_RE, RELEASE_SHA_RE, REQUEST_ID_RE, RUN_ID_RE, SERVER_ID_RE, SHA256_RE, VOLUME_ID_RE
from collector.inventory import CapacityThresholds

MANIFEST_SCHEMA = "storage-viz.manifest/2"
CAPTURE_SCHEMA = "storage-viz.capture/2"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 512 * 1024 * 1024
"""Transfer cap for one gzip capture."""
MAX_CAPTURE_JSON_BYTES = 512 * 1024 * 1024
"""Decompressed cap, the same bound as a v1 snapshot."""
MAX_TREE_DEPTH = 128
"""The agent folds anything deeper into ``other_bytes`` (A10), so a deeper tree is a broken agent."""
MAX_TREE_NODES = 1_000_000
MAX_CHILDREN_PER_NODE = 16_384
MAX_ROWS = 100_000
MAX_STRING_BUDGET = 64 * 1024 * 1024
MAX_VOLUMES = 256
MAX_ROOTS_PER_VOLUME = 64
MAX_RECEIPTS = 16
MAX_COUNTER = 10**18
MAX_TIME = 4_102_444_800
MAX_CADENCE_SECONDS = 30 * 86400

MANIFEST_TOO_LARGE = "MANIFEST_TOO_LARGE"
CAPTURE_TOO_LARGE = "CAPTURE_TOO_LARGE"
MALFORMED = "MALFORMED"
"""Not gzip, not JSON, or not a JSON object."""
BAD_SCHEMA = "BAD_SCHEMA"
BAD_FIELD = "BAD_FIELD"
"""A missing, unknown or wrongly typed field."""
BAD_ID = "BAD_ID"
BAD_SEQ = "BAD_SEQ"
DUPLICATE_ID = "DUPLICATE_ID"
SHA_MISMATCH = "SHA_MISMATCH"
VOLUME_MISMATCH = "VOLUME_MISMATCH"
BINDING_MISMATCH = "BINDING_MISMATCH"
OUTCOME_MISMATCH = "OUTCOME_MISMATCH"
TREE_SUM_MISMATCH = "TREE_SUM_MISMATCH"
TREE_TOO_DEEP = "TREE_TOO_DEEP"
REASONS = frozenset({
    MANIFEST_TOO_LARGE, CAPTURE_TOO_LARGE, MALFORMED, BAD_SCHEMA, BAD_FIELD, BAD_ID, BAD_SEQ, DUPLICATE_ID,
    SHA_MISMATCH, VOLUME_MISMATCH, BINDING_MISMATCH, OUTCOME_MISMATCH, TREE_SUM_MISMATCH, TREE_TOO_DEEP,
})

IDENTITIES = frozenset({"uuid", "alias", "ambiguous"})
MEDIA = frozenset({"ssd", "hdd", "mixed", "unknown"})
MEDIA_CONFIDENCE = frozenset({"resolved", "unresolved"})
CAPTURE_OUTCOMES = frozenset({"complete", "partial"})
RUN_OUTCOMES = frozenset({"complete", "partial", "failed", "nothing_due", "interrupted"})
CAPACITY_STATUSES = frozenset({"ok", "failed"})
MOUNT_TABLE_STATUSES = frozenset({"ok", "unreadable"})
KINDS = frozenset({"dir", "directory", "file", "symlink", "other"})
"""The design spells a directory ``dir``; hstscan writes ``directory``. Both are accepted."""
ERROR_REASONS = frozenset({"EACCES", "VANISHED", "REPLACED", "FD_BUDGET_REOPEN_FAILED", "UNREPRESENTABLE"})


class ContractError(ValueError):
    def __init__(self, reason: str, message: str = "") -> None:
        assert reason in REASONS, reason
        super().__init__(message or reason)
        self.reason = reason


# ------------------------------------------------------------------ domain values


@dataclass(frozen=True)
class RootRef:
    scan_root: str
    binding: str
    prune_class: str


@dataclass(frozen=True)
class CaptureRef:
    sha256: str
    bytes: int
    scan_started_unix: int
    scan_finished_unix: int
    outcome: str


@dataclass(frozen=True)
class Capacity:
    total: int
    used: int
    avail: int
    observed_unix: int
    status: str


@dataclass(frozen=True)
class Expected:
    """What a fetched capture must be: the manifest's sha and the volume's current roots."""
    volume_id: str
    sha256: str
    roots: FrozenSet[Tuple[str, str]]
    """(scan_root, binding) pairs."""


@dataclass(frozen=True)
class VolumeAttempt:
    """A volume's last scan attempt: the update track reads its error code for agent faults."""
    run_id: str
    outcome: str
    error_code: Optional[str]


@dataclass(frozen=True)
class Volume:
    volume_id: str
    identity: str
    diagnostic: Optional[str]
    mountpoint: str
    fstype: str
    media: str
    media_confidence: str
    group: str
    cadence_sec: int
    roots: Tuple[RootRef, ...]
    capacity: Optional[Capacity]
    capture: Optional[CaptureRef]
    last_attempt: Optional[VolumeAttempt] = None

    def expected(self, sha256: str) -> Expected:
        return Expected(self.volume_id, sha256, frozenset((root.scan_root, root.binding) for root in self.roots))


@dataclass(frozen=True)
class Receipt:
    request_id: str
    run_id: Optional[str]
    received_unix: int
    outcome: str
    """Kept as the host wrote it: the job reducer ignores outcomes it does not know."""


@dataclass(frozen=True)
class LastRun:
    run_id: str
    trigger: str
    plan: str
    started_unix: int
    finished_unix: int
    outcome: str
    agent_sha: Optional[str] = None
    """The release that ran it."""


@dataclass(frozen=True)
class HostManifest:
    sha256: str
    """sha256 of the bytes the host wrote. The record keeps it beside ``raw``."""
    raw: Mapping[str, Any]
    """The manifest object verbatim, as the record stores it."""
    server_id: str
    hostname: str
    seq: int
    written_unix: int
    config_digest: Optional[str]
    volumes: Tuple[Volume, ...]
    mount_table: Mapping[str, Any]
    last_run: Optional[LastRun]
    requests: Tuple[Receipt, ...]

    def targets(self) -> Dict[str, Optional[str]]:
        """Every volume the manifest names, with the capture sha it publishes or None."""
        return {volume.volume_id: volume.capture.sha256 if volume.capture else None for volume in self.volumes}

    def volume(self, volume_id: str) -> Optional[Volume]:
        return next((volume for volume in self.volumes if volume.volume_id == volume_id), None)

    def scan_failed(self) -> bool:
        return self.last_run is not None and self.last_run.outcome in ("failed", "interrupted")


@dataclass(frozen=True)
class CaptureRoot:
    scan_root: str
    binding: str
    prune_class: str
    outcome: str
    counts: Mapping[str, int]
    tree: Mapping[str, Any]
    users: Tuple[Mapping[str, Any], ...]
    top_files: Tuple[Mapping[str, Any], ...]
    stale: Tuple[Mapping[str, Any], ...]
    errors: Tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class VolumeCapture:
    volume_id: str
    run_id: str
    group: str
    scan_started_unix: int
    scan_finished_unix: int
    outcome: str
    roots: Tuple[CaptureRoot, ...]


@dataclass(frozen=True)
class ManifestPointer:
    """What ``observe`` reports about manifest.json without sending it."""
    sha256: str
    seq: Optional[int]


@dataclass(frozen=True)
class HostRun:
    """v2 scan facts from ``observe``: run.json while a run is unfinished, and the request mailbox."""
    present: bool = False
    """run.json exists: a run holds the lock, or one was interrupted."""
    scanning: bool = False
    """A run is in progress now."""
    run_id: Optional[str] = None
    request_ids: FrozenSet[str] = frozenset()
    pending: FrozenSet[str] = frozenset()
    """Request ids waiting in ``requests/``."""


# ------------------------------------------------------------------ manifest

_MANIFEST_KEYS = ("schema", "server_id", "hostname", "seq", "written_unix", "agent", "volumes", "mount_table",
                  "last_run", "requests")
_VOLUME_KEYS = ("volume_id", "identity", "diagnostic", "mountpoint", "fstype", "media", "media_confidence", "group",
                "cadence_sec", "roots", "capacity", "capture", "last_attempt")


def _typed(parse: Callable[..., Any]) -> Callable[..., Any]:
    """A parser raises ContractError and nothing else on host data. A lookup or type slip in a
    field reader is a malformed field, never an exception that escapes the boundary."""
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return parse(*args, **kwargs)
        except ContractError:
            raise
        except (TypeError, KeyError, IndexError, AttributeError, ValueError) as exc:
            raise ContractError(BAD_FIELD, f"{type(exc).__name__} while parsing") from exc
    guarded.__name__, guarded.__doc__ = parse.__name__, parse.__doc__
    return guarded


@_typed
def parse_manifest(data: bytes) -> HostManifest:
    if not isinstance(data, (bytes, bytearray)):
        raise ContractError(MALFORMED, "manifest is not bytes")
    if len(data) > MAX_MANIFEST_BYTES:
        raise ContractError(MANIFEST_TOO_LARGE)
    return parse_manifest_object(decode_json(bytes(data)), hashlib.sha256(data).hexdigest())


@_typed
def parse_manifest_object(raw: Any, sha256: str) -> HostManifest:
    """Parse a manifest object whose bytes hashed to ``sha256``. The store calls this on load."""
    if not isinstance(raw, dict):
        raise ContractError(MALFORMED, "manifest is not an object")
    if raw.get("schema") != MANIFEST_SCHEMA:
        raise ContractError(BAD_SCHEMA)
    # An exact key set: a manifest with a phase field (I12) or any other unknown key is refused.
    _object(raw, _MANIFEST_KEYS, "manifest")
    if type(raw["seq"]) is not int or not 0 <= raw["seq"] < MAX_COUNTER:
        raise ContractError(BAD_SEQ)
    agent = _object(raw["agent"], ("release_sha", "release_seq", "config_digest"), "agent")
    _optional(agent["release_sha"], lambda value: _id(value, RELEASE_SHA_RE, "agent.release_sha"))
    _optional(agent["release_seq"], lambda value: _int(value, "agent.release_seq"))
    volumes = tuple(_volume(value) for value in _list(raw["volumes"], "volumes", MAX_VOLUMES))
    _unique((volume.volume_id for volume in volumes), "volume_id")
    _unique((root.scan_root for volume in volumes for root in volume.roots), "scan_root")
    _unique((root.binding for volume in volumes for root in volume.roots), "binding")
    return HostManifest(
        sha256=_id(sha256, SHA256_RE, "manifest sha256"),
        raw=raw,
        server_id=_id(raw["server_id"], SERVER_ID_RE, "server_id"),
        hostname=_text(raw["hostname"], "hostname", 255),
        seq=raw["seq"],
        written_unix=_time(raw["written_unix"], "written_unix"),
        config_digest=_optional(agent["config_digest"], lambda value: _id(value, SHA256_RE, "agent.config_digest")),
        volumes=volumes,
        mount_table=_mount_table(raw["mount_table"]),
        last_run=_optional(raw["last_run"], _last_run),
        requests=tuple(_receipt(value) for value in _list(raw["requests"], "requests", MAX_RECEIPTS)),
    )


def _volume(value: Any) -> Volume:
    raw = _object(value, _VOLUME_KEYS, "volume")
    volume_id = _id(raw["volume_id"], VOLUME_ID_RE, "volume_id")
    identity = _choice(raw["identity"], IDENTITIES, "identity")
    if (identity == "ambiguous") != volume_id.startswith("x-"):
        raise ContractError(BAD_ID, "only an ambiguous identity has an x- id")
    roots = tuple(_root_ref(root) for root in _list(raw["roots"], "roots", MAX_ROOTS_PER_VOLUME))
    if not roots:
        raise ContractError(BAD_FIELD, "a volume has no roots")
    last_attempt = None
    if raw["last_attempt"] is not None:
        attempt = _object(raw["last_attempt"], ("run_id", "started_unix", "finished_unix", "outcome", "error_code"), "last_attempt")
        _time(attempt["started_unix"], "last_attempt.started_unix")
        _optional(attempt["finished_unix"], lambda item: _time(item, "last_attempt.finished_unix"))
        last_attempt = VolumeAttempt(
            _id(attempt["run_id"], RUN_ID_RE, "last_attempt.run_id"),
            _choice(attempt["outcome"], RUN_OUTCOMES, "last_attempt.outcome"),
            _optional(attempt["error_code"], lambda item: _code(item, "last_attempt.error_code")))
    return Volume(
        volume_id=volume_id,
        identity=identity,
        diagnostic=_optional(raw["diagnostic"], lambda item: _code(item, "diagnostic")),
        mountpoint=_path(raw["mountpoint"], "mountpoint"),
        fstype=_text(raw["fstype"], "fstype", 64),
        media=_choice(raw["media"], MEDIA, "media"),
        media_confidence=_choice(raw["media_confidence"], MEDIA_CONFIDENCE, "media_confidence"),
        group=_text(raw["group"], "group", 64),
        cadence_sec=_int(raw["cadence_sec"], "cadence_sec", 1, MAX_CADENCE_SECONDS),
        roots=roots,
        capacity=_optional(raw["capacity"], _capacity),
        capture=_optional(raw["capture"], _capture_ref),
        last_attempt=last_attempt,
    )


def _root_ref(value: Any) -> RootRef:
    raw = _object(value, ("scan_root", "binding", "prune_class"), "root")
    return RootRef(_path(raw["scan_root"], "scan_root"), _id(raw["binding"], BINDING_RE, "binding"),
                   _text(raw["prune_class"], "prune_class", 64))


def _capacity(value: Any) -> Capacity:
    raw = _object(value, ("total", "used", "avail", "observed_unix", "status"), "capacity")
    return Capacity(_int(raw["total"], "capacity.total"), _int(raw["used"], "capacity.used"),
                    _int(raw["avail"], "capacity.avail"), _time(raw["observed_unix"], "capacity.observed_unix"),
                    _choice(raw["status"], CAPACITY_STATUSES, "capacity.status"))


def _capture_ref(value: Any) -> CaptureRef:
    raw = _object(value, ("sha256", "bytes", "scan_started_unix", "scan_finished_unix", "outcome"), "capture")
    started = _time(raw["scan_started_unix"], "capture.scan_started_unix")
    finished = _time(raw["scan_finished_unix"], "capture.scan_finished_unix")
    if finished < started:
        raise ContractError(BAD_FIELD, "capture finished before it started")
    return CaptureRef(_id(raw["sha256"], SHA256_RE, "capture.sha256"), _int(raw["bytes"], "capture.bytes", 1),
                      started, finished, _choice(raw["outcome"], CAPTURE_OUTCOMES, "capture.outcome"))


def _mount_table(value: Any) -> Mapping[str, Any]:
    raw = _object(value, ("status", "observed_unix", "within_roots"), "mount_table")
    _choice(raw["status"], MOUNT_TABLE_STATUSES, "mount_table.status")
    _time(raw["observed_unix"], "mount_table.observed_unix")
    for row in _list(raw["within_roots"], "mount_table.within_roots", MAX_ROWS):
        entry = _object(row, ("mountpoint", "same_device"), "mount_table row")
        _path(entry["mountpoint"], "mount_table.mountpoint")
        if type(entry["same_device"]) is not bool:
            raise ContractError(BAD_FIELD, "same_device is not a boolean")
    return raw


def _last_run(value: Any) -> LastRun:
    raw = _object(value, ("run_id", "trigger", "plan", "started_unix", "finished_unix", "outcome", "agent_sha"), "last_run")
    return LastRun(_id(raw["run_id"], RUN_ID_RE, "last_run.run_id"), _text(raw["trigger"], "trigger", 32),
                   _text(raw["plan"], "plan", 32), _time(raw["started_unix"], "last_run.started_unix"),
                   _time(raw["finished_unix"], "last_run.finished_unix"),
                   _choice(raw["outcome"], RUN_OUTCOMES, "last_run.outcome"),
                   _optional(raw["agent_sha"], lambda item: _id(item, RELEASE_SHA_RE, "last_run.agent_sha")))


def _receipt(value: Any) -> Receipt:
    raw = _object(value, ("request_id", "run_id", "received_unix", "outcome"), "receipt")
    return Receipt(_id(raw["request_id"], REQUEST_ID_RE, "request_id"),
                   _optional(raw["run_id"], lambda item: _id(item, RUN_ID_RE, "receipt.run_id")),
                   _time(raw["received_unix"], "receipt.received_unix"), _text(raw["outcome"], "receipt.outcome", 32))


# ------------------------------------------------------------------ capture

_CAPTURE_KEYS = ("schema", "volume_id", "run_id", "group", "scan_started_unix", "scan_finished_unix", "outcome", "roots")
_ROOT_KEYS = ("scan_root", "binding", "prune_class", "outcome", "counts", "tree", "users", "top_files", "stale", "errors")
_COUNT_KEYS = ("bytes", "files", "dirs", "errors", "vanished_dirs")
_NODE_KEYS = ("name", "kind", "bytes", "files", "uid", "mtime")
_NODE_OPTIONAL = ("name_b64", "other_bytes", "children")


@_typed
def parse_capture(data: bytes, expected: Expected, *, max_json_bytes: int = MAX_CAPTURE_JSON_BYTES) -> VolumeCapture:
    """Check the gzip bytes against the manifest's sha, then the capture against the volume."""
    if not isinstance(data, (bytes, bytearray)):
        raise ContractError(MALFORMED, "capture is not bytes")
    if len(data) > MAX_CAPTURE_BYTES:
        raise ContractError(CAPTURE_TOO_LARGE)
    if hashlib.sha256(data).hexdigest() != expected.sha256:
        raise ContractError(SHA_MISMATCH)
    raw = decode_json(_gunzip(bytes(data), max_json_bytes))
    if not isinstance(raw, dict):
        raise ContractError(MALFORMED, "capture is not an object")
    if raw.get("schema") != CAPTURE_SCHEMA:
        raise ContractError(BAD_SCHEMA)
    _object(raw, _CAPTURE_KEYS, "capture")
    volume_id = _id(raw["volume_id"], VOLUME_ID_RE, "volume_id")
    if volume_id != expected.volume_id:
        raise ContractError(VOLUME_MISMATCH)
    started = _time(raw["scan_started_unix"], "scan_started_unix")
    finished = _time(raw["scan_finished_unix"], "scan_finished_unix")
    if finished < started:
        raise ContractError(BAD_FIELD, "capture finished before it started")
    budget = _Budget()
    roots = tuple(_capture_root(value, budget) for value in _list(raw["roots"], "roots", MAX_ROOTS_PER_VOLUME))
    if frozenset((root.scan_root, root.binding) for root in roots) != expected.roots or len(roots) != len(expected.roots):
        raise ContractError(BINDING_MISMATCH)
    outcome = _choice(raw["outcome"], CAPTURE_OUTCOMES, "outcome")
    if outcome != ("complete" if all(root.outcome == "complete" for root in roots) else "partial"):
        raise ContractError(OUTCOME_MISMATCH, "a volume's outcome is the worst of its roots")
    return VolumeCapture(volume_id, _id(raw["run_id"], RUN_ID_RE, "run_id"), _text(raw["group"], "group", 64),
                         started, finished, outcome, roots)


def _capture_root(value: Any, budget: "_Budget") -> CaptureRoot:
    raw = _object(value, _ROOT_KEYS, "capture root")
    scan_root = _path(raw["scan_root"], "scan_root")
    counts = _object(raw["counts"], _COUNT_KEYS, "counts")
    for key in _COUNT_KEYS:
        _int(counts[key], f"counts.{key}")
    outcome = _choice(raw["outcome"], CAPTURE_OUTCOMES, "root outcome")
    if outcome == "complete" and counts["errors"] != 0:
        raise ContractError(OUTCOME_MISMATCH, "a root with errors cannot be complete")
    _tree(raw["tree"], budget, 0)
    users = _rows(raw["users"], "users", ("uid", "name", "bytes", "files"), (), budget, scan_root)
    top_files = _rows(raw["top_files"], "top_files", ("path", "bytes", "uid", "mtime"), ("path_b64",), budget, scan_root)
    stale = _rows(raw["stale"], "stale", ("path", "kind", "bytes", "uid", "mtime"), ("path_b64",), budget, scan_root)
    errors = _rows(raw["errors"], "errors", ("path", "reason"), ("path_b64",), budget, scan_root)
    for row in errors:
        _choice(row["reason"], ERROR_REASONS, "error reason")
    return CaptureRoot(scan_root, _id(raw["binding"], BINDING_RE, "binding"), _text(raw["prune_class"], "prune_class", 64),
                       outcome, counts, raw["tree"], users, top_files, stale, errors)


def _tree(node: Any, budget: "_Budget", depth: int) -> None:
    if depth > MAX_TREE_DEPTH:
        raise ContractError(TREE_TOO_DEEP)
    budget.node()
    raw = _object(node, _NODE_KEYS + _NODE_OPTIONAL, "tree node", optional=_NODE_OPTIONAL)
    budget.string(_text(raw["name"], "tree.name", 4096))
    if "name_b64" in raw:
        budget.string(_text(raw["name_b64"], "tree.name_b64", 8192))
    _choice(raw["kind"], KINDS, "tree.kind")
    for key in ("bytes", "files", "uid"):
        _int(raw[key], f"tree.{key}")
    _time(raw["mtime"], "tree.mtime")
    other = _int(raw.get("other_bytes", 0), "tree.other_bytes")
    children = _list(raw.get("children", []), "tree.children", MAX_CHILDREN_PER_NODE)
    child_bytes = child_files = 0
    for child in children:
        _tree(child, budget, depth + 1)
        child_bytes += child["bytes"]
        child_files += child["files"]
    # Bytes roll up exactly: listed children plus the folded remainder. Files roll up at least.
    if (children or other) and raw["bytes"] != child_bytes + other:
        raise ContractError(TREE_SUM_MISMATCH, "node bytes differ from children plus other_bytes")
    if raw["files"] < child_files:
        raise ContractError(TREE_SUM_MISMATCH, "node files are fewer than its children's")


def _rows(value: Any, label: str, keys: Tuple[str, ...], optional: Tuple[str, ...], budget: "_Budget",
          scan_root: str) -> Tuple[Mapping[str, Any], ...]:
    rows = []
    for row in _list(value, label, MAX_ROWS):
        raw = _object(row, keys + optional, label, optional=optional)
        for key in ("uid", "bytes", "files"):
            if key in raw:
                _int(raw[key], f"{label}.{key}")
        if "mtime" in raw:
            _time(raw["mtime"], f"{label}.mtime")
        if "name" in raw:
            budget.string(_text(raw["name"], f"{label}.name", 128))
        if "kind" in raw:
            _choice(raw["kind"], KINDS, f"{label}.kind")
        if "path" in raw:
            path = _path(raw["path"], f"{label}.path")
            if path != scan_root and not path.startswith(scan_root.rstrip("/") + "/"):
                raise ContractError(BAD_FIELD, f"{label} path is outside its root")
            budget.string(path)
        if "path_b64" in raw:
            budget.string(_text(raw["path_b64"], f"{label}.path_b64", 16384))
        rows.append(raw)
    return tuple(rows)


class _Budget:
    def __init__(self) -> None:
        self.nodes = 0
        self.strings = 0

    def node(self) -> None:
        self.nodes += 1
        if self.nodes > MAX_TREE_NODES:
            raise ContractError(CAPTURE_TOO_LARGE, "tree node budget exceeded")

    def string(self, value: str) -> None:
        self.strings += len(value)
        if self.strings > MAX_STRING_BUDGET:
            raise ContractError(CAPTURE_TOO_LARGE, "string budget exceeded")


# ------------------------------------------------------------------ field readers


def _gunzip(data: bytes, limit: int) -> bytes:
    inflater = zlib.decompressobj(wbits=16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(data, limit + 1)
    except zlib.error as exc:
        raise ContractError(MALFORMED, "capture is not gzip") from exc
    if len(out) > limit:
        raise ContractError(CAPTURE_TOO_LARGE, "capture inflates past the limit")
    if not inflater.eof or inflater.unused_data:
        raise ContractError(MALFORMED, "capture is truncated or has trailing data")
    return out


def decode_json(data: bytes) -> Any:
    try:
        return json.loads(data)
    except RecursionError as exc:
        raise ContractError(TREE_TOO_DEEP) from exc
    except ValueError as exc:
        raise ContractError(MALFORMED, "not JSON") from exc


def _object(value: Any, keys: Iterable[str], label: str, optional: Iterable[str] = ()) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(BAD_FIELD, f"{label} is not an object")
    allowed = set(keys)
    required = allowed - set(optional)
    if not required <= set(value) or not set(value) <= allowed:
        raise ContractError(BAD_FIELD, f"{label} keys differ from the schema")
    return value


def _list(value: Any, label: str, limit: int) -> list:
    if not isinstance(value, list):
        raise ContractError(BAD_FIELD, f"{label} is not an array")
    if len(value) > limit:
        raise ContractError(CAPTURE_TOO_LARGE if limit >= MAX_CHILDREN_PER_NODE else BAD_FIELD, f"{label} is too long")
    return value


def _int(value: Any, label: str, lo: int = 0, hi: int = MAX_COUNTER) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise ContractError(BAD_FIELD, f"{label} is not an integer in range")
    return value


def _time(value: Any, label: str) -> int:
    return _int(value, label, 0, MAX_TIME)


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise ContractError(BAD_FIELD, f"{label} is not a bounded string")
    return value


def _code(value: Any, label: str) -> str:
    text = _text(value, label, 64)
    if not text or not all(ch.isupper() or ch.isdigit() or ch == "_" for ch in text):
        raise ContractError(BAD_FIELD, f"{label} is not a code")
    return text


def _path(value: Any, label: str) -> str:
    """Any absolute UTF-8 path without NUL (§2.2). Control characters are refused at removal selection (§4.4)."""
    text = _text(value, label, 4096)
    if not text.startswith("/"):
        raise ContractError(BAD_FIELD, f"{label} is not an absolute path")
    return text


def _id(value: Any, pattern: Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ContractError(BAD_ID, f"{label} is malformed")
    return value


def _choice(value: Any, allowed: FrozenSet[str], label: str) -> str:
    # The type first: a list or dict is unhashable and would raise TypeError on the set lookup.
    if not isinstance(value, str) or value not in allowed:
        raise ContractError(BAD_FIELD, f"{label} is not one of the allowed values")
    return value


def _optional(value: Any, parse: Callable[[Any], Any]) -> Any:
    return None if value is None else parse(value)


def _unique(values: Iterable[str], label: str) -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise ContractError(DUPLICATE_ID, f"{label} appears twice")
        seen.add(value)


# ------------------------------------------------------------------ v1 responses


def snapshot_response(snapshot, thresholds=CapacityThresholds()):
    """Add the collector's judgments to a projected v1 snapshot without changing stored data."""
    response = dict(snapshot)
    if "mounts" in snapshot:
        response["mounts"] = [
            {**mount, **judge.capacity_fields(mount, thresholds)} for mount in snapshot["mounts"]
        ]
    for table in ("top_files", "stale"):
        if table in snapshot:
            response[table] = [
                {**row, "age_days": judge.age_days(row.get("mtime"), snapshot.get("scan_started_unix"))}
                for row in snapshot[table]
            ]
    return response
