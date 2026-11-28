"""Domain errors the collector raises. ``api/errors.py`` is the only place that maps them to HTTP."""
from __future__ import annotations

from typing import Any, Mapping


class DomainError(Exception):
    code = "INTERNAL"

    def __init__(self, message: str = "", **detail: Any) -> None:
        super().__init__(message or self.code)
        self.detail: Mapping[str, Any] = detail


class UnknownServer(DomainError):
    """Raised only by an inventory lookup, before any store access (C6)."""
    code = "UNKNOWN_SERVER"


class CaptureGone(DomainError):
    code = "CAPTURE_GONE"


class StoreUnavailable(DomainError):
    code = "STORE_UNAVAILABLE"


class ServerBusy(DomainError):
    """The server's actor did not answer within the reply bound (C5)."""
    code = "SERVER_BUSY"
    retry_after = 5


class ActiveJob(DomainError):
    code = "ACTIVE_JOB"


class Cooldown(DomainError):
    code = "COOLDOWN"

    def __init__(self, retry_after: int, **detail: Any) -> None:
        super().__init__(**detail)
        self.retry_after = retry_after


class GlobalConcurrency(DomainError):
    code = "GLOBAL_CONCURRENCY"


class RescanDisabled(DomainError):
    code = "RESCAN_DISABLED"
