"""The durable rescan job: a pure reducer over host scan observations.

A job succeeds only when the publication its scan produced is accepted, never on an
SSH exit status alone (I24). Anything that cannot be tied to the job ends ``unconfirmed``.

A ``receipt`` job (a v2 host) sends ``rescan <request_id>`` and follows that id: in the
request mailbox, in run.json, then as a receipt in the manifest (design §3.3).

"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Optional, Tuple, Union

COOLDOWN_SECONDS = 15 * 60
MAX_ACTIVE_JOBS = 2
DISPATCH_DEADLINE_SECONDS = 10 * 60
RECONCILE_DEADLINE_SECONDS = 10 * 60
COLLECT_DEADLINE_SECONDS = 15 * 60
MIN_RUN_DEADLINE_SECONDS = 2 * 3600
MAX_DISPATCHES = 3
RESEND_INTERVAL_SECONDS = 60
"""The idle observe interval: a receipt job sends its id again at most this often."""

STATES = ("dispatching", "pending", "running", "reconciling", "collecting", "succeeded", "failed", "unconfirmed")
TERMINAL = frozenset({"succeeded", "failed", "unconfirmed"})
RECEIPT_SUCCEEDS = frozenset({"complete", "partial"})
RECEIPT_FAILS = {"failed": "SCAN_FAILED", "interrupted": "SCAN_INTERRUPTED"}
REPLY_OUTCOMES = frozenset({"received", "already_received", "rejected"})


@dataclass(frozen=True)
class Job:
    request_id: str
    actor: str
    requested_unix: int
    since_unix: int
    state: str = "dispatching"
    dispatches: int = 0
    finished_unix: Optional[int] = None
    result_code: Optional[str] = None
    last_dispatch_unix: Optional[int] = None
    """When a receipt job last sent its id, so resends are spaced in time."""


@dataclass(frozen=True)
class Traced:
    """What one observation of a v2 host shows of the job's request id."""
    at: int
    pending: bool = False
    """The id waits in ``requests/``: delivered, so never sent again."""
    running: bool = False
    """run.json lists the id: the running scan serves it."""
    receipt: Optional[str] = None
    """The outcome of the manifest receipt for the id, as written. Unknown outcomes are ignored."""
    captures: str = "current"
    """The accepted manifest's captures: current, fetching or rejected."""


@dataclass(frozen=True)
class RescanReply:
    """The host's answer to ``rescan <request_id>``. A timeout or unparsable answer is a TransportError."""
    outcome: str
    """received, already_received or rejected."""
    code: Optional[str] = None


@dataclass(frozen=True)
class RescanReplied:
    at: int
    outcome: str
    """received, already_received, rejected, or ambiguous (timeout, exit 255, unparsable reply)."""
    code: Optional[str] = None


@dataclass(frozen=True)
class Tick:
    at: int
    run_deadline_seconds: int = MIN_RUN_DEADLINE_SECONDS


@dataclass(frozen=True)
class Restarted:
    at: int


@dataclass(frozen=True)
class Dispatch:
    pass


Event = Union[Traced, RescanReplied, Tick, Restarted]
EVENT_KINDS = ("Traced", "RescanReplied", "Tick", "Restarted")
Result = Tuple[Job, Tuple[Dispatch, ...]]


def admit(request_id: str, actor: str, at: int) -> Job:
    return Job(request_id=request_id, actor=actor, requested_unix=at, since_unix=at)


def run_deadline_seconds(longest_sec: Optional[int]) -> int:
    return max(MIN_RUN_DEADLINE_SECONDS, 3 * longest_sec) if longest_sec else MIN_RUN_DEADLINE_SECONDS


def job_deadline_seconds(run_deadline: int) -> int:
    """Time from admission to a terminal state: a scheduled scan to wait out, our own run, then the pull."""
    return 2 * run_deadline + RECONCILE_DEADLINE_SECONDS + COLLECT_DEADLINE_SECONDS


def _to(job: Job, state: str, at: int, **fields) -> Job:
    return replace(job, state=state, since_unix=at if state != job.state else job.since_unix, **fields)


def _finish(job: Job, state: str, at: int, code: str) -> Job:
    return _to(job, state, at, finished_unix=at, result_code=code)


def _ignore(job: Job, event: Event) -> Result:
    return job, ()


def _after(seconds: Callable[[Job, Tick], int], state: str, code: str):
    """End the state after its own wait, and any state at the job deadline that no transition moves."""
    def handler(job: Job, event: Tick) -> Result:
        if (event.at - job.since_unix < seconds(job, event)
                and event.at - job.requested_unix < job_deadline_seconds(event.run_deadline_seconds)):
            return job, ()
        return _finish(job, state, event.at, code), ()
    return handler


def _to_reconciling(job: Job, event: Event) -> Result:
    return _to(job, "reconciling", event.at), ()


def _restart_dispatching(job: Job, event: Restarted) -> Result:
    # The start that was in flight died with the process; whether it reached the host is unknown.
    return _to_reconciling(job, event) if job.dispatches else (job, ())


# ------------------------------------------------------------------ receipt jobs (v2 hosts)


def _send(job: Job, event: Traced) -> Result:
    return replace(job, dispatches=job.dispatches + 1, last_dispatch_unix=event.at), (Dispatch(),)


