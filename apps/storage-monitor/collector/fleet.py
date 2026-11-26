"""Fleet: one ServerActor per enabled server, the views HTTP reads, and rescan admission.

The fleet lock guards only the global admission count. It is never held across
SSH, a store write or an actor reply.
"""
from __future__ import annotations

import fcntl
import logging
import os
import pathlib
import threading
import time
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from collector import jobs
from collector.actor import STOP, Admit, Ask, Clock, Host, ServerActor, spawn_thread
from collector.errors import (
    ActiveJob, Cooldown, GlobalConcurrency, RescanUnavailable, ServerBusy, StoreUnavailable, UnknownServer,
)
from collector.ids import SERVER_ID_RE
from collector.inventory import Server
from collector.store import ServerStore
from collector.view import ServerView, active_job, job_v1

_LOG = logging.getLogger(__name__)

MAX_PARALLEL_TRANSFERS = 2
REPLY_TIMEOUT_SECONDS = 5.0
STARTUP_LOAD_SECONDS = 30.0


class Fleet:
    def __init__(self, servers: Iterable[Server], state_root: str | os.PathLike, host: Host, *,
                 clock: Clock = time, cooldown_seconds: int = jobs.COOLDOWN_SECONDS,
                 max_active_jobs: int = jobs.MAX_ACTIVE_JOBS, reply_timeout: float = REPLY_TIMEOUT_SECONDS,
                 spawn: Callable[[Callable[[], None], str], None] = spawn_thread) -> None:
        if not 0 <= cooldown_seconds <= 24 * 3600 or not 1 <= max_active_jobs <= 16:
            raise ValueError("rescan cooldown or concurrency is out of range")
        self.servers: Tuple[Server, ...] = tuple(server for server in servers if server.enabled)
        self.state_root = pathlib.Path(state_root)
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self.max_active_jobs = max_active_jobs
        self.reply_timeout = reply_timeout
        transfers = threading.BoundedSemaphore(MAX_PARALLEL_TRANSFERS)
        self.actors: Dict[str, ServerActor] = {
            server.id: ServerActor(server, ServerStore(self.state_root, server.id), host,
                                   clock=clock, transfers=transfers, spawn=spawn)
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

    def views(self) -> List[ServerView]:
        return [self.actors[server.id].view for server in self.servers]

    # ------------------------------------------------------------ rescan

    def request_rescan(self, server_id: Any, actor_name: str) -> dict:
        server = self.lookup(server_id)
        now = int(self.clock.time())
        audit = _audit(now, actor_name, server.id)
        current = active_job(self.actors[server.id].view)
        if current is not None:
            raise ActiveJob(job=job_v1(current, server.id), audit=audit("ACTIVE_JOB", current.request_id))
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
            raise ActiveJob(job=job_v1(reply.job, server.id), audit=audit("ACTIVE_JOB", reply.job.request_id))
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
        return {"status": "started", "job": job_v1(reply.job, server.id), "audit": event}


def _audit(now: int, actor_name: str, server_id: str) -> Callable[[str, Optional[str]], dict]:
    def event(result_code: str, job_id: Optional[str]) -> dict:
        return {"timestamp_unix": now, "actor": _safe(actor_name), "server_id": server_id,
                "result_code": result_code, "job_id": job_id or "none"}
    return event


def _safe(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "_.:-" else "_" for ch in str(value))[:128].strip("._:-")
    return text or "unknown"
