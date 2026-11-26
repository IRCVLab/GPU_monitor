"""The per-host agent update track (target design 3.4), a pure reducer.

Inactive until M3: no actor feeds it events yet, and every record keeps the
initial ``in_sync`` state with no desired release.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Tuple, Union

STATES = ("in_sync", "staging", "activating", "probation", "rolling_back", "rolled_back", "backoff", "refused")
FIRST_BACKOFF_SECONDS = 15 * 60
MAX_BACKOFF_SECONDS = 6 * 3600
PROBATION_GRACE_SECONDS = 3600


@dataclass(frozen=True)
class Update:
    desired: Optional[str] = None
    state: str = "in_sync"
    previous: Optional[str] = None
    attempts: int = 0
    next_try_unix: Optional[int] = None
    since_unix: Optional[int] = None
    last_error: Optional[str] = None


@dataclass(frozen=True)
class Observed:
    at: int
    release_sha: Optional[str]
    idle: bool
    job_active: bool
    allowed: bool
    """The fleet rollout lets this host stage the desired release."""
    cadence_sec: int = 3600


@dataclass(frozen=True)
class StageDone:
    at: int
    ok: bool
    code: Optional[str] = None


@dataclass(frozen=True)
class ActivateDone:
    at: int
    result: str
    """ok, busy, failed or refused."""
    code: Optional[str] = None


@dataclass(frozen=True)
class ProbationJudged:
    """The first real run by the desired release was judged by central."""
    at: int
    fault: bool
    code: Optional[str] = None


@dataclass(frozen=True)
class DesiredChanged:
    at: int
    sha: Optional[str]


@dataclass(frozen=True)
class Tick:
    at: int


@dataclass(frozen=True)
class Stage:
    sha: str


@dataclass(frozen=True)
class Activate:
    sha: str


Event = Union[Observed, StageDone, ActivateDone, ProbationJudged, DesiredChanged, Tick]
EVENT_KINDS = ("Observed", "StageDone", "ActivateDone", "ProbationJudged", "DesiredChanged", "Tick")
Effect = Union[Stage, Activate]
Result = Tuple[Update, Tuple[Effect, ...]]


def _ignore(update: Update, event: Event) -> Result:
    return update, ()


def _to(update: Update, state: str, at: int, **fields) -> Update:
    return replace(update, state=state, since_unix=at, **fields)


def _backoff(update: Update, at: int, code: Optional[str]) -> Result:
    attempts = update.attempts + 1
    delay = min(MAX_BACKOFF_SECONDS, FIRST_BACKOFF_SECONDS * 2 ** (attempts - 1))
    return _to(update, "backoff", at, attempts=attempts, next_try_unix=at + delay, last_error=code), ()


def _in_sync_observed(update: Update, event: Observed) -> Result:
    behind = update.desired is not None and event.release_sha != update.desired
    if not (behind and event.idle and not event.job_active and event.allowed):
        return update, ()
    return _to(update, "staging", event.at, previous=event.release_sha), (Stage(update.desired),)


def _staging_done(update: Update, event: StageDone) -> Result:
    if not event.ok:
        return _backoff(update, event.at, event.code)
    return _to(update, "activating", event.at), (Activate(update.desired),)


def _activating_observed(update: Update, event: Observed) -> Result:
    if event.release_sha == update.desired:
        return _to(update, "probation", event.at), ()
    # The updater answered BUSY while a scan held the lock: try again once the host is idle.
    return (update, (Activate(update.desired),)) if event.idle else (update, ())


def _activating_done(update: Update, event: ActivateDone) -> Result:
    if event.result == "failed":
        return _backoff(update, event.at, event.code)
    if event.result == "refused":
        return _to(update, "refused", event.at, last_error=event.code), ()
    return update, ()


def _probation_judged(update: Update, event: ProbationJudged) -> Result:
    if not event.fault:
        return _to(update, "in_sync", event.at, attempts=0, next_try_unix=None, last_error=None), ()
    return _roll_back(update, event.at, event.code)


def _roll_back(update: Update, at: int, code: Optional[str]) -> Result:
    if update.previous is None:
        return _to(update, "refused", at, last_error=code), ()
    return _to(update, "rolling_back", at, last_error=code), (Activate(update.previous),)


def _probation_observed(update: Update, event: Observed) -> Result:
    if update.since_unix is not None and event.at - update.since_unix > event.cadence_sec + PROBATION_GRACE_SECONDS:
        return _roll_back(update, event.at, "PROBATION_NO_RUN")
    return update, ()


def _rolling_back_observed(update: Update, event: Observed) -> Result:
    if event.release_sha == update.previous:
        return _to(update, "rolled_back", event.at), ()
    return update, ()


def _backoff_tick(update: Update, event: Tick) -> Result:
    if update.next_try_unix is None or event.at < update.next_try_unix:
        return update, ()
    return _to(update, "staging", event.at), (Stage(update.desired),)


def _desired_changed(update: Update, event: DesiredChanged) -> Result:
    if event.sha == update.desired:
        return update, ()
    return Update(desired=event.sha, state="in_sync", since_unix=event.at), ()


TRANSITIONS: Dict[Tuple[str, str], Callable[[Update, Event], Result]] = {
    **{(state, kind): _ignore for state in STATES for kind in EVENT_KINDS},
    **{(state, "DesiredChanged"): _desired_changed for state in STATES},
    ("in_sync", "Observed"): _in_sync_observed,
    ("staging", "StageDone"): _staging_done,
    ("activating", "Observed"): _activating_observed,
    ("activating", "ActivateDone"): _activating_done,
    ("probation", "Observed"): _probation_observed,
    ("probation", "ProbationJudged"): _probation_judged,
    ("rolling_back", "Observed"): _rolling_back_observed,
    ("backoff", "Tick"): _backoff_tick,
}


def reduce(update: Update, event: Event) -> Result:
    return TRANSITIONS[(update.state, type(event).__name__)](update, event)
