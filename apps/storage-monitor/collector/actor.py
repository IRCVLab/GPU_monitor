"""ServerActor: the only writer of one server's record, store directory, SSH contact, job and update track.

Every change goes through the pure reducers in ``health``, ``jobs`` and ``update``. The actor
commits the new record before it runs the effects the reducers asked for, then
publishes a new immutable ``ServerView`` by one attribute assignment. Transfers,
update requests and the blocking rescan start run in helper threads that post their
results back to the inbox, so observes continue while they run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Mapping, Optional, Protocol, Tuple

from collector import contract, health, jobs, judge, snapshot
from collector import update as updates
from collector.contract import AgentFacts, ContractError, HostManifest, HostRun, LastRun, ManifestPointer
from collector.errors import RescanUnavailable, StoreUnavailable
from collector.health import (
    LEGACY_VOLUME, CaptureAccepted, CaptureGone, CaptureMismatch, CaptureRejected, ExchangeFailed, ExchangeOk, Fetch,
    LegacyPublication, PublicationObserved, TransferFailed,
)
from collector.inventory import Server
from collector.store import DURATION_LIMIT, DURATION_PLANS, Record, ServerStore
from collector.transport import TransportError
from collector.view import ObservationView, ServerView, Snapshot, build_view, render_snapshot, volumes_payload

_LOG = logging.getLogger(__name__)

# Transfer failures that condemn the published capture. Any other code is retried on a later observe.
_REJECT_CODES = frozenset({"SNAPSHOT_TOO_LARGE", "BAD_STATUS", "BAD_GENERATION", "CAPTURE_TOO_LARGE"})
_GONE_CODES = frozenset({"CAPTURE_GONE"})
"""The host pruned the capture: the manifest moved on. Reread it; the sha is not condemned."""
_MISMATCH_CODES = frozenset({"SIZE_MISMATCH"})
_MISMATCH_MESSAGES = ("byte_size does not match downloaded bytes", "sha256 does not match downloaded bytes")
# What malformed capture content raises inside validation. Anything else (MemoryError, OSError) is retried.
_CONTENT_ERRORS = (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError)
TICK_FAILED = "TICK_FAILED"
MAX_TICK_BACKOFF_SECONDS = 10 * 60
SEQ_BLOCK = 1000
"""Contact sequence numbers reserved per record write. record.json stores the ceiling as link.seq."""
UPDATE_BUSY = updates.REQUEST_STATES
"""Update states that observe at the active interval, to read the host's answer soon."""
CONTRACT_REJECTED = "CONTRACT_REJECTED"
MANIFEST_REFUSALS = contract.REASONS | {"IDENTITY_MISMATCH", "MANIFEST_SEQ_REGRESSED"}
"""Refusals of a manifest that blame the agent that wrote it."""
AGENT_FAULT_PREFIX = "INTERNAL_"
BUNDLE_UNAVAILABLE = "BUNDLE_UNAVAILABLE"


class Clock(Protocol):
    def time(self) -> float: ...
    def monotonic(self) -> float: ...


class Host(Protocol):
    """What the actor asks of a storage server: ``gate.HostRouter`` sends each call over the
    server's transport, the gate (``gate.GateHost``) or S2 (``legacy.LegacyHost``)."""
    def observe(self, server: Server) -> Any:
        """An Observation whose ``transport`` names who answered. Only the gate delivers request ids."""
    def get_manifest(self, server: Server) -> bytes: ...
    def get_capture(self, server: Server, volume_id: str, sha256: str, cap: int) -> bytes: ...
    def rescan(self, server: Server, request_id: str) -> jobs.RescanReply: ...
    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes:
        """SHIM(M2b..M5c) S3: the v1 snapshot named by a scan-status envelope."""
    def start_rescan(self, server: Server) -> None:
        """SHIM(M2b..M5c) S4: the blocking start of the manual scan unit."""
    def stage_update(self, server: Server, sha: str, bundle: bytes, signature: bytes) -> None: ...
    def activate_update(self, server: Server, sha: str) -> None: ...
    def rollback_update(self, server: Server, sha: str) -> None: ...


class Channel(Protocol):
    """The fleet's side of the agent update channel (``fleet.Rollout``)."""
    def desired(self) -> Any:
        """The desired agent release (``fleet.DesiredAgent``: ``sha``, ``read_bundle()``), or None."""
    def permits(self, server_id: str) -> bool:
        """Whether the rollout lets this host take the desired release at all (no slot involved)."""
    def acquire(self, server_id: str) -> bool:
        """Reserve one of the fleet's staging slots, if the rollout lets this host stage now."""
    def hold(self, server_id: str) -> None:
        """Keep the slot of a host that was staging before a restart."""
    def release(self, server_id: str) -> None: ...
    def reported(self, server_id: str, before: updates.Update, after: updates.Update) -> None:
        """A committed change of this host's update track."""
    def confirmed(self, server_id: str, sha: str) -> None:
        """The host runs ``sha`` in sync, and its accepted manifest shows a good real run by it."""


class NoChannel:
    """No desired agent release: every update track stays idle."""

    def desired(self) -> Any:
        return None

    def permits(self, server_id: str) -> bool:
        return False

    def acquire(self, server_id: str) -> bool:
        return False

    def hold(self, server_id: str) -> None:
        pass

    def release(self, server_id: str) -> None:
        pass

    def reported(self, server_id: str, before: updates.Update, after: updates.Update) -> None:
        pass

    def confirmed(self, server_id: str, sha: str) -> None:
        pass


