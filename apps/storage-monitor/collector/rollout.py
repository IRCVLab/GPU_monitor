"""The fleet rollout of the desired agent release (design §3.4), a pure reducer over RolloutState.

A new sha goes to the canary first. Only after the canary leaves probation on it
does the sha enter ``passed`` and the phase become ``fleet``, where every host may
stage it, two at a time. A failed canary, or any host failing probation in the
fleet phase, halts the rollout until the desired sha changes (I23). A sha any host
rolls back from leaves ``passed``, so it must pass the canary again.

``fleet.Rollout`` owns the one RolloutState and persists it as ``rollout.json``;
``encode`` and ``parse`` are that file's boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Any, Callable, Dict, Optional, Tuple, Union

from collector.ids import RELEASE_SHA_RE, SERVER_ID_RE

SCHEMA = "storage-viz.rollout/1"
PHASES = ("canary", "fleet", "halted")
MAX_PASSED = 20
MAX_STAGING = 2
"""Hosts staging at once in the fleet phase."""


@dataclass(frozen=True)
class Passed:
    sha: str
    release_seq: Optional[int]


@dataclass(frozen=True)
class RolloutState:
    desired_sha: Optional[str] = None
    release_seq: Optional[int] = None
    canary: Optional[str] = None
    phase: str = "canary"
    reason: Optional[str] = None
    passed: Tuple[Passed, ...] = ()
    """The last MAX_PASSED shas that cleared the canary, oldest first."""


@dataclass(frozen=True)
class DesiredChanged:
    sha: Optional[str]
    release_seq: Optional[int] = None


@dataclass(frozen=True)
class HostInSync:
    """The host left probation in sync on ``sha``."""
    host: str
    sha: str


@dataclass(frozen=True)
class HostFailed:
    """The host cannot take ``sha``: refused, or three back-offs."""
    host: str
    sha: str
    reason: Optional[str] = None


@dataclass(frozen=True)
class HostRolledBack:
    """The host failed probation on ``sha`` and is rolled back."""
    host: str
    sha: str
    reason: Optional[str] = None


Event = Union[DesiredChanged, HostInSync, HostFailed, HostRolledBack]
EVENT_KINDS = ("DesiredChanged", "HostInSync", "HostFailed", "HostRolledBack")


def allows(state: RolloutState, host: str) -> bool:
    """Whether ``host`` may stage the desired sha now (the staging-slot count is fleet.Rollout's)."""
    if state.desired_sha is None:
        return False
    return state.phase == "fleet" or (state.phase == "canary" and host == state.canary)


def _ignore(state: RolloutState, event: Event) -> RolloutState:
    return state


def _desired_changed(state: RolloutState, event: DesiredChanged) -> RolloutState:
    if event.sha == state.desired_sha:
        return state
    passed = event.sha is not None and any(entry.sha == event.sha for entry in state.passed)
    return replace(state, desired_sha=event.sha, release_seq=event.release_seq,
                   phase="fleet" if passed else "canary", reason=None)


def _current(handler: Callable[[RolloutState, Any], RolloutState]) -> Callable[[RolloutState, Any], RolloutState]:
    """A host event about another sha than the desired one is stale and changes nothing."""
    def guarded(state: RolloutState, event: Any) -> RolloutState:
        return handler(state, event) if event.sha == state.desired_sha else state
    return guarded


def _halt(state: RolloutState, event: Union[HostFailed, HostRolledBack]) -> RolloutState:
    return replace(state, phase="halted", reason=f"{event.host}: {event.reason or type(event).__name__}")


@_current
def _canary_in_sync(state: RolloutState, event: HostInSync) -> RolloutState:
    if event.host != state.canary:
        return state
    kept = tuple(entry for entry in state.passed if entry.sha != event.sha)
    return replace(state, phase="fleet", reason=None,
                   passed=(kept + (Passed(event.sha, state.release_seq),))[-MAX_PASSED:])


@_current
def _canary_failed(state: RolloutState, event: Union[HostFailed, HostRolledBack]) -> RolloutState:
    return _halt(state, event) if event.host == state.canary else state


@_current
def _fleet_rolled_back(state: RolloutState, event: HostRolledBack) -> RolloutState:
    return _halt(state, event)


def _unpass(handler: Callable[[RolloutState, Any], RolloutState]) -> Callable[[RolloutState, Any], RolloutState]:
    """A rollback from a sha, whichever host and phase, takes it off ``passed`` before ``handler`` runs."""
    def unpassed(state: RolloutState, event: HostRolledBack) -> RolloutState:
        kept = tuple(entry for entry in state.passed if entry.sha != event.sha)
        return handler(replace(state, passed=kept) if kept != state.passed else state, event)
    return unpassed


TRANSITIONS: Dict[Tuple[str, str], Callable[[RolloutState, Any], RolloutState]] = {
    **{(phase, kind): _ignore for phase in PHASES for kind in EVENT_KINDS},
    **{(phase, "DesiredChanged"): _desired_changed for phase in PHASES},
    ("canary", "HostInSync"): _canary_in_sync,
    ("canary", "HostFailed"): _canary_failed,
    ("canary", "HostRolledBack"): _unpass(_canary_failed),
    ("fleet", "HostRolledBack"): _unpass(_fleet_rolled_back),
    ("halted", "HostRolledBack"): _unpass(_ignore),
}


def reduce(state: RolloutState, event: Event) -> RolloutState:
    return TRANSITIONS[(state.phase, type(event).__name__)](state, event)


def host_event(host: str, before: Any, after: Any) -> Optional[Event]:
    """The rollout event a committed update-track change means, if any (``before``/``after``: update.Update).

    Probation failing is the host rolling back, reported when the track leaves probation, so the
    rollout halts before the host has even answered the rollback.
    """
    sha = after.desired
    if sha is None or before.desired != sha or before.state == after.state and before.attempts == after.attempts:
        return None
    if before.state == "probation" and after.state == "in_sync":
        return HostInSync(host, sha)
    if before.state == "probation" and after.state in ("rolling_back", "refused"):
        return HostRolledBack(host, sha, after.last_error)
    if after.state == "refused" and before.state != "rolling_back":
        return HostFailed(host, sha, after.last_error)
    if after.state == "backoff" and after.attempts >= 3 > before.attempts:
        return HostFailed(host, sha, f"BACKOFF_3:{after.last_error}")
    return None


# ------------------------------------------------------------------ rollout.json


def encode(state: RolloutState) -> bytes:
    value = {"schema": SCHEMA, "desired_sha": state.desired_sha, "release_seq": state.release_seq,
             "canary": state.canary, "phase": state.phase, "reason": state.reason,
             "passed": [{"sha": entry.sha, "release_seq": entry.release_seq} for entry in state.passed]}
    return json.dumps(value, sort_keys=True).encode() + b"\n"


def parse(data: bytes) -> RolloutState:
    """rollout.json as written by ``encode``; ValueError for anything else."""
    raw = json.loads(data)
    if not isinstance(raw, dict) or set(raw) != {"schema", "desired_sha", "release_seq", "canary", "phase", "reason", "passed"}:
        raise ValueError("rollout.json keys differ from the schema")
    if raw["schema"] != SCHEMA or raw["phase"] not in PHASES:
        raise ValueError("rollout.json schema or phase is unknown")
    passed = raw["passed"]
    if not isinstance(passed, list) or len(passed) > MAX_PASSED:
        raise ValueError("rollout.json passed is malformed")
    for entry in passed:
        if not isinstance(entry, dict) or set(entry) != {"sha", "release_seq"}:
            raise ValueError("rollout.json passed entry is malformed")
    _check(raw["desired_sha"], _sha, "desired_sha")
    _check(raw["release_seq"], _seq, "release_seq")
    _check(raw["canary"], lambda value: isinstance(value, str) and SERVER_ID_RE.fullmatch(value), "canary")
    _check(raw["reason"], lambda value: isinstance(value, str) and len(value) <= 512, "reason")
    for entry in passed:
        if not _sha(entry["sha"]):
            raise ValueError("rollout.json passed sha is malformed")
        _check(entry["release_seq"], _seq, "passed release_seq")
    return RolloutState(raw["desired_sha"], raw["release_seq"], raw["canary"], raw["phase"], raw["reason"],
                        tuple(Passed(entry["sha"], entry["release_seq"]) for entry in passed))


def _sha(value: Any) -> bool:
    return isinstance(value, str) and RELEASE_SHA_RE.fullmatch(value) is not None


def _seq(value: Any) -> bool:
    return type(value) is int and value >= 0


def _check(value: Any, valid: Callable[[Any], Any], label: str) -> None:
    if value is not None and not valid(value):
        raise ValueError(f"rollout.json {label} is malformed")
