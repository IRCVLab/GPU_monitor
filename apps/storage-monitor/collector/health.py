"""Per-server health reducers: the contact link and the held data.

Both reducers are pure. ``ServerActor`` is their only caller and the only
writer of the state they return.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Mapping, Optional, Tuple, Union

from collector.contract import HostManifest

# ---------------------------------------------------------------- link

LINK_STATES = ("unknown", "ok", "unreachable", "auth_failed", "protocol_error", "identity_mismatch")
TRANSPORT_CODES = frozenset({"TIMEOUT", "UNREACHABLE"})
AUTH_CODES = frozenset({"AUTH_FAILED"})
IDENTITY_CODES = frozenset({"IDENTITY_MISMATCH"})


@dataclass(frozen=True)
class Link:
    state: str = "unknown"
    seq: int = 0
    since: Optional[int] = None
    last_ok_unix: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class ExchangeOk:
    """Any SSH exchange whose reply parsed: observe, transfer or rescan."""
    seq: int
    at: int


@dataclass(frozen=True)
class ExchangeFailed:
    seq: int
    at: int
    code: str


LinkEvent = Union[ExchangeOk, ExchangeFailed]


def link_event_kind(event: LinkEvent) -> str:
    if isinstance(event, ExchangeOk):
        return "ok"
    if event.code in TRANSPORT_CODES:
        return "transport"
    if event.code in AUTH_CODES:
        return "auth"
    if event.code in IDENTITY_CODES:
        return "identity"
    return "protocol"


LINK_EVENT_KINDS = ("ok", "transport", "auth", "protocol", "identity")
_LINK_TARGET = {
    "ok": "ok", "transport": "unreachable", "auth": "auth_failed",
    "protocol": "protocol_error", "identity": "identity_mismatch",
}


def _link_to(target: str) -> Callable[[Link, LinkEvent], Link]:
    def handler(link: Link, event: LinkEvent) -> Link:
        return Link(
            state=target,
            seq=event.seq,
            since=link.since if link.state == target else event.at,
            last_ok_unix=event.at if target == "ok" else link.last_ok_unix,
            error=None if target == "ok" else event.code,
        )
    return handler


# The newest exchange decides the link, whatever the state before it.
LINK_TRANSITIONS: Dict[Tuple[str, str], Callable[[Link, LinkEvent], Link]] = {
    (state, kind): _link_to(_LINK_TARGET[kind]) for state in LINK_STATES for kind in LINK_EVENT_KINDS
}


def reduce_link(link: Link, event: LinkEvent) -> Link:
    # Results apply in issue order: a late reply for an older exchange changes nothing (I7, C1).
    if event.seq <= link.seq:
        return link
    return LINK_TRANSITIONS[(link.state, link_event_kind(event))](link, event)


# ---------------------------------------------------------------- data

MAX_REJECTED = 32
GONE_BACKOFF_SECONDS = 60
GONE_BACKOFF_MAX_SECONDS = 3600
DOWNLOADABLE = frozenset({"complete", "partial"})
DATA_STATES = ("none", "current", "fetching", "stale_rejected")


@dataclass(frozen=True)
class Data:
    manifest: Optional[HostManifest] = None
    """The last accepted publication."""
    accepted: Mapping[str, str] = field(default_factory=dict)
    """volume_id -> sha256 of the capture held in the store. Can lag the manifest (stale_rejected)."""
    rejected: Mapping[str, Mapping] = field(default_factory=dict)
    """sha256 -> {volume_id, reason, at}. Never fetched again while the manifest names it (I8, C2)."""
    retried: frozenset = frozenset()
    """Memory only: shas that already had their one clean retry after a size or digest mismatch."""
    fetching: frozenset = frozenset()
    """Memory only: shas handed to the transfer worker."""
    gone: Mapping[str, Tuple[int, int]] = field(default_factory=dict)
    """Memory only: sha -> (times the host had no such capture, unix time before which it is not fetched)."""


@dataclass(frozen=True)
class PublicationObserved:
    """The host's current publication: every accepted or unchanged manifest."""
    publication: HostManifest
    at: int = 0
    """Central time of the observe: a capture the host lost is not fetched again before its back-off ends."""


