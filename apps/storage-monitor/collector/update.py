"""The per-host agent update track (target design §3.4), a pure reducer.

The actor feeds it every gate observation, the result of each bundle stage, the
probation verdict on the first real run of a new release, and desired-release
changes. It answers with the gate requests to send: ``Stage``, ``Activate`` or
``Rollback``.

``activate-update`` and ``rollback-update`` only queue a request on the host; the
updater's answer arrives later in ``observe`` as ``update/state.json``
``last_attempt``. An answer counts only when the host finished it at or after the
host time the request was sent (``requested_host_unix``), so an answer to an older
request is never read as the answer to this one. Every comparison of host times
uses host times (I20).

Host answers that cannot change for this sha are terminal, with no retry loop:
FLOOR, BOOTSTRAP_TOO_OLD, BAD_RELEASE, ROLLBACK_TARGET and the host's own self-test
back-off go to ``refused``. A failed attempt (signature, digest, self-test) backs off
15 min, doubling to 6 h. A failed probation sends ``rollback-update <previous>``,
never ``activate-update``: only the rollback makes the host mark the release bad.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Tuple, Union

STATES = ("in_sync", "staging", "activating", "probation", "rolling_back", "rolled_back", "backoff", "refused")
STAGING_STATES = frozenset({"staging", "activating"})
"""States that hold one of the fleet's staging slots."""
FIRST_BACKOFF_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 6 * 3600
PROBATION_GRACE_SECONDS = 3600
RESEND_SECONDS = 300
"""Host seconds without an answer before an idle host gets the same update request again."""
AGENT_BROKEN = "AGENT_BROKEN"
PROBATION_NO_RUN = "PROBATION_NO_RUN"
NO_PREVIOUS = "NO_PREVIOUS_RELEASE"


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
    """Host time of the last request sent, or of the start of probation."""


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
    """The fleet rollout lets this host stage the desired release now, over the gate."""
    broken: bool = False
    """The gate reports the release's check-config failing."""
    attempt: Optional[Attempt] = None
    cadence_sec: int = 3600
    """The longest scan cadence the host's manifest declares."""


@dataclass(frozen=True)
class StageDone:
    at: int
    host_at: int
    ok: bool
    code: Optional[str] = None


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


@dataclass(frozen=True)
class Stage:
    sha: str


@dataclass(frozen=True)
class Activate:
    sha: str


@dataclass(frozen=True)
class Rollback:
    sha: str


Event = Union[Observed, StageDone, ProbationJudged, DesiredChanged]
EVENT_KINDS = ("Observed", "StageDone", "ProbationJudged", "DesiredChanged")
Effect = Union[Stage, Activate, Rollback]
Result = Tuple[Update, Tuple[Effect, ...]]


def _ignore(update: Update, event: Event) -> Result:
    return update, ()


def _to(update: Update, state: str, at: int, **fields) -> Update:
    return replace(update, state=state, since_unix=at, **fields)


def _backoff(update: Update, at: int, code: Optional[str]) -> Result:
    attempts = update.attempts + 1
    delay = min(MAX_BACKOFF_SECONDS, FIRST_BACKOFF_SECONDS * 2 ** (attempts - 1))
    return _to(update, "backoff", at, attempts=attempts, next_try_unix=at + delay, last_error=code), ()


def _answer(update: Update, event: Observed, action: str, sha: Optional[str]) -> Optional[Attempt]:
    """The host's answer to the request this track sent last, if the observation carries it."""
    attempt = event.attempt
    if (attempt is None or attempt.action != action or attempt.sha != sha or update.requested_host_unix is None
            or attempt.finished_unix < update.requested_host_unix):
        return None
    return attempt


def _resend(update: Update, event: Observed, effect: Effect, answer: Optional[Attempt]) -> Result:
    """Send the request again on an idle host: at once after a BUSY answer, else RESEND_SECONDS after the last send."""
    waited = update.requested_host_unix is None or event.host_at - update.requested_host_unix >= RESEND_SECONDS
    busy = answer is not None and answer.outcome == "busy"
    if event.idle and (busy or waited):
        return replace(update, requested_host_unix=event.host_at), (effect,)
    return update, ()


def _start(update: Update, event: Observed) -> Result:
    behind = update.desired is not None and event.release_sha != update.desired
    if not (behind and event.idle and not event.job_active and event.allowed):
        return update, ()
    return _to(update, "staging", event.at, previous=event.release_sha, requested_host_unix=event.host_at), (Stage(update.desired),)


def _backoff_observed(update: Update, event: Observed) -> Result:
    if update.next_try_unix is not None and event.at < update.next_try_unix:
        return update, ()
    return _start(update, event)


def _staging_done(update: Update, event: StageDone) -> Result:
    if not event.ok:
        return _backoff(update, event.at, event.code)
    return _to(update, "activating", event.at, requested_host_unix=event.host_at), (Activate(update.desired),)


def _activating_observed(update: Update, event: Observed) -> Result:
    answer = _answer(update, event, "activate", update.desired)
    if event.release_sha == update.desired:
        # Probation counts from the activation on the host clock: a run started after it is the new release's.
        started = answer.finished_unix if answer is not None and answer.outcome in ("activated", "already_active") else event.host_at
        return _to(update, "probation", event.at, requested_host_unix=started, last_error=None), ()
    if answer is not None and answer.outcome == "refused":
        return _to(update, "refused", event.at, last_error=answer.code), ()
    if answer is not None and answer.outcome == "failed":
        return _backoff(update, event.at, answer.code)
    return _resend(update, event, Activate(update.desired), answer)


def _roll_back(update: Update, at: int, host_at: int, code: Optional[str]) -> Result:
    if update.previous is None:
        return _to(update, "refused", at, last_error=NO_PREVIOUS), ()
    return _to(update, "rolling_back", at, last_error=code, requested_host_unix=host_at), (Rollback(update.previous),)


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
        return _to(update, "rolled_back", event.at), ()
    answer = _answer(update, event, "rollback", update.previous)
    if answer is not None and answer.outcome == "refused":
        # FLOOR: the previous release is below the host's floor, so the fix goes forward, not back.
        return _to(update, "refused", event.at, last_error=answer.code), ()
    return _resend(update, event, Rollback(update.previous), answer)


def _desired_changed(update: Update, event: DesiredChanged) -> Result:
    if event.sha == update.desired:
        return update, ()
    return Update(desired=event.sha, state="in_sync", since_unix=event.at), ()


TRANSITIONS: Dict[Tuple[str, str], Callable[[Update, Event], Result]] = {
    **{(state, kind): _ignore for state in STATES for kind in EVENT_KINDS},
    **{(state, "DesiredChanged"): _desired_changed for state in STATES},
    ("in_sync", "Observed"): _start,
    ("backoff", "Observed"): _backoff_observed,
    ("staging", "StageDone"): _staging_done,
    ("activating", "Observed"): _activating_observed,
    ("probation", "Observed"): _probation_observed,
    ("probation", "ProbationJudged"): _probation_judged,
    ("rolling_back", "Observed"): _rolling_back_observed,
}


def reduce(update: Update, event: Event) -> Result:
    return TRANSITIONS[(update.state, type(event).__name__)](update, event)
