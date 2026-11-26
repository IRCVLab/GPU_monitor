"""ServerActor: the only writer of one server's record, store directory, SSH contact and job.

Every change goes through the pure reducers in ``health`` and ``jobs``. The actor
commits the new record before it runs the effects the reducers asked for, then
publishes a new immutable ``ServerView`` by one attribute assignment. Transfers
and the blocking rescan start run in helper threads that post their results back
to the inbox, so observes continue while they run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import logging
import queue
import threading
import time
from typing import Any, Callable, Mapping, Optional, Protocol

from collector import health, jobs, judge, snapshot
from collector.errors import StoreUnavailable
from collector.health import (
    CaptureAccepted, CaptureMismatch, CaptureRejected, ExchangeFailed, ExchangeOk, PublicationObserved, TransferFailed,
)
from collector.inventory import Server
from collector.store import DURATION_LIMIT, DURATION_PLANS, Record, ServerStore
from collector.transport import TransportError
from collector.view import ObservationView, ServerView, Snapshot, build_view, render_snapshot

_LOG = logging.getLogger(__name__)

# Transfer failures that condemn the published capture. Any other code is retried on a later observe.
_REJECT_CODES = frozenset({"SNAPSHOT_TOO_LARGE", "BAD_STATUS", "BAD_GENERATION"})
_MISMATCH_CODES = frozenset({"SIZE_MISMATCH"})
_MISMATCH_MESSAGES = ("byte_size does not match downloaded bytes", "sha256 does not match downloaded bytes")
TICK_FAILED = "TICK_FAILED"
MAX_TICK_BACKOFF_SECONDS = 10 * 60


class Clock(Protocol):
    def time(self) -> float: ...
    def monotonic(self) -> float: ...


class Host(Protocol):
    """What the actor asks of a storage server. ``legacy.LegacyHost`` speaks today's protocol."""
    def observe(self, server: Server) -> Any: ...
    def fetch_capture(self, server: Server, publication: Mapping[str, Any]) -> bytes: ...
    def start_rescan(self, server: Server) -> None: ...


def spawn_thread(work: Callable[[], None], name: str) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()


class Ask:
    """A reply slot with a bound. Once the asker gives up, the actor drops the request unseen."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._state = "open"
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

    def wait(self, timeout: float) -> Any:
        if self._done.wait(timeout):
            return self.value
        with self._lock:
            if self._state == "open":
                self._state = "cancelled"
                return None
        # The actor already took the request: its answer follows one record write.
        self._done.wait()
        return self.value


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
    sha: str
    generation: Optional[str]
    outcome: str
    """accepted, rejected, mismatch, retry or transport."""
    code: Optional[str] = None
    payload: Optional[Mapping[str, Any]] = None
    data: Optional[bytes] = None


@dataclass(frozen=True)
class Dispatched:
    seq: int
    ok: bool
    code: Optional[str] = None


STOP = object()


def _capture_event(message: Fetched, at: int) -> health.DataEvent:
    if message.outcome == "accepted":
        return CaptureAccepted(message.sha)
    if message.outcome == "rejected":
        return CaptureRejected(message.sha, message.generation, message.code, at)
    if message.outcome == "mismatch":
        return CaptureMismatch(message.sha, message.generation, at)
    return TransferFailed(message.sha)


def record_duration(durations: Mapping[str, Any], status: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep the last completed full and ssd scan durations for the ETA (today's rule)."""
    plan = status.get("scan_plan")
    duration = status.get("last_duration_sec")
    finished = status.get("finished_unix") or status.get("last_finished_unix")
    if (status.get("state") != "complete" or plan not in DURATION_PLANS
            or type(duration) is not int or duration < 0 or type(finished) is not int or finished <= 0):
        return durations
    entries = durations.get(plan, [])
    if any(recorded == finished for recorded, _ in entries):
        return durations
    return {**durations, plan: [list(entry) for entry in sorted([*entries, [finished, duration]])[-DURATION_LIMIT:]]}


