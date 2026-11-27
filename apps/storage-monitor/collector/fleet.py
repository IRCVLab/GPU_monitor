"""Fleet: one ServerActor per enabled server, the views HTTP reads, rescan admission and the agent rollout.

The fleet lock guards only the global admission count, and the rollout lock only
the rollout state and its staging slots. Neither is held across SSH or an actor reply.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import fcntl
import hashlib
import json
import logging
import os
import pathlib
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from collector import jobs, rollout
from collector.actor import STOP, Admit, Ask, Channel, Clock, Host, ServerActor, spawn_thread
from collector.errors import (
    ActiveJob, Cooldown, GlobalConcurrency, RescanUnavailable, ServerBusy, StoreUnavailable, UnknownServer,
)
from collector.ids import RELEASE_SHA_RE, SERVER_ID_RE, SHA256_RE
from collector.inventory import Server, canary_of
from collector.store import ServerStore
from collector.view import ServerView, active_job, job_body

_LOG = logging.getLogger(__name__)

MAX_PARALLEL_TRANSFERS = 2
REPLY_TIMEOUT_SECONDS = 5.0
STARTUP_LOAD_SECONDS = 30.0
DESIRED_AGENT_PATH = pathlib.Path(__file__).resolve().parents[1] / "agent" / "storage-viz-agent.json"
"""The agent bundle metadata of the active dashboard release, next to the running code (release puller)."""
MAX_METADATA_BYTES = 4096
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_SIGNATURE_BYTES = 4096


@dataclass(frozen=True)
class DesiredAgent:
    """The agent release the dashboard release carries: ``storage-viz-agent.json`` and its bundle beside it."""
    sha: str
    release_seq: int
    sha256: str
    size: int
    directory: pathlib.Path

    def read_bundle(self) -> Tuple[bytes, bytes]:
        """The bundle and its signature, exactly as the metadata describes them; ValueError otherwise."""
        bundle = _read_bounded(self.directory / "storage-viz-agent.tar.gz", MAX_BUNDLE_BYTES)
        signature = _read_bounded(self.directory / "storage-viz-agent.tar.gz.sig", MAX_SIGNATURE_BYTES)
        if len(bundle) != self.size or hashlib.sha256(bundle).hexdigest() != self.sha256 or not signature:
            raise ValueError("the agent bundle does not match its metadata")
        return bundle, signature


def _read_bounded(path: pathlib.Path, cap: int) -> bytes:
    with open(path, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"{path.name} is too large")
    return data


def load_desired_agent(path: pathlib.Path) -> Optional[DesiredAgent]:
    """The desired agent release, or None: no metadata means no desired agent, and the update tracks stay idle."""
    try:
        raw = json.loads(_read_bounded(path, MAX_METADATA_BYTES))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        _LOG.error("agent bundle metadata %s is unreadable, no agent release is desired: %s", path, exc)
        return None
    valid = (isinstance(raw, dict) and raw.get("application") == "storage-viz-agent"
             and isinstance(raw.get("source_sha"), str) and RELEASE_SHA_RE.fullmatch(raw["source_sha"])
             and type(raw.get("release_seq")) is int and raw["release_seq"] >= 0
             and isinstance(raw.get("sha256"), str) and SHA256_RE.fullmatch(raw["sha256"])
             and type(raw.get("size")) is int and 0 < raw["size"] <= MAX_BUNDLE_BYTES)
    if not valid:
        _LOG.error("agent bundle metadata %s is malformed, no agent release is desired", path)
        return None
    return DesiredAgent(raw["source_sha"], raw["release_seq"], raw["sha256"], raw["size"], path.parent)


class Rollout:
    """The fleet's side of the agent update channel (``actor.Channel``): the one writer of rollout.json.

    It re-reads the desired release when its metadata file changes (a cheap stat on every call),
    and a changed sha is DesiredChanged. Staging slots: the canary alone in the canary phase, at
    most ``rollout.MAX_STAGING`` hosts at once in the fleet phase, none while halted.
    """

    def __init__(self, state_root: pathlib.Path, servers: Iterable[Server], desired_path: pathlib.Path) -> None:
        self.path = state_root / "rollout.json"
        self.desired_path = desired_path
        self._canary = canary_of(tuple(servers))
        self._lock = threading.Lock()
        self._staging: set = set()
        self._stamp: Any = ()
        self._desired: Optional[DesiredAgent] = None
        self._state: Optional[rollout.RolloutState] = None

    def _current(self) -> rollout.RolloutState:
        """The state with the desired release refreshed. Call with the lock held."""
        if self._state is None:
            self._state = replace(self._load(), canary=self._canary)  # the inventory decides the canary
        try:
            st = os.stat(self.desired_path)
            stamp: Any = (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            stamp = None
        if stamp != self._stamp:
            self._stamp = stamp
            self._desired = load_desired_agent(self.desired_path) if stamp is not None else None
        sha = self._desired.sha if self._desired is not None else None
        if sha != self._state.desired_sha:
            seq = self._desired.release_seq if self._desired is not None else None
            self._apply(rollout.DesiredChanged(sha, seq))
        return self._state

    def _load(self) -> rollout.RolloutState:
        try:
            return rollout.parse(self.path.read_bytes())
        except FileNotFoundError:
            return rollout.RolloutState()
        except (OSError, ValueError) as exc:
            # Starting over means the canary gates the desired sha again: never less safe than the lost state.
            _LOG.error("rollout.json is unreadable, starting from the canary phase: %s", exc)
            return rollout.RolloutState()

    def _apply(self, event: rollout.Event) -> None:
        state = rollout.reduce(self._state, event)
        if state == self._state:
            return
        self._state = state
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            tmp = self.path.with_name(f".rollout.json.{os.getpid()}.tmp")
            with open(tmp, "wb") as handle:
                handle.write(rollout.encode(state))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError:
            _LOG.exception("rollout.json not written; the rollout state lives in memory until the next change")
        _LOG.info("agent rollout %s on %s: %s", state.phase, state.desired_sha, state.reason or type(event).__name__)

    def view(self) -> rollout.RolloutState:
        with self._lock:
            return self._current()

    def desired(self) -> Optional[DesiredAgent]:
        with self._lock:
            self._current()
            return self._desired

    def acquire(self, server_id: str) -> bool:
        with self._lock:
            state = self._current()
            if server_id in self._staging:
                return True
            if not rollout.allows(state, server_id) or len(self._staging) >= rollout.MAX_STAGING:
                return False
            self._staging.add(server_id)
            return True

    def hold(self, server_id: str) -> None:
        with self._lock:
            self._staging.add(server_id)

    def release(self, server_id: str) -> None:
        with self._lock:
            self._staging.discard(server_id)

    def reported(self, server_id: str, before: Any, after: Any) -> None:
        event = rollout.host_event(server_id, before, after)
        if event is not None:
            with self._lock:
                self._current()
                self._apply(event)


class Fleet:
    def __init__(self, servers: Iterable[Server], state_root: str | os.PathLike, host: Host, *,
                 clock: Clock = time, cooldown_seconds: int = jobs.COOLDOWN_SECONDS,
                 max_active_jobs: int = jobs.MAX_ACTIVE_JOBS, reply_timeout: float = REPLY_TIMEOUT_SECONDS,
                 spawn: Callable[[Callable[[], None], str], None] = spawn_thread,
                 channel: Optional[Channel] = None, desired_path: pathlib.Path = DESIRED_AGENT_PATH) -> None:
        if not 0 <= cooldown_seconds <= 24 * 3600 or not 1 <= max_active_jobs <= 16:
            raise ValueError("rescan cooldown or concurrency is out of range")
        self.servers: Tuple[Server, ...] = tuple(server for server in servers if server.enabled)
        self.state_root = pathlib.Path(state_root)
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.max_active_jobs = max_active_jobs
        self.reply_timeout = reply_timeout
        transfers = threading.BoundedSemaphore(MAX_PARALLEL_TRANSFERS)
        self.rollout = Rollout(self.state_root, self.servers, pathlib.Path(desired_path))
        channel = channel or self.rollout
        self.actors: Dict[str, ServerActor] = {
            server.id: ServerActor(server, ServerStore(self.state_root, server.id), host,
                                   clock=clock, transfers=transfers, spawn=spawn, channel=channel)
            for server in self.servers
        }
        self._lock = threading.Lock()
        self._admitting: set = set()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._singleton: Optional[int] = None

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Take the process singleton lock and start one thread per actor."""
        self.state_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.state_root / "dashboard.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SystemExit(f"another dashboard already owns {self.state_root}") from None
        self._singleton = fd
        self._stop.clear()
        for actor in self.actors.values():
            thread = threading.Thread(target=actor.run, args=(self._stop,), name=f"storage-viz-actor-{actor.server.id}", daemon=True)
            self._threads.append(thread)
            thread.start()
        # Serve the stored data from the first request on. A store that stalls only delays startup by the bound.
        deadline = time.monotonic() + STARTUP_LOAD_SECONDS
        for actor in self.actors.values():
            actor.started.wait(max(0.0, deadline - time.monotonic()))

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for actor in self.actors.values():
            actor.post(STOP)
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._threads = []
        if self._singleton is not None:
            os.close(self._singleton)
            self._singleton = None

    # ------------------------------------------------------------ reads

    def lookup(self, server_id: Any) -> Server:
        if not isinstance(server_id, str) or not SERVER_ID_RE.fullmatch(server_id) or server_id not in self.actors:
            raise UnknownServer("server id is not in enabled inventory")
        return self.actors[server_id].server

    def view(self, server_id: Any) -> ServerView:
        self.lookup(server_id)
        return self.actors[server_id].view

    def open_capture(self, sid, vid, sha):
        return self.actors[sid].store.open_capture(vid, sha)

    def views(self) -> List[ServerView]:
        return [self.actors[server.id].view for server in self.servers]

    def desired_agent(self) -> Optional[DesiredAgent]:
        return self.rollout.desired()

    def rollout_view(self) -> dict:
        """Rollout state published by the fleet route."""
        state = self.rollout.view()
        return {"phase": state.phase, "reason": state.reason, "desired_sha": state.desired_sha,
                "release_seq": state.release_seq, "canary": state.canary,
                "passed": [{"sha": entry.sha, "release_seq": entry.release_seq} for entry in state.passed]}

    # ------------------------------------------------------------ rescan

    def request_rescan(self, server_id: Any, actor_name: str) -> dict:
        server = self.lookup(server_id)
        now = int(self.clock.time())
        audit = _audit(now, actor_name, server.id)
        current = active_job(self.actors[server.id].view)
        if current is not None:
            raise ActiveJob(job=job_body(current), audit=audit("ACTIVE_JOB", current.request_id))
        with self._lock:
            if server.id in self._admitting:
                raise ActiveJob(audit=audit("ACTIVE_JOB", None))
            busy = sum(1 for view in self.views() if active_job(view) is not None) + len(self._admitting)
            if busy >= self.max_active_jobs:
                raise GlobalConcurrency(audit=audit("GLOBAL_CONCURRENCY", None))
            self._admitting.add(server.id)

        def release() -> None:
            with self._lock:
                self._admitting.discard(server.id)

        # The reservation lasts until the admission is final, even when this request stops waiting
        # first: an admission the actor already took still writes its job (C5).
        ask = Ask(settled=release)
        self.actors[server.id].post(Admit(uuid.uuid4().hex, actor_name, now, self.cooldown_seconds, ask))
        reply = ask.wait(self.reply_timeout)
        if reply is None:
            raise ServerBusy(audit=audit("SERVER_BUSY", None))
        if reply.error == "ACTIVE_JOB":
            raise ActiveJob(job=job_body(reply.job), audit=audit("ACTIVE_JOB", reply.job.request_id))
        if reply.error == "COOLDOWN":
            raise Cooldown(reply.retry_after, retry_after_seconds=reply.retry_after, audit=audit("COOLDOWN", None))
        if reply.error == StoreUnavailable.code:
            raise StoreUnavailable(audit=audit(reply.error, None))
        if reply.error == RescanUnavailable.code:
            raise RescanUnavailable(audit=audit(reply.error, None))
        if reply.error is not None:
            # The edge answers 500 INTERNAL and logs this under the request id (section 4.3).
            raise RuntimeError(f"rescan admission for {server.id} failed: {reply.error}")
        event = audit("ACCEPTED", reply.job.request_id)
        _LOG.info("rescan accepted %s", event)
        return {"status": "started", "job": job_body(reply.job), "audit": event}


def _audit(now: int, actor_name: str, server_id: str) -> Callable[[str, Optional[str]], dict]:
    def event(result_code: str, job_id: Optional[str]) -> dict:
        return {"timestamp_unix": now, "actor": _safe(actor_name), "server_id": server_id,
                "result_code": result_code, "job_id": job_id or "none"}
    return event


def _safe(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in str(value))[:128].strip("._:-")
    return text or "unknown"