@dataclass(frozen=True)
class CaptureAccepted:
    volume_id: str
    sha: str


@dataclass(frozen=True)
class CaptureRejected:
    volume_id: str
    sha: str
    reason: str
    at: int


@dataclass(frozen=True)
class CaptureMismatch:
    """The bytes did not match the published size or digest: maybe a truncated transfer."""
    volume_id: str
    sha: str
    at: int


@dataclass(frozen=True)
class TransferFailed:
    """Transport failure, a capture gone from the host, or a publication that changed during the transfer.
    Retried on the next observe."""
    volume_id: str
    sha: str


@dataclass(frozen=True)
class CaptureGone:
    """The host has no file for a capture its manifest names. Not the capture's fault: never rejected.

    The first time, the manifest is read again (it probably moved on). Each repeat for the same
    sha waits GONE_BACKOFF_SECONDS, doubling up to GONE_BACKOFF_MAX_SECONDS, before the next fetch.
    """
    volume_id: str
    sha: str
    at: int


@dataclass(frozen=True)
class Fetch:
    volume_id: str
    sha: str
    publication: HostManifest
    """The publication that named the sha: it says how to fetch and check the capture."""


DataEvent = Union[PublicationObserved, CaptureAccepted, CaptureRejected, CaptureMismatch, TransferFailed, CaptureGone]
DATA_EVENT_KINDS = ("PublicationObserved", "CaptureAccepted", "CaptureRejected", "CaptureMismatch", "TransferFailed",
                    "CaptureGone")


def targets(data: Data) -> Dict[str, Optional[str]]:
    return data.manifest.targets() if data.manifest is not None else {}


def volume_state(data: Data, volume_id: str) -> str:
    held = data.accepted.get(volume_id)
    if held is None:
        return "none"
    sha = targets(data).get(volume_id)
    if sha is None or sha == held:
        return "current"
    if sha in data.rejected:
        return "stale_rejected"
    return "fetching"


def volume_states(data: Data) -> Dict[str, str]:
    return {volume_id: volume_state(data, volume_id) for volume_id in sorted({*targets(data), *data.accepted})}


def volume_problems(data: Data) -> Dict[str, str]:
    """Volumes whose published capture the host keeps reporting missing, after a reread named it again."""
    return {volume_id: "CAPTURE_GONE" for volume_id, sha in targets(data).items()
            if sha is not None and data.gone.get(sha, (0, 0))[0] >= 2}


def captures_progress(data: Data) -> str:
    """Whether every capture the publication names is held: rejected, fetching or current."""
    named = [(volume_id, sha) for volume_id, sha in targets(data).items() if sha is not None]
    if any(sha in data.rejected for _, sha in named):
        return "rejected"
    if any(data.accepted.get(volume_id) != sha for volume_id, sha in named):
        return "fetching"
    return "current"


def data_state(data: Data) -> str:
    """The server's worst volume: any stale_rejected, then any fetching, then any current."""
    states = set(volume_states(data).values())
    return next((state for state in ("stale_rejected", "fetching", "current") if state in states), "none")


def _publication_observed(data: Data, event: PublicationObserved) -> Tuple[Data, Tuple[Fetch, ...]]:
    named = event.publication.targets()
    # A volume the publication no longer names is dropped; one it names without a capture keeps what is held.
    accepted = {volume_id: sha for volume_id, sha in data.accepted.items() if volume_id in named}
    rejected = {sha: entry for sha, entry in data.rejected.items() if sha in named.values()}
    # A capture names its own volume, so one sha cannot serve two volumes: a volume that names
    # another volume's held capture is refused once, without a transfer.
    held_by = {sha: volume_id for volume_id, sha in accepted.items()}
    rejected = _capped({**rejected, **{
        sha: {"volume_id": volume_id, "reason": "VOLUME_MISMATCH", "at": event.at}
        for volume_id, sha in named.items()
        if sha is not None and sha not in rejected and held_by.get(sha, volume_id) != volume_id}})
    gone = {sha: entry for sha, entry in data.gone.items() if sha in named.values()}
    fetches = tuple(
        Fetch(volume_id, sha, event.publication) for volume_id, sha in sorted(named.items())
        if sha is not None and sha != accepted.get(volume_id) and sha not in rejected and sha not in data.fetching
        and gone.get(sha, (0, 0))[1] <= event.at
    )
    data = replace(data, manifest=event.publication, accepted=accepted, rejected=rejected, gone=gone,
                   fetching=data.fetching | {fetch.sha for fetch in fetches})
    return data, fetches


