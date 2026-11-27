"""The per-host agent update track (target design §3.4), a pure reducer.

The actor feeds it every gate observation, the result of each request it sent,
the probation verdict on the first real run of a new release, and desired-release
changes. It answers with the gate requests to send: ``Stage``, ``Activate`` or
``Rollback``.

No state waits on one event. Every state that sent a request (``staging``,
``activating``, ``rolling_back``) holds a host-time resend deadline: a lost result,
a failed record write or a restart only delays the next send to an idle host.
Sends are bounded: after MAX_REQUESTS without success ``staging`` and
``activating`` back off, and ``rolling_back`` ends in ``rollback_failed``.

``activate-update`` and ``rollback-update`` only queue a request on the host; the
updater's answer arrives later in ``observe`` as ``update/state.json``
``last_attempt``. An answer counts only when the host finished it at or after the
host time the request was sent (``requested_host_unix``), so an answer to an older
request is never read as the answer to this one. Every comparison of host times
uses host times (I20). A request result or answer for another sha than the one
the track is sending is ignored.

Host answers that cannot change for this sha are terminal, with no retry loop:
FLOOR, BOOTSTRAP_TOO_OLD, BAD_RELEASE, ROLLBACK_TARGET and the host's own self-test
back-off go to ``refused``. A failed attempt (signature, digest, self-test) backs off
15 min, doubling to 6 h. A failed probation sends ``rollback-update <previous>``,
never ``activate-update``: only the rollback makes the host mark the release bad.
A host the rollout no longer permits stops before activation and returns to
``in_sync`` on the release it runs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Tuple, Union

STATES = ("in_sync", "staging", "activating", "probation", "rolling_back", "rolled_back", "rollback_failed", "backoff",
          "refused")
STAGING_STATES = frozenset({"staging", "activating"})
"""States that hold one of the fleet's staging slots."""
REQUEST_STATES = frozenset({"staging", "activating", "rolling_back"})
"""States that sent a request and hold a resend deadline."""
FIRST_BACKOFF_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 6 * 3600
PROBATION_GRACE_SECONDS = 3600
RESEND_SECONDS = 300
"""Host seconds without an answer before an idle host gets the same activate or rollback request again."""
STAGE_RESEND_SECONDS = 900
"""The same for a stage: longer than the stage-update transfer timeout."""
MAX_REQUESTS = 6
"""Sends of one request before the step gives up."""
AGENT_BROKEN = "AGENT_BROKEN"
PROBATION_NO_RUN = "PROBATION_NO_RUN"
NO_PREVIOUS = "NO_PREVIOUS_RELEASE"
NO_ANSWER = "NO_ANSWER"
ROLLOUT_STOPPED = "ROLLOUT_STOPPED"


@dataclass(frozen=True)
class Update:
    desired: Optional[str] = None
    state: str = "in_sync"
    previous: Optional[str] = None
    """The release the host ran before this update: the rollback target."""
    attempts: int = 0
    """Back-offs for the desired release so far."""
    next_try_unix: Optional[int] = None
    since_unix: Optional[int] = None
    last_error: Optional[str] = None
    requested_host_unix: Optional[int] = None
    """Host time of the last request sent, or of the start of probation. The resend deadline counts from it."""
    requests: int = 0
    """Sends of the current request."""
    probation_manifest: Optional[str] = None
    """sha256 of the manifest the host showed when probation began: the old release wrote it."""


@dataclass(frozen=True)
class Attempt:
    """The host updater's last attempt (``update/state.json`` ``last_attempt``), as the gate reports it."""
    action: Optional[str]
    """activate or rollback; None when the request itself was unreadable."""
    sha: Optional[str]
    outcome: str
    """activated, already_active, rolled_back, busy, refused or failed."""
    code: Optional[str]
    finished_unix: int
    """Host time."""


@dataclass(frozen=True)
class Observed:
    """One gate ``observe``."""
    at: int
    host_at: int
    release_sha: Optional[str]
    idle: bool
    """No scan lock and no run.json on the host."""
    job_active: bool
    allowed: bool
    """The fleet rollout gave this host a staging slot for the desired release, over the gate."""
    permitted: bool = True
    """The rollout still lets this host take the desired release (not halted, canary or fleet phase)."""
    broken: bool = False
    """The gate reports the release's check-config failing."""
    attempt: Optional[Attempt] = None
    cadence_sec: int = 3600
    """The longest scan cadence the host's manifest declares."""
    manifest_sha: Optional[str] = None
    """sha256 of the manifest.json the host shows now."""


