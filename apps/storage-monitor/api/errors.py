"""The only mapping from domain errors to HTTP statuses (target design 4.3).

SHIM(M2b..M8) S5: the v1 routes keep their body ``{"error": code, ...}``. M6 moves the
handler into ``api/`` and M8 switches the routes to the v2 envelope.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from collector.errors import (
    ActiveJob, Cooldown, DomainError, GlobalConcurrency, RescanDisabled, ServerBusy, SnapshotAbsent,
    StoreUnavailable, UnknownServer,
)

STATUS: Dict[type, int] = {
    UnknownServer: 404,
    SnapshotAbsent: 404,
    RescanDisabled: 403,
    ActiveJob: 409,
    Cooldown: 429,
    GlobalConcurrency: 429,
    StoreUnavailable: 503,
    ServerBusy: 503,
}


def http_error(exc: BaseException) -> Tuple[int, Dict[str, str], Dict[str, Any]]:
    """Status, extra headers and v1 body for any exception a route raised. Unknown ones are 500."""
    if not isinstance(exc, DomainError) or type(exc) not in STATUS:
        return 500, {}, {"error": "INTERNAL"}
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return STATUS[type(exc)], headers, {"error": exc.code, **exc.detail}