def _resend(job: Job, event: Traced) -> Result:
    """The id shows nowhere on the host: send it again, at most once per idle observe interval.

    The host answers already_received for an id it holds, so a resend never scans twice.
    After the last send, a job the host accepted
    is RESCAN_NOT_STARTED, and one it never acknowledged is unconfirmed.
    """
    if job.last_dispatch_unix is not None and event.at - job.last_dispatch_unix < RESEND_INTERVAL_SECONDS:
        return job, ()
    if job.dispatches >= MAX_DISPATCHES:
        if job.state == "reconciling":
            return _finish(job, "unconfirmed", event.at, "NO_TRACE"), ()
        return _finish(job, "failed", event.at, "RESCAN_NOT_STARTED"), ()
    return _send(job, event)


def _receipt(job: Job, event: Traced) -> Optional[Result]:
    """Decide on the manifest receipt for the id, if there is one the job understands."""
    if event.receipt in RECEIPT_FAILS:
        return _finish(job, "failed", event.at, RECEIPT_FAILS[event.receipt]), ()
    if event.receipt not in RECEIPT_SUCCEEDS:
        return None
    collecting = _to(job, "collecting", event.at)
    if event.captures == "rejected":
        return _finish(collecting, "failed", event.at, "CAPTURE_REJECTED"), ()
    if event.captures == "current":
        return _finish(collecting, "succeeded", event.at, "OK"), ()
    return collecting, ()


def _traced(job: Job, event: Traced, *, unseen: Callable[[Job, Traced], Result]) -> Result:
    """The receipt, then run.json, then the mailbox decide. ``unseen`` handles no trace of the id."""
    decided = _receipt(job, event)
    if decided is not None:
        return decided
    if event.running:
        return _to(job, "running", event.at), ()
    if event.pending:
        # Delivered and waiting: the host starts it, or the scan holding the lock serves it (C4).
        return _to(job, "pending", event.at), ()
    return unseen(job, event)


def _dispatching_traced(job: Job, event: Traced) -> Result:
    if job.dispatches == 0:
        return _send(job, event)
    return _traced(job, event, unseen=_ignore)  # the reply to the send in flight decides


def _pending_traced(job: Job, event: Traced) -> Result:
    return _traced(job, event, unseen=_resend)


def _running_traced(job: Job, event: Traced) -> Result:
    return _receipt(job, event) or (job, ())


def _reconciling_traced(job: Job, event: Traced) -> Result:
    return _traced(job, event, unseen=_resend)


def _collecting_traced(job: Job, event: Traced) -> Result:
    return _receipt(job, event) or (job, ())


def _replied(on_received: Callable[[Job, RescanReplied], Result]):
    def handler(job: Job, event: RescanReplied) -> Result:
        if event.outcome == "rejected":
            return _finish(job, "failed", event.at, event.code or "RESCAN_REJECTED"), ()
        if event.outcome in ("received", "already_received"):
            return on_received(job, event)
        # Ambiguous: the request may or may not have reached the host. Observation decides.
        return (_to(job, "reconciling", event.at), ()) if job.state == "dispatching" else (job, ())
    return handler


def _to_pending(job: Job, event: Event) -> Result:
    return _to(job, "pending", event.at), ()


_RUN_DEADLINE = lambda job, event: event.run_deadline_seconds  # noqa: E731

TRANSITIONS: Dict[Tuple[str, str], Callable[[Job, Event], Result]] = {
    ("dispatching", "Traced"): _dispatching_traced,
    ("dispatching", "RescanReplied"): _replied(_to_pending),
    ("dispatching", "Tick"): _after(lambda job, event: DISPATCH_DEADLINE_SECONDS, "unconfirmed", "NO_TRACE"),
    ("dispatching", "Restarted"): _restart_dispatching,
    ("pending", "Traced"): _pending_traced,
    ("pending", "RescanReplied"): _replied(_ignore),
    ("pending", "Tick"): _after(_RUN_DEADLINE, "failed", "RESCAN_NOT_STARTED"),
    ("pending", "Restarted"): _ignore,
    ("running", "Traced"): _running_traced,
    ("running", "RescanReplied"): _ignore,
    ("running", "Tick"): _after(_RUN_DEADLINE, "failed", "RESCAN_TIMEOUT"),
    ("running", "Restarted"): _ignore,
    ("reconciling", "Traced"): _reconciling_traced,
    ("reconciling", "RescanReplied"): _replied(_to_pending),
    ("reconciling", "Tick"): _after(lambda job, event: RECONCILE_DEADLINE_SECONDS, "unconfirmed", "NO_TRACE"),
    ("reconciling", "Restarted"): _ignore,
    ("collecting", "Traced"): _collecting_traced,
    ("collecting", "RescanReplied"): _ignore,
    ("collecting", "Tick"): _after(lambda job, event: COLLECT_DEADLINE_SECONDS, "failed", "PULL_FAILED"),
    ("collecting", "Restarted"): _ignore,
    **{(state, kind): _ignore for state in TERMINAL for kind in EVENT_KINDS},
}


def reduce(job: Job, event: Event) -> Result:
    return TRANSITIONS[(job.state, type(event).__name__)](job, event)