@dataclass(frozen=True)
class Stage:
    sha: str


@dataclass(frozen=True)
class Activate:
    sha: str


@dataclass(frozen=True)
class Rollback:
    sha: str


Effect = Union[Stage, Activate, Rollback]


@dataclass(frozen=True)
class Sent:
    """The result of sending one request (the exchange, not the host updater's answer)."""
    at: int
    host_at: int
    effect: Effect
    ok: bool
    code: Optional[str] = None
    permitted: bool = True
    """The rollout still lets this host take the desired release."""


@dataclass(frozen=True)
class ProbationJudged:
    """Central's verdict on the first real run by the desired release."""
    at: int
    host_at: int
    fault: bool
    code: Optional[str] = None


@dataclass(frozen=True)
class DesiredChanged:
    at: int
    sha: Optional[str]


Event = Union[Observed, Sent, ProbationJudged, DesiredChanged]
EVENT_KINDS = ("Observed", "Sent", "ProbationJudged", "DesiredChanged")
Result = Tuple[Update, Tuple[Effect, ...]]


def _ignore(update: Update, event: Event) -> Result:
    return update, ()


def _to(update: Update, state: str, at: int, **fields) -> Update:
    return replace(update, state=state, since_unix=at, **fields)


def _request(update: Update, state: str, at: int, host_at: int, effect: Effect, **fields) -> Result:
    """Enter ``state`` by sending ``effect`` for the first time."""
    return _to(update, state, at, requests=1, requested_host_unix=host_at, **fields), (effect,)


def _backoff(update: Update, at: int, code: Optional[str]) -> Result:
    attempts = update.attempts + 1
    delay = min(MAX_BACKOFF_SECONDS, FIRST_BACKOFF_SECONDS * 2 ** (attempts - 1))
    return _to(update, "backoff", at, attempts=attempts, next_try_unix=at + delay, last_error=code, requests=0), ()


def _effect(update: Update) -> Effect:
    """The request the current state sends."""
    if update.state == "staging":
        return Stage(update.desired)
    if update.state == "activating":
        return Activate(update.desired)
    return Rollback(update.previous)


def _resend(update: Update, event: Observed, answer: Optional[Attempt]) -> Result:
    """Send the request again to an idle host: at once after a BUSY answer, else after the resend window.
    The last allowed send failing gives up: back off, or rollback_failed."""
    window = STAGE_RESEND_SECONDS if update.state == "staging" else RESEND_SECONDS
    due = update.requested_host_unix is None or event.host_at - update.requested_host_unix >= window
    busy = answer is not None and answer.outcome == "busy"
    if not (event.idle and (busy or due)):
        return update, ()
    if update.requests >= MAX_REQUESTS:
        code = update.last_error or (answer.code if busy else NO_ANSWER)
        if update.state == "rolling_back":
            return _to(update, "rollback_failed", event.at, last_error=code, requests=0), ()
        return _backoff(update, event.at, code)
    return replace(update, requests=update.requests + 1, requested_host_unix=event.host_at), (_effect(update),)


def _answer(update: Update, event: Observed, action: str, sha: Optional[str]) -> Optional[Attempt]:
    """The host's answer to the request this track sent last, if the observation carries it."""
    attempt = event.attempt
    if (attempt is None or attempt.action != action or attempt.sha != sha or update.requested_host_unix is None
            or attempt.finished_unix < update.requested_host_unix):
        return None
    return attempt


def _stop(update: Update, at: int) -> Result:
    """The rollout no longer permits this host: stop before activation, on the release it runs."""
    return _to(update, "in_sync", at, last_error=ROLLOUT_STOPPED, requests=0), ()


def _start(update: Update, event: Observed) -> Result:
    behind = update.desired is not None and event.release_sha != update.desired
    if not (behind and event.idle and not event.job_active and event.allowed):
        return update, ()
    return _request(update, "staging", event.at, event.host_at, Stage(update.desired), previous=event.release_sha)


def _backoff_observed(update: Update, event: Observed) -> Result:
    if update.next_try_unix is not None and event.at < update.next_try_unix:
        return update, ()
    return _start(update, event)


def _staging_observed(update: Update, event: Observed) -> Result:
    # No result came (lost, a failed record write, a restart): stage again; the host replaces the staged bundle.
    if not event.permitted:
        return _stop(update, event.at)
    return _resend(update, event, None)


