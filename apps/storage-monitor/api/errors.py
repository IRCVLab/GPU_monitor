"""The only mapping from domain errors to HTTP statuses (target design 4.3).

SHIM(M2b..M8) S5: the v1 routes keep their body ``{"error": code, ...}``. M8 retires this shape.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from collector.errors import (
    ActiveJob, Cooldown, DomainError, GlobalConcurrency, RescanDisabled, RescanUnavailable, ServerBusy, SnapshotAbsent,
    StoreUnavailable, UnknownServer,
)

STATUS: Dict[type | str, int] = {
    "INTERNAL": 500,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "BAD_LENGTH": 400, "BAD_BODY": 400, "BAD_JSON": 400, "BAD_REQUEST": 400,
    "BODY_TOO_LARGE": 413,
    "AUTH_REQUIRED": 401, "FORBIDDEN": 403, "BAD_ORIGIN": 403, "BAD_SESSION": 403, "BAD_CSRF": 403,
    "UNKNOWN_VOLUME": 404, "CAPTURE_GONE": 410,
    "UNCONFIGURED": 503, "LEGACY_DISABLED": 503,
    UnknownServer: 404,
    SnapshotAbsent: 404,
    RescanDisabled: 403,
    ActiveJob: 409,
    Cooldown: 429,
    GlobalConcurrency: 429,
    StoreUnavailable: 503,
    ServerBusy: 503,
    RescanUnavailable: 503,
}


def _v1_error(exc: BaseException) -> Tuple[int, Dict[str, str], Dict[str, Any]]:
    """Status, extra headers and v1 body for any exception a route raised. Unknown ones are 500."""
    if isinstance(exc, RequestError):
        return STATUS[exc.code], exc.headers, exc.body
    if not isinstance(exc, DomainError) or type(exc) not in STATUS:
        return STATUS["INTERNAL"], {}, {"error": "INTERNAL"}
    headers = {}
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(exc, GlobalConcurrency):
        retry_after = 5
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return STATUS[type(exc)], headers, {"error": exc.code, **exc.detail}


class RequestError(Exception):
    def __init__(self, code, *, headers=None, body=None):
        super().__init__(code)
        self.code = code
        self.headers = headers or {}
        self.body = body or {"error": code}


def http_error(exc, request_id, *, v1):
    status, headers, body = _v1_error(exc)
    if v1:
        return status, headers, body
    code = exc.code if isinstance(exc, (RequestError, DomainError)) else "INTERNAL"
    error = {"code": code, "message": code.replace("_", " ").capitalize(), "request_id": request_id}
    if "Retry-After" in headers:
        error["retry_after_sec"] = int(headers["Retry-After"])
    return status, headers, {"error": error}