def _capture_accepted(data: Data, event: CaptureAccepted) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha not in data.fetching:
        return data, ()
    data = replace(data, fetching=data.fetching - {event.sha}, retried=data.retried - {event.sha},
                   gone={sha: entry for sha, entry in data.gone.items() if sha != event.sha})
    if targets(data).get(event.volume_id) != event.sha:
        return data, ()
    return replace(data, accepted={**data.accepted, event.volume_id: event.sha}), ()


def _reject(data: Data, volume_id: str, sha: str, reason: str, at: int) -> Data:
    if data.accepted.get(volume_id) == sha:
        # Bytes the volume holds were valid once: never both accepted and rejected for one volume.
        return replace(data, fetching=data.fetching - {sha}, retried=data.retried - {sha})
    entry = {"volume_id": volume_id, "reason": reason, "at": at}
    return replace(data, rejected=_capped({**data.rejected, sha: entry}), fetching=data.fetching - {sha},
                   retried=data.retried - {sha})


def _capped(rejected: Dict[str, Mapping]) -> Dict[str, Mapping]:
    if len(rejected) > MAX_REJECTED:
        oldest = sorted(rejected, key=lambda key: rejected[key]["at"])[: len(rejected) - MAX_REJECTED]
        rejected = {key: value for key, value in rejected.items() if key not in oldest}
    return rejected


def _capture_rejected(data: Data, event: CaptureRejected) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha not in data.fetching:
        return data, ()
    return _reject(data, event.volume_id, event.sha, event.reason, event.at), ()


def _capture_mismatch(data: Data, event: CaptureMismatch) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha not in data.fetching:
        return data, ()
    if event.sha in data.retried:
        return _reject(data, event.volume_id, event.sha, "SHA_MISMATCH", event.at), ()
    return replace(data, fetching=data.fetching - {event.sha}, retried=data.retried | {event.sha}), ()


def _transfer_failed(data: Data, event: TransferFailed) -> Tuple[Data, Tuple[Fetch, ...]]:
    return replace(data, fetching=data.fetching - {event.sha}), ()


def _capture_gone(data: Data, event: CaptureGone) -> Tuple[Data, Tuple[Fetch, ...]]:
    times = data.gone.get(event.sha, (0, 0))[0] + 1
    wait = 0 if times == 1 else min(GONE_BACKOFF_SECONDS * 2 ** (times - 2), GONE_BACKOFF_MAX_SECONDS)
    return replace(data, fetching=data.fetching - {event.sha}, gone={**data.gone, event.sha: (times, event.at + wait)}), ()


_DATA_HANDLERS = {
    "PublicationObserved": _publication_observed,
    "CaptureAccepted": _capture_accepted,
    "CaptureRejected": _capture_rejected,
    "CaptureMismatch": _capture_mismatch,
    "TransferFailed": _transfer_failed,
    "CaptureGone": _capture_gone,
}
# Every summary state handles every event the same way; the handlers carry the per-volume guards.
DATA_TRANSITIONS = {(state, kind): _DATA_HANDLERS[kind] for state in DATA_STATES for kind in DATA_EVENT_KINDS}


def reduce_data(data: Data, event: DataEvent) -> Tuple[Data, Tuple[Fetch, ...]]:
    return DATA_TRANSITIONS[(data_state(data), type(event).__name__)](data, event)
