"""The durable rescan job: a pure reducer over host scan observations.

A job succeeds only when the publication its scan produced is accepted, never on an
SSH exit status alone (I24). Anything that cannot be tied to the job ends ``unconfirmed``.

A ``receipt`` job (a v2 host) sends ``rescan <request_id>`` and follows that id: in the
request mailbox, in run.json, then as a receipt in the manifest (design §3.3).

SHIM(M2b..M5c) S4: an ``invocation`` job (a v1 host, which writes no receipts)
correlates by the scan unit's systemd InvocationID and the agent run id in its
lifecycle file.
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
FINISHED_RUN = frozenset({"complete", "partial", "failed"})
PROTOCOLS = ("invocation", "receipt")
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
    baseline_run: Optional[str] = None
    """The agent run the observation before the last start showed; any other run started after it."""
    invocation: Optional[str] = None
    """InvocationID of the scan unit the job is tied to."""
    agent_run: Optional[str] = None
    """Agent run id seen while that invocation ran."""
    generation: Optional[str] = None
    """The publication the job waits to see accepted."""
    finished_unix: Optional[int] = None
    result_code: Optional[str] = None
    protocol: str = "invocation"
    """``receipt`` for a v2 host; ``invocation`` is SHIM(M2b..M5c) S4."""
    last_dispatch_unix: Optional[int] = None
    """When a receipt job last sent its id, so resends are spaced in time."""


@dataclass(frozen=True)
class Scan:
    """Host scan facts from one observation."""
    invocation: Optional[str] = None
    """InvocationID of the active scan unit; None when no scan unit is active."""
    full: bool = False
    """The active scan covers every root, so it satisfies a rescan request."""
    run_id: Optional[str] = None
    run_state: Optional[str] = None
    run_full: bool = False
    generation: Optional[str] = None
    error_code: Optional[str] = None


@dataclass(frozen=True)
class Observed:
    at: int
    scan: Scan
    accepted: Optional[str] = None
    """Generation of the capture the store holds."""
    rejected: frozenset = frozenset()
    """Generations the data reducer rejected."""


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
class DispatchReturned:
    """The blocking ``systemctl start`` returned. Its status proves nothing on its own."""
    at: int
    ok: bool
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


Event = Union[Observed, DispatchReturned, Traced, RescanReplied, Tick, Restarted]
EVENT_KINDS = ("Observed", "DispatchReturned", "Traced", "RescanReplied", "Tick", "Restarted")
Result = Tuple[Job, Tuple[Dispatch, ...]]


def admit(request_id: str, actor: str, at: int, protocol: str = "invocation") -> Job:
    """The baseline run comes from the first observation after admission, never from an older one."""
    return Job(request_id=request_id, actor=actor, requested_unix=at, since_unix=at, protocol=protocol)


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


def _new_run(job: Job, scan: Scan) -> Optional[str]:
    return scan.run_id if scan.run_id is not None and scan.run_id != job.baseline_run else None


def _correlate(job: Job, event: Observed) -> Job:
    scan = event.scan
    if scan.run_state == "running":
        return _to(job, "running", event.at, invocation=scan.invocation, agent_run=_new_run(job, scan))
    # The unit has not written its lifecycle yet, so the run shown finished before it started.
    return _to(job, "running", event.at, invocation=scan.invocation, agent_run=None, baseline_run=scan.run_id)


def _collect(job: Job, event: Observed) -> Result:
    if job.generation is not None and job.generation == event.accepted:
        return _finish(job, "succeeded", event.at, "OK"), ()
    if job.generation in event.rejected:
        return _finish(job, "failed", event.at, "CAPTURE_REJECTED"), ()
    return job, ()


def _run_ended(job: Job, event: Observed) -> Result:
    """The tied scan ended. Its lifecycle tells how, unless a newer run already replaced it."""
    scan = event.scan
    if (job.agent_run or _new_run(job, scan)) != scan.run_id or scan.run_state not in FINISHED_RUN:
        return _finish(job, "unconfirmed", event.at, "RUN_UNCORRELATED"), ()
    if scan.run_state == "failed":
        return _finish(job, "failed", event.at, scan.error_code or "SCAN_FAILED"), ()
    return _collect(_to(job, "collecting", event.at, agent_run=scan.run_id, generation=scan.generation), event)


def _dispatch(job: Job, event: Observed) -> Result:
    """Start the scan unit. No unit is active, so the run this observation shows is not ours."""
    if job.dispatches >= MAX_DISPATCHES:
        return _finish(job, "failed", event.at, "RESCAN_NOT_STARTED"), ()
    return _to(job, "dispatching", event.at, baseline_run=event.scan.run_id, dispatches=job.dispatches + 1), (Dispatch(),)


def _dispatching_observed(job: Job, event: Observed) -> Result:
    scan = event.scan
    if scan.invocation is not None:
        return (_correlate(job, event), ()) if scan.full else (_to(job, "pending", event.at), ())
    if job.dispatches == 0:
        return _dispatch(job, event)
    return job, ()


def _pending_observed(job: Job, event: Observed) -> Result:
    scan = event.scan
    if scan.invocation is not None:
        return (_correlate(job, event), ()) if scan.full else (job, ())
    return _dispatch(job, event)


def _running_observed(job: Job, event: Observed) -> Result:
    scan = event.scan
    if scan.invocation != job.invocation:
        return _run_ended(job, event)
    if job.agent_run is None and scan.run_state == "running" and _new_run(job, scan):
        return replace(job, agent_run=scan.run_id), ()
    return job, ()


def _reconciling_observed(job: Job, event: Observed) -> Result:
    scan = event.scan
    if scan.invocation is not None:
        return (_correlate(job, event), ()) if scan.full else (_to(job, "pending", event.at), ())
    if _new_run(job, scan) is None or scan.run_state not in FINISHED_RUN:
        return job, ()
    # A run that started after our start and failed before it chose a plan is ours failing.
    if scan.run_full or scan.run_state == "failed":
        return _run_ended(job, event)
    # A partial scheduled run held the lock, so our start lost it (C4). Start again.
    return _dispatch(job, event)


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
    Bounded like the invocation job's starts: after the last send, a job the host accepted
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


def _only(protocol: str, handler: Callable[[Job, Any], Result]) -> Callable[[Job, Any], Result]:
    """An event of one protocol leaves a job of the other unchanged."""
    def guarded(job: Job, event: Event) -> Result:
        return handler(job, event) if job.protocol == protocol else (job, ())
    return guarded


_RUN_DEADLINE = lambda job, event: event.run_deadline_seconds  # noqa: E731

TRANSITIONS: Dict[Tuple[str, str], Callable[[Job, Event], Result]] = {
    ("dispatching", "Observed"): _only("invocation", _dispatching_observed),
    ("dispatching", "DispatchReturned"): _only("invocation", _to_reconciling),
    ("dispatching", "Traced"): _only("receipt", _dispatching_traced),
    ("dispatching", "RescanReplied"): _only("receipt", _replied(_to_pending)),
    ("dispatching", "Tick"): _after(lambda job, event: DISPATCH_DEADLINE_SECONDS, "unconfirmed", "NO_TRACE"),
    ("dispatching", "Restarted"): _restart_dispatching,
    ("pending", "Observed"): _only("invocation", _pending_observed),
    ("pending", "DispatchReturned"): _ignore,
    ("pending", "Traced"): _only("receipt", _pending_traced),
    ("pending", "RescanReplied"): _only("receipt", _replied(_ignore)),
    ("pending", "Tick"): _after(_RUN_DEADLINE, "failed", "RESCAN_NOT_STARTED"),
    ("pending", "Restarted"): _ignore,
    ("running", "Observed"): _only("invocation", _running_observed),
    ("running", "DispatchReturned"): _ignore,
    ("running", "Traced"): _only("receipt", _running_traced),
    ("running", "RescanReplied"): _ignore,
    ("running", "Tick"): _after(_RUN_DEADLINE, "failed", "RESCAN_TIMEOUT"),
    ("running", "Restarted"): _ignore,
    ("reconciling", "Observed"): _only("invocation", _reconciling_observed),
    ("reconciling", "DispatchReturned"): _ignore,
    ("reconciling", "Traced"): _only("receipt", _reconciling_traced),
    ("reconciling", "RescanReplied"): _only("receipt", _replied(_to_pending)),
    ("reconciling", "Tick"): _after(lambda job, event: RECONCILE_DEADLINE_SECONDS, "unconfirmed", "NO_TRACE"),
    ("reconciling", "Restarted"): _ignore,
    ("collecting", "Observed"): _only("invocation", _collect),
    ("collecting", "DispatchReturned"): _ignore,
    ("collecting", "Traced"): _only("receipt", _collecting_traced),
    ("collecting", "RescanReplied"): _ignore,
    ("collecting", "Tick"): _after(lambda job, event: COLLECT_DEADLINE_SECONDS, "failed", "PULL_FAILED"),
    ("collecting", "Restarted"): _ignore,
    **{(state, kind): _ignore for state in TERMINAL for kind in EVENT_KINDS},
}


def reduce(job: Job, event: Event) -> Result:
    return TRANSITIONS[(job.state, type(event).__name__)](job, event)
