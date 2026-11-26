"""Per-server health reducers: the contact link and the held data.

Both reducers are pure. ``ServerActor`` is their only caller and the only
writer of the state they return.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Mapping, Optional, Tuple, Union

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

# SHIM(M2b..M5c) S3: a v1 host publishes one snapshot, held as the pseudo-volume "legacy".
LEGACY_VOLUME = "legacy"
MAX_REJECTED = 32
DOWNLOADABLE = frozenset({"complete", "partial"})
DATA_STATES = ("none", "current", "fetching", "stale_rejected")


@dataclass(frozen=True)
class Data:
    manifest: Optional[Mapping] = None
    """The last v1 status envelope the host published, validated at the boundary."""
    accepted: Optional[str] = None
    """sha256 of the capture held in the store."""
    rejected: Mapping[str, Mapping] = field(default_factory=dict)
    """sha256 -> {volume_id, generation, reason, at}. Never fetched again (I8, C2)."""
    retried: frozenset = frozenset()
    """Memory only: shas that already had their one clean retry after a size or digest mismatch."""
    fetching: Optional[str] = None
    """Memory only: the sha handed to the transfer worker."""


@dataclass(frozen=True)
class PublicationObserved:
    manifest: Optional[Mapping]


@dataclass(frozen=True)
class CaptureAccepted:
    sha: str


@dataclass(frozen=True)
class CaptureRejected:
    sha: str
    generation: Optional[str]
    reason: str
    at: int


@dataclass(frozen=True)
class CaptureMismatch:
    """The bytes did not match the published size or digest: maybe a truncated transfer."""
    sha: str
    generation: Optional[str]
    at: int


@dataclass(frozen=True)
class TransferFailed:
    """Transport failure or a publication that changed during the transfer. Retried on the next observe."""
    sha: str


@dataclass(frozen=True)
class Fetch:
    manifest: Mapping


DataEvent = Union[PublicationObserved, CaptureAccepted, CaptureRejected, CaptureMismatch, TransferFailed]
DATA_EVENT_KINDS = ("PublicationObserved", "CaptureAccepted", "CaptureRejected", "CaptureMismatch", "TransferFailed")


def published_sha(manifest: Optional[Mapping]) -> Optional[str]:
    if manifest is None or manifest.get("status") not in DOWNLOADABLE:
        return None
    return manifest.get("sha256")


def data_state(data: Data) -> str:
    if data.accepted is None:
        return "none"
    sha = published_sha(data.manifest)
    if sha is None or sha == data.accepted:
        return "current"
    if sha in data.rejected:
        return "stale_rejected"
    return "fetching"


def _publication_observed(data: Data, event: PublicationObserved) -> Tuple[Data, Tuple[Fetch, ...]]:
    sha = published_sha(event.manifest)
    rejected = {key: value for key, value in data.rejected.items() if key == sha}
    data = replace(data, manifest=event.manifest, rejected=rejected)
    if sha is None or sha == data.accepted or sha in rejected or data.fetching is not None:
        return data, ()
    return replace(data, fetching=sha), (Fetch(event.manifest),)


def _capture_accepted(data: Data, event: CaptureAccepted) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha != data.fetching:
        return data, ()
    return replace(data, accepted=event.sha, fetching=None, retried=data.retried - {event.sha}), ()


def _reject(data: Data, sha: str, generation: Optional[str], reason: str, at: int) -> Data:
    entry = {"volume_id": LEGACY_VOLUME, "generation": generation, "reason": reason, "at": at}
    rejected = {**data.rejected, sha: entry}
    if len(rejected) > MAX_REJECTED:
        oldest = sorted(rejected, key=lambda key: rejected[key]["at"])[: len(rejected) - MAX_REJECTED]
        rejected = {key: value for key, value in rejected.items() if key not in oldest}
    return replace(data, rejected=rejected, fetching=None, retried=data.retried - {sha})


def _capture_rejected(data: Data, event: CaptureRejected) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha != data.fetching:
        return data, ()
    return _reject(data, event.sha, event.generation, event.reason, event.at), ()


def _capture_mismatch(data: Data, event: CaptureMismatch) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha != data.fetching:
        return data, ()
    if event.sha in data.retried:
        return _reject(data, event.sha, event.generation, "SHA_MISMATCH", event.at), ()
    return replace(data, fetching=None, retried=data.retried | {event.sha}), ()


def _transfer_failed(data: Data, event: TransferFailed) -> Tuple[Data, Tuple[Fetch, ...]]:
    if event.sha != data.fetching:
        return data, ()
    return replace(data, fetching=None), ()


_DATA_HANDLERS = {
    "PublicationObserved": _publication_observed,
    "CaptureAccepted": _capture_accepted,
    "CaptureRejected": _capture_rejected,
    "CaptureMismatch": _capture_mismatch,
    "TransferFailed": _transfer_failed,
}
# Every data state handles every event the same way; the handlers carry the guards.
DATA_TRANSITIONS = {(state, kind): _DATA_HANDLERS[kind] for state in DATA_STATES for kind in DATA_EVENT_KINDS}


def reduce_data(data: Data, event: DataEvent) -> Tuple[Data, Tuple[Fetch, ...]]:
    return DATA_TRANSITIONS[(data_state(data), type(event).__name__)](data, event)