def _staging_sent(update: Update, event: Sent) -> Result:
    if event.effect != Stage(update.desired):
        return update, ()
    if not event.ok:
        return _backoff(update, event.at, event.code)
    if not event.permitted:
        return _stop(update, event.at)
    return _request(update, "activating", event.at, event.host_at, Activate(update.desired))


def _failed_send(update: Update, event: Sent, expected: Effect) -> Result:
    """A request that did not reach the updater: note why, and make it due at the next idle observe."""
    if event.effect != expected or event.ok:
        return update, ()
    return replace(update, last_error=event.code, requested_host_unix=event.host_at - RESEND_SECONDS), ()


def _activating_observed(update: Update, event: Observed) -> Result:
    answer = _answer(update, event, "activate", update.desired)
    if event.release_sha == update.desired:
        # Probation counts from the activation on the host clock: a run started after it is the new release's.
        started = answer.finished_unix if answer is not None and answer.outcome in ("activated", "already_active") else event.host_at
        return _to(update, "probation", event.at, requested_host_unix=started, last_error=None, requests=0,
                   probation_manifest=event.manifest_sha), ()
    if answer is not None and answer.outcome == "refused":
        return _to(update, "refused", event.at, last_error=answer.code, requests=0), ()
    if answer is not None and answer.outcome == "failed":
        return _backoff(update, event.at, answer.code)
    if not event.permitted:
        return _stop(update, event.at)
    return _resend(update, event, answer)


def _activating_sent(update: Update, event: Sent) -> Result:
    return _failed_send(update, event, Activate(update.desired))


def _roll_back(update: Update, at: int, host_at: int, code: Optional[str]) -> Result:
    if update.previous is None:
        return _to(update, "refused", at, last_error=NO_PREVIOUS), ()
    return _request(update, "rolling_back", at, host_at, Rollback(update.previous), last_error=code)


def _probation_observed(update: Update, event: Observed) -> Result:
    if event.broken:
        return _roll_back(update, event.at, event.host_at, AGENT_BROKEN)
    started = update.requested_host_unix
    if started is not None and event.host_at - started > event.cadence_sec + PROBATION_GRACE_SECONDS:
        return _roll_back(update, event.at, event.host_at, PROBATION_NO_RUN)
    return update, ()


def _probation_judged(update: Update, event: ProbationJudged) -> Result:
    if not event.fault:
        return _to(update, "in_sync", event.at, attempts=0, next_try_unix=None, last_error=None), ()
    return _roll_back(update, event.at, event.host_at, event.code)


def _rolling_back_observed(update: Update, event: Observed) -> Result:
    if event.release_sha == update.previous:
        return _to(update, "rolled_back", event.at, requests=0), ()
    answer = _answer(update, event, "rollback", update.previous)
    if answer is not None and answer.outcome == "refused":
        # FLOOR: the previous release is below the host's floor, so the fix goes forward, not back.
        return _to(update, "refused", event.at, last_error=answer.code, requests=0), ()
    return _resend(update, event, answer)


def _rolling_back_sent(update: Update, event: Sent) -> Result:
    return _failed_send(update, event, Rollback(update.previous))


def _desired_changed(update: Update, event: DesiredChanged) -> Result:
    if event.sha == update.desired:
        return update, ()
    return Update(desired=event.sha, state="in_sync", since_unix=event.at), ()


TRANSITIONS: Dict[Tuple[str, str], Callable[[Update, Event], Result]] = {
    **{(state, kind): _ignore for state in STATES for kind in EVENT_KINDS},
    **{(state, "DesiredChanged"): _desired_changed for state in STATES},
    ("in_sync", "Observed"): _start,
    ("backoff", "Observed"): _backoff_observed,
    ("staging", "Observed"): _staging_observed,
    ("staging", "Sent"): _staging_sent,
    ("activating", "Observed"): _activating_observed,
    ("activating", "Sent"): _activating_sent,
    ("probation", "Observed"): _probation_observed,
    ("probation", "ProbationJudged"): _probation_judged,
    ("rolling_back", "Observed"): _rolling_back_observed,
    ("rolling_back", "Sent"): _rolling_back_sent,
}


def reduce(update: Update, event: Event) -> Result:
    return TRANSITIONS[(update.state, type(event).__name__)](update, event)
