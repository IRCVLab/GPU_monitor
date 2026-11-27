"""The only mapping from domain errors to HTTP statuses (target design 4.3)."""
from __future__ import annotations

from typing import Dict

from collector.errors import (
    ActiveJob, Cooldown, DomainError, GlobalConcurrency, RescanDisabled, RescanUnavailable, ServerBusy,
    StoreUnavailable, UnknownServer,
)

STATUS: Dict[type | str, int] = {
    "INTERNAL": 500,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "BAD_REQUEST": 400,
    "BODY_TOO_LARGE": 413,
    "AUTH_REQUIRED": 401, "FORBIDDEN": 403, "BAD_ORIGIN": 403, "BAD_CSRF": 403,
    "UNKNOWN_VOLUME": 404, "CAPTURE_GONE": 410,
    UnknownServer: 404,
    RescanDisabled: 403,
    ActiveJob: 409,
    Cooldown: 429,
    GlobalConcurrency: 429,
    StoreUnavailable: 503,
    ServerBusy: 503,
    RescanUnavailable: 503,
}


class RequestError(Exception):
    def __init__(self, code, *, headers=None):
        super().__init__(code)
        self.code = code
        self.headers = headers or {}


def http_error(exc, request_id, *, health_probe=False):
    code = exc.code if isinstance(exc, (DomainError, RequestError)) else "INTERNAL"
    status = STATUS.get(type(exc), STATUS.get(code, 500))
    if health_probe and isinstance(exc, UnknownServer):
        # SHIM(M8..M9) S9: installed checker compares the string at body.error.
        return status, {}, {"error": code}
    if isinstance(exc, RescanDisabled):
        code = "FORBIDDEN"
    headers = dict(exc.headers) if isinstance(exc, RequestError) else {}
    error = {"code": code, "message": code.replace("_", " ").capitalize(), "request_id": request_id}
    if isinstance(exc, ActiveJob):
        error["job"] = exc.detail.get("job")
    retry = 5 if isinstance(exc, GlobalConcurrency) else getattr(exc, "retry_after", None)
    if retry is not None:
        headers["Retry-After"] = str(retry)
        error["retry_after_sec"] = retry
    return status, headers, {"error": error}