def spawn_thread(work: Callable[[], None], name: str) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()


class Ask:
    """A reply slot with a bound. The asker waits at most its timeout.

    A request the actor has not claimed by then is dropped unseen. One it has claimed
    still completes, and ``settled`` runs once either way, when the outcome is final.
    """

    def __init__(self, settled: Callable[[], None] = lambda: None) -> None:
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._state = "open"
        self._settled = settled
        self.value: Any = None

    def claim(self) -> bool:
        with self._lock:
            if self._state != "open":
                return False
            self._state = "claimed"
            return True

    def reply(self, value: Any) -> None:
        self.value = value
        self._done.set()
        self._settled()

    def wait(self, timeout: float) -> Any:
        """The reply, or None when none came within ``timeout``."""
        if self._done.wait(timeout):
            return self.value
        with self._lock:
            cancelled = self._state == "open"
            if cancelled:
                self._state = "cancelled"
        if cancelled:
            self._settled()
        return self.value if self._done.is_set() else None


@dataclass(frozen=True)
class Admit:
    request_id: str
    actor: str
    at: int
    cooldown_seconds: int
    ask: Ask


@dataclass(frozen=True)
class Admitted:
    job: Optional[jobs.Job] = None
    error: Optional[str] = None
    retry_after: Optional[int] = None


@dataclass(frozen=True)
class Fetched:
    seq: int
    volume_id: str
    sha: str
    generation: Optional[str]
    outcome: str
    """accepted, rejected, mismatch, gone, retry or transport."""
    code: Optional[str] = None
    payload: Optional[Mapping[str, Any]] = None
    data: Optional[bytes] = None


@dataclass(frozen=True)
class Replied:
    """The host's answer to ``rescan <request_id>``; ambiguous when the exchange gave none."""
    seq: int
    outcome: str
    code: Optional[str] = None


@dataclass(frozen=True)
class Dispatched:
    seq: int
    ok: bool
    code: Optional[str] = None


@dataclass(frozen=True)
class UpdateSent:
    """The result of one stage, activate or rollback request."""
    seq: int
    effect: updates.Effect
    ok: bool
    code: Optional[str] = None
    contacted: bool = True
    """False when the request never reached ssh (the bundle could not be read)."""


STOP = object()


@dataclass(frozen=True)
class Refusal:
    """A manifest central refused for its content, as read just now."""
    code: str
    sha256: str
    claimed_release: Optional[str]
    """agent.release_sha as the refused bytes claim it, untrusted: only ever a reason not to blame a release."""


def blames(refusal: Optional[Refusal], update: updates.Update) -> bool:
    """Whether a refused manifest counts against the release in probation.

    Only a manifest that appeared after the activation: not the one the host showed when probation
    began, and not one whose bytes say another release wrote it.
    """
    return (refusal is not None and refusal.sha256 != update.probation_manifest
            and refusal.claimed_release in (None, update.desired))


def probation_verdict(update: updates.Update, data: health.Data) -> Optional[Tuple[bool, Optional[str]]]:
    """(fault, code) for the first real run by the desired release, or None while there is none yet.

    The run is the manifest's last run by the desired release that started after the activation
    (host times both).
    """
    if update.requested_host_unix is None:
        return None
    return run_verdict(update.desired, data, since=update.requested_host_unix)


def run_verdict(release: Optional[str], data: health.Data, since: Optional[int] = None) -> Optional[Tuple[bool, Optional[str]]]:
    """(fault, code) for the accepted manifest's last run by ``release``, or None when it decides nothing.

    The manifest must be written by ``release`` and its last run made by it (started at or after
    ``since``, host time). An ``INTERNAL_*`` error on any volume of that run is an agent fault. A
    complete or partial run passes once central holds every capture the manifest names, and a
    rejected capture is CONTRACT_REJECTED. Environment errors (EACCES, a failed group) are scan
    diagnostics: the track waits for a real run, up to cadence + 1 h.
    """
    manifest = data.manifest
    run = manifest.last_run if isinstance(manifest, HostManifest) else None
    if (release is None or run is None or run.agent_sha != release or manifest.raw["agent"]["release_sha"] != release
            or (since is not None and run.started_unix < since)):
        return None
    for volume in manifest.volumes:
        attempt = volume.last_attempt
        if attempt is not None and attempt.run_id == run.run_id and (attempt.error_code or "").startswith(AGENT_FAULT_PREFIX):
            return True, attempt.error_code
    if run.outcome not in ("complete", "partial"):
        return None
    progress = health.captures_progress(data)
    if progress == "rejected":
        return True, CONTRACT_REJECTED
    return (False, None) if progress == "current" else None


def _cadence(data: health.Data) -> int:
    manifest = data.manifest
    if isinstance(manifest, HostManifest) and manifest.volumes:
        return max(volume.cadence_sec for volume in manifest.volumes)
    return updates.Observed.__dataclass_fields__["cadence_sec"].default


def _capture_event(message: Fetched, at: int) -> health.DataEvent:
    if message.outcome == "accepted":
        return CaptureAccepted(message.volume_id, message.sha)
    if message.outcome == "rejected":
        return CaptureRejected(message.volume_id, message.sha, message.generation, message.code, at)
    if message.outcome == "mismatch":
        return CaptureMismatch(message.volume_id, message.sha, message.generation, at)
    if message.outcome == "gone":
        return CaptureGone(message.volume_id, message.sha, at)
    return TransferFailed(message.volume_id, message.sha)