class ServerActor:
    def __init__(self, server: Server, store: ServerStore, host: Host, *, clock: Clock = time,
                 transfers: Optional[threading.BoundedSemaphore] = None,
                 spawn: Callable[[Callable[[], None], str], None] = spawn_thread) -> None:
        self.server = server
        self.store = store
        self.host = host
        self.clock = clock
        self.transfers = transfers or threading.BoundedSemaphore(1)
        self.spawn = spawn
        self.inbox: "queue.Queue[Any]" = queue.Queue()
        self.view = ServerView(server=server)
        self.loaded = False
        self.started = threading.Event()
        """Set once the first load attempt has published a view, loaded or not."""
        self._record = Record()
        self._snapshot: Optional[Snapshot] = None
        self._observation: Optional[ObservationView] = None
        self._scan: Optional[jobs.Scan] = None
        self._host_clock = None
        self._error: Optional[str] = None
        self._seq = 0
        self._next_contact = 0.0
        self._failed_ticks = 0

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
        self._next_contact = self.clock.monotonic() + judge.IDLE_TELEMETRY_INTERVAL_SECONDS
        stored = self.store.load()
        if stored is None:
            stored, capture = self.store.import_legacy() or (Record(), None)
            self.store.commit(stored, capture)
        self._snapshot = self._load_snapshot(stored)
        record = stored
        if self._snapshot is None and record.data.accepted is not None:
            record = replace(record, data=replace(record.data, accepted=None))
        if record.job is not None:
            record = self._with_job(record, jobs.reduce(record.job, jobs.Restarted(int(self.clock.time())))[0])
        if record != stored:
            self.store.commit(record)
        self._record = record
        self._seq = record.link.seq
        self._error = None
        self.loaded = True
        self._next_contact = 0.0

    def _load_snapshot(self, record: Record) -> Optional[Snapshot]:
        sha = record.data.accepted
        if sha is None:
            return None
        try:
            return render_snapshot(json.loads(self.store.read_capture(sha)))
        except (StoreUnavailable, ValueError):
            _LOG.warning("stored capture for %s is unreadable; it will be fetched again", self.server.id)
            return None

    def _commit(self, record: Record, capture: Optional[tuple] = None) -> bool:
        if record == self._record and capture is None:
            return True
        try:
            self.store.commit(record, capture)
        except StoreUnavailable:
            _LOG.exception("commit failed for %s", self.server.id)
            self._error = StoreUnavailable.code
            return False
        self._record = record
        if self._error == StoreUnavailable.code:
            self._error = None
        return True

    def _publish(self) -> None:
        self.view = build_view(self.server, self._record, self._snapshot, self._observation, self._host_clock, self._error)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _with_job(record: Record, job: jobs.Job) -> Record:
        if job.state in jobs.TERMINAL:
            return replace(record, job=None, last_job=job)
        return replace(record, job=job)

    def _job_observed(self, record: Record, held: Optional[Snapshot]) -> tuple:
        if self._scan is None:
            return record.job, ()
        rejected = frozenset(entry.get("generation") for entry in record.data.rejected.values())
        accepted = held.overview.get("scan_generation") if held is not None else None
        event = jobs.Observed(int(self.clock.time()), self._scan, accepted, rejected)
        return jobs.reduce(record.job, event)

    # ------------------------------------------------------------ messages

    def _handle(self, message: Any) -> None:
        if isinstance(message, Admit):
            self._admit(message)
        elif isinstance(message, Fetched):
            self._fetched(message)
        elif isinstance(message, Dispatched):
            self._dispatched(message)
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
        self._host_clock = (observation.host_now, received)
        self._observation = ObservationView({**observation.status, "observed_unix": at}, received)
        self._scan = observation.scan
        record = self._record
        if observation.publication_error is None:
            link_event = ExchangeOk(seq, at)
            data, fetches = health.reduce_data(record.data, PublicationObserved(observation.publication))
        else:
            link_event = ExchangeFailed(seq, at, observation.publication_error)
            data, fetches = record.data, ()
        record = replace(record, link=health.reduce_link(record.link, link_event), data=data,
                         durations=record_duration(record.durations, observation.status))
        dispatches: tuple = ()
        if record.job is not None:
            job, dispatches = self._job_observed(record, self._snapshot)
            record = self._with_job(record, job)
        self._schedule(record.job is not None or observation.status.get("state") == "running")
        if not self._commit(record):
            return
        for fetch in fetches:
            self._start_transfer(fetch.manifest)
        for _ in dispatches:
            self._start_rescan()

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
        job = jobs.admit(message.request_id, message.actor, message.at)
        if not self._commit(replace(record, job=job)):
            return Admitted(error=StoreUnavailable.code)
        self._publish()
        # Decide on a fresh observation: start the scan, wait for the running one, or tie to it.
        self._next_contact = 0.0
        return Admitted(job=job)

    # ------------------------------------------------------------ transfers

    def _start_transfer(self, manifest: Mapping[str, Any]) -> None:
        seq = self._next_seq()
        sha = manifest["sha256"]
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
                self.post(Fetched(seq, sha, generation, outcome, exc.code))
                return
            except Exception:
                _LOG.exception("capture transfer failed for %s", server.id)
                self.post(Fetched(seq, sha, generation, "retry", "TRANSFER_FAILED"))
                return
            desired = snapshot.DesiredServer(server_id=server.id, config_digest=server.scanner_digest)
            try:
                valid = snapshot.validate_download(manifest, data, desired)
            except Exception as exc:
                mismatch = str(exc) in _MISMATCH_MESSAGES
                self.post(Fetched(seq, sha, generation, "mismatch" if mismatch else "rejected",
                                  "SHA_MISMATCH" if mismatch else "INVALID_SNAPSHOT"))
                return
            self.post(Fetched(seq, sha, generation, "accepted", payload=valid.payload, data=data))

        self.spawn(work, f"storage-viz-transfer-{server.id}")

    def _fetched(self, message: Fetched) -> None:
        at = int(self.clock.time())
        record = self._record
        try:
            link_event = ExchangeFailed(message.seq, at, message.code) if message.outcome == "transport" else ExchangeOk(message.seq, at)
            data, _ = health.reduce_data(record.data, _capture_event(message, at))
            record = replace(record, link=health.reduce_link(record.link, link_event), data=data)
            accepted = message.outcome == "accepted" and data.accepted == message.sha != self._record.data.accepted
            held = render_snapshot(message.payload) if accepted else self._snapshot
            if record.job is not None and record.job.state == "collecting":
                job, _ = self._job_observed(record, held)
                record = self._with_job(record, job)
        except Exception:
            self._drop_fetch(message.sha)
            raise
        if not self._commit(record, (message.sha, message.data) if accepted else None):
            self._drop_fetch(message.sha)
            return
        self._snapshot = held

    def _drop_fetch(self, sha: str) -> None:
        """The transfer's result was not applied, so the next observe fetches the capture again."""
        data, _ = health.reduce_data(self._record.data, TransferFailed(sha))
        self._record = replace(self._record, data=data)

    # ------------------------------------------------------------ rescan

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