def _durable(record: Record) -> Record:
    """The record less what changes on every exchange or lives only in memory; a change here needs a write."""
    return replace(record, link=replace(record.link, seq=0, last_ok_unix=None),
                   data=replace(record.data, fetching=frozenset(), retried=frozenset(), gone={}))


def record_duration(durations: Mapping[str, Any], status: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep the last completed full and ssd scan durations for the ETA (today's rule)."""
    if status.get("state") != "complete":
        return durations
    finished = status.get("finished_unix") or status.get("last_finished_unix")
    return _add_duration(durations, status.get("scan_plan"), finished, status.get("last_duration_sec"))


def last_run_duration(durations: Mapping[str, Any], last_run: Optional[LastRun]) -> Mapping[str, Any]:
    """The same history from a v2 manifest: its last run, when complete or partial, keyed by plan."""
    if last_run is None or last_run.outcome not in ("complete", "partial"):
        return durations
    return _add_duration(durations, last_run.plan, last_run.finished_unix, last_run.finished_unix - last_run.started_unix)


def _add_duration(durations: Mapping[str, Any], plan: Any, finished: Any, duration: Any) -> Mapping[str, Any]:
    if (plan not in DURATION_PLANS or type(duration) is not int or duration < 0
            or type(finished) is not int or finished <= 0):
        return durations
    entries = durations.get(plan, [])
    if any(recorded == finished for recorded, _ in entries):
        return durations
    return {**durations, plan: [list(entry) for entry in sorted([*entries, [finished, duration]])[-DURATION_LIMIT:]]}


class ServerActor:
    def __init__(self, server: Server, store: ServerStore, host: Host, *, clock: Clock = time,
                 transfers: Optional[threading.BoundedSemaphore] = None,
                 spawn: Callable[[Callable[[], None], str], None] = spawn_thread,
                 channel: Optional[Channel] = None) -> None:
        self.server = server
        self.store = store
        self.host = host
        self.channel: Channel = channel or NoChannel()
        self.clock = clock
        self.transfers = transfers or threading.BoundedSemaphore(1)
        self.spawn = spawn
        self.inbox: "queue.Queue[Any]" = queue.Queue()
        self.view = ServerView(server=server)
        self.loaded = False
        self.started = threading.Event()
        """Set once the first load attempt has published a view, loaded or not."""
        self._record = Record()
        self._captures: Mapping[str, tuple] = {}
        """volume_id -> (sha, parsed capture) for every accepted capture: a v1 payload (S3) or a VolumeCapture."""
        self._snapshot: Optional[Snapshot] = None
        self._rendered: Any = None
        """What ``_snapshot`` was rendered from, so an unchanged publication renders nothing."""
        self._bad_manifest: Optional[tuple] = None
        """Memory only: (sha256, code) of the last manifest refused, so it is read once per process."""
        self._reread_manifest = False
        self._observation: Optional[ObservationView] = None
        self._scan: Optional[jobs.Scan] = None
        self._run: HostRun = HostRun()
        self._host_clock = None
        self._error: Optional[str] = None
        self._agent = AgentFacts()
        """The agent release and updater state from the last gate observe."""
        self._seq = 0
        self._ceiling = 0
        """No seq above this was issued: the link.seq record.json holds."""
        self._next_contact = 0.0
        self._failed_ticks = 0
        self._transport = server.transport
        """Who answered the last observe: ``gate`` or ``legacy`` (S2). The inventory's choice until then."""

    # ------------------------------------------------------------ driving

    def post(self, message: Any) -> None:
        self.inbox.put(message)

    def run(self, stop: threading.Event) -> None:
        self._guard(self.load)
        self.started.set()
        while not stop.is_set():
            wait = max(0.0, self._next_contact - self.clock.monotonic())
            try:
                message = self.inbox.get(timeout=min(wait, judge.ACTIVE_TELEMETRY_INTERVAL_SECONDS))
            except queue.Empty:
                message = None
            if message is STOP:
                return
            if message is not None:
                self._guard(self._handle, message)
            self.step()

    def step(self) -> None:
        """Handle every queued message, then contact the host if it is due."""
        while True:
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            if message is not STOP:
                self._guard(self._handle, message)
        if self.clock.monotonic() >= self._next_contact:
            self._guard(self._tick)

    def _guard(self, work: Callable[..., None], *args: Any) -> None:
        # One server's failure never leaves its actor: the view keeps the last good data (I5).
        try:
            work(*args)
        except StoreUnavailable:
            _LOG.exception("store unavailable for %s", self.server.id)
            self._error = StoreUnavailable.code
        except Exception:
            _LOG.exception("actor tick failed for %s", self.server.id)
            self._error = TICK_FAILED
        self._publish()

    # ------------------------------------------------------------ state

    def load(self) -> None:
        """Read the record and its capture. A read error leaves both untouched and is retried on the next tick.

        Load deletes no file: a capture it finds corrupt goes with the next regular commit.
        """
        self._next_contact = self.clock.monotonic() + judge.IDLE_TELEMETRY_INTERVAL_SECONDS
        stored = self.store.load()
        if stored is None:
            stored, capture = self.store.import_legacy() or (Record(), None)
            self.store.commit(stored, capture, prune=False)
        captures = self._load_captures(stored)
        record = stored
        if set(captures) != set(record.data.accepted):
            record = replace(record, data=replace(record.data, accepted={
                volume_id: sha for volume_id, sha in record.data.accepted.items() if volume_id in captures}))
        self._swap(self._hold(record.data, captures))
        if record.job is not None:
            record = self._with_job(record, jobs.reduce(record.job, jobs.Restarted(int(self.clock.time())))[0])
        if record != stored:
            self.store.commit(record, prune=False)
        self._record = record
        if record.update.state in updates.STAGING_STATES:
            self.channel.hold(self.server.id)
        else:
            self.channel.release(self.server.id)
        self._seq = self._ceiling = record.link.seq
        self._error = None
        self.loaded = True
        self._next_contact = 0.0

    def _load_captures(self, record: Record) -> dict:
        """Parse every accepted capture. A read error propagates and leaves the record as it is;
        a corrupt capture is dropped, so it is fetched again."""
        captures = {}
        manifest = record.data.manifest
        for volume_id, sha in record.data.accepted.items():
            data = self.store.read_capture(volume_id, sha)
            try:
                if volume_id == LEGACY_VOLUME:
                    captures[volume_id] = (sha, json.loads(data))
                elif isinstance(manifest, HostManifest) and manifest.volume(volume_id) is not None:
                    captures[volume_id] = (sha, contract.parse_capture(data, manifest.volume(volume_id).expected(sha)))
            except ValueError:
                _LOG.warning("stored capture for %s is corrupt; it will be fetched again", self.server.id)
        return captures

    def _hold(self, data: health.Data, captures: Mapping[str, tuple]) -> tuple:
        """(captures, render key, snapshot) for ``data``: the accepted captures and the v1 snapshot built from them.
        The snapshot is reused when nothing it shows changed. Nothing is assigned until ``_swap``."""
        captures = {volume_id: held for volume_id, held in captures.items() if data.accepted.get(volume_id) == held[0]}
        if LEGACY_VOLUME in captures:
            key = ("v1", captures[LEGACY_VOLUME][0])
        elif isinstance(data.manifest, HostManifest) and captures:
            key = ("v2", data.manifest.sha256, tuple(sorted(data.accepted.items())),
                   tuple(sorted(health.volume_states(data).items())), tuple(sorted(health.volume_problems(data).items())))
        else:
            key = None
        if key == self._rendered:
            return captures, key, self._snapshot
        if key is None:
            return captures, key, None
        if key[0] == "v1":
            return captures, key, render_snapshot(captures[LEGACY_VOLUME][1])
        parsed = {volume_id: capture for volume_id, (_, capture) in captures.items()}
        return captures, key, render_snapshot(volumes_payload(self.server.id, data.manifest, parsed, health.volume_states(data),
                                                              health.volume_problems(data)))

    def _swap(self, held: tuple) -> None:
        self._captures, self._rendered, self._snapshot = held

    def _commit(self, record: Record, capture: Optional[tuple] = None) -> bool:
        """Make ``record`` current, writing it only when its durable part changed or a capture comes with it."""
        if capture is None and _durable(record) == _durable(self._record):
            self._record = record
            return True
        return self._write(record, capture)

    def _write(self, record: Record, capture: Optional[tuple] = None) -> bool:
        # On disk link.seq is the ceiling of issued seqs, so a restart resumes above all of them (I7).
        ceiling = self._ceiling if self._seq <= self._ceiling else self._seq + SEQ_BLOCK - 1
        try:
            self.store.commit(replace(record, link=replace(record.link, seq=ceiling)), capture)
        except StoreUnavailable:
            _LOG.exception("commit failed for %s", self.server.id)
            self._error = StoreUnavailable.code
            return False
        self._ceiling = ceiling
        self._record = record
        if self._error == StoreUnavailable.code:
            self._error = None
        return True

    def _publish(self) -> None:
        self.view = build_view(self.server, self._record, self._snapshot, self._observation, self._host_clock, self._error,
                               self._transport, self._agent.release_sha)

    def _next_seq(self) -> int:
        self._seq += 1
        if self._seq > self._ceiling:
            # Reserve a block before the seq leaves the actor. If the write fails, the next seq tries again.
            self._write(self._record)
        return self._seq

    @staticmethod
    def _with_job(record: Record, job: jobs.Job) -> Record:
        if job.state in jobs.TERMINAL:
            return replace(record, job=None, last_job=job)
        return replace(record, job=job)

    def _job_observed(self, record: Record, held: Optional[Snapshot]) -> tuple:
        if record.job.protocol == "receipt":
            return jobs.reduce(record.job, self._trace(record))
        if self._scan is None:
            return record.job, ()
        rejected = frozenset(entry.get("generation") for entry in record.data.rejected.values())
        accepted = held.overview.get("scan_generation") if held is not None else None  # SHIM(M2b..M5c) S3
        event = jobs.Observed(int(self.clock.time()), self._scan, accepted, rejected)
        return jobs.reduce(record.job, event)

    def _trace(self, record: Record) -> jobs.Traced:
        request_id = record.job.request_id
        manifest = record.data.manifest
        receipts = manifest.requests if isinstance(manifest, HostManifest) else ()
        return jobs.Traced(
            int(self.clock.time()), pending=request_id in self._run.pending, running=request_id in self._run.request_ids,
            receipt=next((r.outcome for r in receipts if r.request_id == request_id), None),
            captures=health.captures_progress(record.data))

    # ------------------------------------------------------------ messages

    def _handle(self, message: Any) -> None:
        if isinstance(message, Admit):
            self._admit(message)
        elif isinstance(message, Fetched):
            self._fetched(message)
        elif isinstance(message, Dispatched):
            self._dispatched(message)
        elif isinstance(message, Replied):
            self._replied(message)
        elif isinstance(message, UpdateSent):
            self._update_sent(message)
        else:
            raise TypeError(f"unknown actor message {type(message).__name__}")

    def _tick(self) -> None:
        if not self.loaded:
            self.load()
            return
        # Until the observe schedules the next contact, a failure waits out a back-off that grows to a cap.
        backoff = judge.IDLE_TELEMETRY_INTERVAL_SECONDS * 2 ** min(self._failed_ticks, 8)
        self._next_contact = self.clock.monotonic() + min(backoff, MAX_TICK_BACKOFF_SECONDS)
        self._failed_ticks += 1
        self._observe()
        record = self._record
        if record.job is not None:
            longest = judge.eta(record.durations, "full", None)[1]
            job, _ = jobs.reduce(record.job, jobs.Tick(int(self.clock.time()), jobs.run_deadline_seconds(longest)))
            self._commit(self._with_job(record, job))
        self._failed_ticks = 0
        if self._error == TICK_FAILED:
            self._error = None

    def _observe(self) -> None:
        seq = self._next_seq()
        at = int(self.clock.time())
        active = self._record.job is not None
        try:
            observation = self.host.observe(self.server)
        except TransportError as exc:
            self._observation = ObservationView(
                {"state": "unknown", "error_code": exc.code, "progress_available": False, "observed_unix": at},
                self.clock.monotonic())
            self._schedule(active)
            self._commit(replace(self._record, link=health.reduce_link(self._record.link, ExchangeFailed(seq, at, exc.code))))
            return
        received = self.clock.monotonic()
        self._transport = observation.transport
        self._host_clock = (observation.host_now, received)
        self._observation = ObservationView({**observation.status, "observed_unix": at}, received)
        self._agent = observation.agent
        self._scan = observation.scan
        self._run = observation.run
        record = self._record
        before = record.update
        refused = None
        if observation.manifest is not None:
            record, fetches, refused = self._manifest_observed(record, observation.manifest, seq, at)
        elif observation.publication_error is None:
            # SHIM(M2b..M5c) S3: a host without manifest.json, or one rolled back to the v1 agent.
            data, fetches = health.reduce_data(record.data, PublicationObserved(LegacyPublication(observation.publication), at))
            record = replace(record, link=health.reduce_link(record.link, ExchangeOk(seq, at)), data=data,
                             durations=record_duration(record.durations, observation.status))
        else:
            record = replace(record, link=health.reduce_link(record.link, ExchangeFailed(seq, at, observation.publication_error)),
                             durations=record_duration(record.durations, observation.status))
            fetches = ()
        dispatches: tuple = ()
        if record.job is not None:
            job, dispatches = self._job_observed(record, self._snapshot)
            record = self._with_job(record, job)
        record, update_effects = self._update_observed(record, observation, at, refused)
        self._schedule(record.job is not None or observation.status.get("state") == "running"
                       or record.update.state in UPDATE_BUSY)
        committed = self._commit(record)
        self._update_settled(before, update_effects if committed else ())
        if not committed:
            return
        self._swap(self._hold(record.data, self._captures))
        for fetch in fetches:
            self._start_transfer(fetch)
        for _ in dispatches:
            self._dispatch(record.job)

    def _manifest_observed(self, record: Record, pointer: ManifestPointer, seq: int, at: int) -> tuple:
        """Read a changed manifest, then publish the held one to the data reducer (design §4.2 steps 2, 3).

        Returns (record, fetches, refusal): a Refusal when a newly read manifest was refused for its content."""
        held = record.data.manifest if isinstance(record.data.manifest, HostManifest) else None
        link = health.reduce_link(record.link, ExchangeOk(seq, at))
        manifest = held
        if self._reread_manifest or held is None or pointer.sha256 != held.sha256:
            if not self._reread_manifest and self._bad_manifest is not None and self._bad_manifest[0] == pointer.sha256:
                # The same refused manifest: keep the link on its error without reading it again.
                return replace(record, link=health.reduce_link(record.link, ExchangeFailed(seq, at, self._bad_manifest[1]))), (), None
            self._reread_manifest = False
            read_seq = self._next_seq()
            try:
                raw = self.host.get_manifest(self.server)
            except TransportError as exc:
                if exc.code in health.TRANSPORT_CODES | health.AUTH_CODES:
                    return replace(record, link=health.reduce_link(link, ExchangeFailed(read_seq, at, exc.code))), (), None
                if exc.code == "MANIFEST_MISSING":
                    # Replaced between observe and read, or rolled back: the next observe decides.
                    return replace(record, link=link), (), None
                raw, code = None, exc.code
            else:
                try:
                    manifest = contract.parse_manifest(raw)
                    code = _manifest_problem(manifest, held, self.server.id)
                except ContractError as exc:
                    code = exc.reason
                except Exception:
                    # The observe must still finish, so job deadlines keep running.
                    _LOG.exception("manifest check failed for %s", self.server.id)
                    code = "MANIFEST_UNREADABLE"
            if code is not None:
                self._bad_manifest = (hashlib.sha256(raw).hexdigest() if raw is not None else pointer.sha256, code)
                refused = Refusal(code, self._bad_manifest[0], contract.claimed_release(raw)) if code in MANIFEST_REFUSALS else None
                return replace(record, link=health.reduce_link(link, ExchangeFailed(read_seq, at, code))), (), refused
            self._bad_manifest = None
            link = health.reduce_link(link, ExchangeOk(read_seq, at))
        # Every observe republishes the held manifest, so a failed transfer is retried (design §4.2 step 3).
        data, fetches = health.reduce_data(record.data, PublicationObserved(manifest, at))
        return replace(record, link=link, data=data, durations=last_run_duration(record.durations, manifest.last_run)), fetches, None

    def _schedule(self, busy: bool) -> None:
        interval = judge.ACTIVE_TELEMETRY_INTERVAL_SECONDS if busy else judge.IDLE_TELEMETRY_INTERVAL_SECONDS
        self._next_contact = self.clock.monotonic() + interval

    def _admit(self, message: Admit) -> None:
        if not message.ask.claim():
            return
        reply = Admitted(error="INTERNAL")
        try:
            reply = self._admission(message)
        finally:
            message.ask.reply(reply)

    def _admission(self, message: Admit) -> Admitted:
        record = self._record
        if not self.loaded:
            return Admitted(error=StoreUnavailable.code)
        if record.job is not None:
            return Admitted(job=record.job, error="ACTIVE_JOB")
        last = record.last_job.requested_unix if record.last_job is not None else None
        if last is not None and message.at - last < message.cooldown_seconds:
            return Admitted(error="COOLDOWN", retry_after=message.cooldown_seconds - (message.at - last))
        protocol = "receipt" if isinstance(record.data.manifest, HostManifest) else "invocation"
        if protocol == "receipt" and self._transport != "gate":
            # Refused before any job or cooldown exists: only the gate writes the request mailbox (S2 cannot).
            return Admitted(error=RescanUnavailable.code)
        job = jobs.admit(message.request_id, message.actor, message.at, protocol)
        if not self._commit(replace(record, job=job)):
            return Admitted(error=StoreUnavailable.code)
        self._publish()
        # Decide on a fresh observation: start the scan, wait for the running one, or tie to it.
        self._next_contact = 0.0
        return Admitted(job=job)

    # ------------------------------------------------------------ transfers

    def _start_transfer(self, fetch: Fetch) -> None:
        if isinstance(fetch.publication, HostManifest):
            self._start_volume_transfer(fetch)
            return
        seq = self._next_seq()
        manifest = fetch.publication.envelope
        volume_id, sha = fetch.volume_id, fetch.sha
        generation = manifest["generation"].removesuffix(".json")
        server, host, transfers = self.server, self.host, self.transfers

        def work() -> None:
            try:
                with transfers:
                    data = host.fetch_capture(server, manifest)
            except TransportError as exc:
                outcome = "mismatch" if exc.code in _MISMATCH_CODES else (
                    "transport" if exc.code in health.TRANSPORT_CODES | health.AUTH_CODES else (
                        "rejected" if exc.code in _REJECT_CODES else "retry"))
                self.post(Fetched(seq, volume_id, sha, generation, outcome, exc.code))
                return
            except Exception:
                _LOG.exception("capture transfer failed for %s", server.id)
                self.post(Fetched(seq, volume_id, sha, generation, "retry", "TRANSFER_FAILED"))
                return
            desired = snapshot.DesiredServer(server_id=server.id, config_digest=server.scanner_digest)
            try:
                valid = snapshot.validate_download(manifest, data, desired)
            except _CONTENT_ERRORS as exc:
                mismatch = str(exc) in _MISMATCH_MESSAGES
                self.post(Fetched(seq, volume_id, sha, generation, "mismatch" if mismatch else "rejected",
                                  "SHA_MISMATCH" if mismatch else "INVALID_SNAPSHOT"))
                return
            except Exception:
                _LOG.exception("capture validation failed for %s", server.id)
                self.post(Fetched(seq, volume_id, sha, generation, "retry", "TRANSFER_FAILED"))
                return
            self.post(Fetched(seq, volume_id, sha, generation, "accepted", payload=valid.payload, data=data))

        self.spawn(work, f"storage-viz-transfer-{server.id}")

    def _start_volume_transfer(self, fetch: Fetch) -> None:
        seq = self._next_seq()
        volume_id, sha = fetch.volume_id, fetch.sha
        volume = fetch.publication.volume(volume_id)
        declared, expected = volume.capture.bytes, volume.expected(sha)
        server, host, transfers = self.server, self.host, self.transfers

        def post(outcome: str, code: Optional[str] = None, **fields: Any) -> None:
            self.post(Fetched(seq, volume_id, sha, None, outcome, code, **fields))

        def work() -> None:
            if declared > contract.MAX_CAPTURE_BYTES:
                post("rejected", contract.CAPTURE_TOO_LARGE)
                return
            try:
                with transfers:
                    data = host.get_capture(server, volume_id, sha, declared)
            except TransportError as exc:
                post("gone" if exc.code in _GONE_CODES else "transport" if exc.code in health.TRANSPORT_CODES | health.AUTH_CODES
                     else "rejected" if exc.code in _REJECT_CODES else "mismatch" if exc.code in _MISMATCH_CODES
                     else "retry", exc.code)
                return
            except Exception:
                _LOG.exception("capture transfer failed for %s", server.id)
                post("retry", "TRANSFER_FAILED")
                return
            try:
                capture = contract.parse_capture(data, expected)
            except ContractError as exc:
                post("mismatch" if exc.reason == contract.SHA_MISMATCH else "rejected", exc.reason)
                return
            except Exception:
                # Not the capture's fault (memory, a bug): never leave the sha in flight.
                _LOG.exception("capture validation failed for %s", server.id)
                post("retry", "TRANSFER_FAILED")
                return
            post("accepted", payload=capture, data=data)

        self.spawn(work, f"storage-viz-transfer-{server.id}")

    def _fetched(self, message: Fetched) -> None:
        at = int(self.clock.time())
        record = self._record
        try:
            link_event = ExchangeFailed(message.seq, at, message.code) if message.outcome == "transport" else ExchangeOk(message.seq, at)
            data, _ = health.reduce_data(record.data, _capture_event(message, at))
            record = replace(record, link=health.reduce_link(record.link, link_event), data=data)
            accepted = data.accepted.get(message.volume_id) == message.sha != self._record.data.accepted.get(message.volume_id)
            captures = {**self._captures, message.volume_id: (message.sha, message.payload)} if accepted else self._captures
            held = self._hold(data, captures)
            if record.job is not None and record.job.state == "collecting":
                job, _ = self._job_observed(record, held[2])
                record = self._with_job(record, job)
            before = record.update
            record, update_effects = self._judge_probation(record, at, None, ())
        except Exception:
            self._drop_fetch(message.volume_id, message.sha)
            raise
        committed = self._commit(record, (message.volume_id, message.sha, message.data) if accepted else None)
        self._update_settled(before, update_effects if committed else ())
        if not committed:
            self._drop_fetch(message.volume_id, message.sha)
            return
        self._swap(held)
        if message.outcome == "gone" and record.data.gone.get(message.sha, (0, 0))[0] == 1:
            # Probably pruned after a newer manifest: read it again now. A repeat waits out its back-off.
            self._reread_manifest = True
            self._next_contact = 0.0

    def _drop_fetch(self, volume_id: str, sha: str) -> None:
        """The transfer's result was not applied, so the next observe fetches the capture again."""
        data, _ = health.reduce_data(self._record.data, TransferFailed(volume_id, sha))
        self._record = replace(self._record, data=data)

    # ------------------------------------------------------------ agent update track

    def _host_time(self) -> Optional[int]:
        return judge.host_now(*self._host_clock, self.clock.monotonic()) if self._host_clock else None

    def _update_observed(self, record: Record, observation: Any, at: int, refused: Optional[str]) -> tuple:
        """Feed one observe to the update track: a desired-release change, then over the gate the host's state."""
        update = record.update
        desired = self.channel.desired()
        sha = desired.sha if desired is not None else None
        if sha != update.desired:
            update, _ = updates.reduce(update, updates.DesiredChanged(at, sha))
        effects: tuple = ()
        if observation.transport == "gate":  # S2 cannot stage: the track only follows the desired release
            agent = observation.agent
            permitted = self.channel.permits(self.server.id)
            allowed = (permitted and update.state in ("in_sync", "backoff") and update.desired is not None
                       and agent.release_sha != update.desired and self.channel.acquire(self.server.id))
            update, effects = updates.reduce(update, updates.Observed(
                at, observation.host_now, agent.release_sha, idle=not observation.run.present,
                job_active=record.job is not None, allowed=allowed, permitted=permitted, broken=agent.broken,
                attempt=agent.attempt, cadence_sec=_cadence(record.data),
                manifest_sha=observation.manifest.sha256 if observation.manifest is not None else None))
        return self._judge_probation(replace(record, update=update), at, refused, effects)

    def _judge_probation(self, record: Record, at: int, refused: Optional[Refusal], effects: tuple) -> tuple:
        if record.update.state != "probation":
            return record, effects
        verdict = (True, CONTRACT_REJECTED) if blames(refused, record.update) else probation_verdict(record.update, record.data)
        host_at = self._host_time()
        if verdict is None or host_at is None:
            return record, effects
        update, more = updates.reduce(record.update, updates.ProbationJudged(at, host_at, *verdict))
        return replace(record, update=update), effects + more

    def _update_settled(self, before: updates.Update, effects: tuple) -> None:
        """After a commit attempt: free the staging slot, tell the rollout, then send the requests."""
        after = self._record.update
        if after.state not in updates.STAGING_STATES:
            self.channel.release(self.server.id)
        if after != before:
            self.channel.reported(self.server.id, before, after)
        if (after.state == "in_sync" and self._transport == "gate" and self._agent.release_sha == after.desired
                and run_verdict(after.desired, self._record.data) == (False, None)):
            # A host already on the desired release with a good real run by it, such as a canary
            # bootstrapped onto the desired bundle: it counts as in sync for the rollout.
            self.channel.confirmed(self.server.id, after.desired)
        for effect in effects:
            self._start_update(effect)

    def _start_update(self, effect: updates.Effect) -> None:
        """Send one update request from a helper thread. Only a stage, which carries the bundle, waits for
        the fleet transfer semaphore; an activate or rollback request is a few bytes and goes out at once."""
        seq = self._next_seq()
        server, host, transfers, desired = self.server, self.host, self.transfers, self.channel.desired()

        def work() -> None:
            try:
                if isinstance(effect, updates.Stage):
                    if desired is None or desired.sha != effect.sha:
                        raise ValueError("the desired release changed")
                    bundle, signature = desired.read_bundle()
                    with transfers:
                        host.stage_update(server, effect.sha, bundle, signature)
                elif isinstance(effect, updates.Activate):
                    host.activate_update(server, effect.sha)
                else:
                    host.rollback_update(server, effect.sha)
            except TransportError as exc:
                self.post(UpdateSent(seq, effect, False, exc.code))
            except (OSError, ValueError):
                _LOG.exception("agent bundle unavailable for %s", server.id)
                self.post(UpdateSent(seq, effect, False, BUNDLE_UNAVAILABLE, contacted=False))
            except Exception:
                _LOG.exception("update request failed for %s", server.id)
                self.post(UpdateSent(seq, effect, False, "UPDATE_FAILED", contacted=False))
            else:
                self.post(UpdateSent(seq, effect, True))

        self.spawn(work, f"storage-viz-update-{server.id}")

    def _update_sent(self, message: UpdateSent) -> None:
        at = int(self.clock.time())
        record = self._record
        before = record.update
        if message.contacted:
            failed_contact = message.code in health.TRANSPORT_CODES | health.AUTH_CODES
            link_event = ExchangeFailed(message.seq, at, message.code) if failed_contact else ExchangeOk(message.seq, at)
            record = replace(record, link=health.reduce_link(record.link, link_event))
        effects: tuple = ()
        host_at = self._host_time()
        if host_at is not None:
            # A sent activate or rollback only queues the updater: its answer comes with a later observe.
            update, effects = updates.reduce(record.update, updates.Sent(at, host_at, message.effect, message.ok, message.code,
                                                                         permitted=self.channel.permits(self.server.id)))
            record = replace(record, update=update)
        committed = self._commit(record)
        self._update_settled(before, effects if committed else ())
        if committed:
            self._next_contact = min(self._next_contact, self.clock.monotonic() + judge.ACTIVE_TELEMETRY_INTERVAL_SECONDS)

    # ------------------------------------------------------------ rescan

    def _dispatch(self, job: jobs.Job) -> None:
        if job.protocol == "receipt":
            self._send_rescan(job.request_id)
        else:
            self._start_rescan()

    def _send_rescan(self, request_id: str) -> None:
        seq = self._next_seq()
        server, host = self.server, self.host

        def work() -> None:
            try:
                reply = host.rescan(server, request_id)
            except TransportError as exc:
                self.post(Replied(seq, "ambiguous", exc.code))
                return
            except Exception:
                _LOG.exception("rescan request failed for %s", server.id)
                self.post(Replied(seq, "ambiguous", "RESCAN_FAILED"))
                return
            outcome = reply.outcome if reply.outcome in jobs.REPLY_OUTCOMES else "ambiguous"
            self.post(Replied(seq, outcome, reply.code))

        self.spawn(work, f"storage-viz-rescan-{server.id}")

    def _replied(self, message: Replied) -> None:
        at = int(self.clock.time())
        record = self._record
        failed_contact = message.outcome == "ambiguous" and message.code in health.TRANSPORT_CODES | health.AUTH_CODES
        link_event = ExchangeFailed(message.seq, at, message.code) if failed_contact else ExchangeOk(message.seq, at)
        record = replace(record, link=health.reduce_link(record.link, link_event))
        if record.job is not None:
            job, _ = jobs.reduce(record.job, jobs.RescanReplied(at, message.outcome, message.code))
            record = self._with_job(record, job)
        if self._commit(record):
            self._next_contact = 0.0

    def _start_rescan(self) -> None:
        seq = self._next_seq()
        server, host = self.server, self.host

        def work() -> None:
            # SHIM(M2b..M3b) S2: today's sudo rule allows only the blocking oneshot start.
            try:
                host.start_rescan(server)
            except TransportError as exc:
                self.post(Dispatched(seq, False, exc.code))
                return
            except Exception:
                _LOG.exception("rescan start failed for %s", server.id)
                self.post(Dispatched(seq, False, "RESCAN_FAILED"))
                return
            self.post(Dispatched(seq, True))

        self.spawn(work, f"storage-viz-rescan-{server.id}")

    def _dispatched(self, message: Dispatched) -> None:
        at = int(self.clock.time())
        record = self._record
        failed_contact = message.code in health.TRANSPORT_CODES | health.AUTH_CODES
        link_event = ExchangeFailed(message.seq, at, message.code) if failed_contact else ExchangeOk(message.seq, at)
        record = replace(record, link=health.reduce_link(record.link, link_event))
        if record.job is not None:
            job, _ = jobs.reduce(record.job, jobs.DispatchReturned(at, message.ok, message.code))
            record = self._with_job(record, job)
        if self._commit(record):
            self._next_contact = 0.0


def _manifest_problem(manifest: HostManifest, held: Optional[HostManifest], server_id: str) -> Optional[str]:
    if manifest.server_id != server_id:
        return "IDENTITY_MISMATCH"
    # Only seq orders host data (I20). The record keeps the higher one and the link reports the regression.
    if held is not None and manifest.sha256 != held.sha256 and manifest.seq <= held.seq:
        return "MANIFEST_SEQ_REGRESSED"
    return None
