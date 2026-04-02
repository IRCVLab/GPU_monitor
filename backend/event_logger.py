"""이벤트 기록 유틸 — DB 장애 시 재시도와 상태 가시성을 함께 제공한다."""
import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_PENDING_LIMIT = 256
_pending_events: deque[dict] = deque()
_write_lock = asyncio.Lock()
_dropped_total = 0
_last_failure_at: datetime | None = None
_last_failure_error: str | None = None
_last_failure_event: dict | None = None
_last_recovery_at: datetime | None = None
_last_recovered_count = 0


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _queue_failed_batch(events: list[dict]) -> None:
    global _dropped_total

    for event in events:
        if len(_pending_events) >= _PENDING_LIMIT:
            _pending_events.popleft()
            _dropped_total += 1
        _pending_events.append(event)


def get_event_log_health() -> dict:
    """Return a snapshot that callers can surface in status payloads."""
    return {
        "degraded": bool(_pending_events),
        "pending_count": len(_pending_events),
        "dropped_total": _dropped_total,
        "last_failure_at": _serialize_dt(_last_failure_at),
        "last_failure_error": _last_failure_error,
        "last_failure_event_type": (
            _last_failure_event.get("event_type") if _last_failure_event else None
        ),
        "last_failure_server_id": (
            _last_failure_event.get("server_id") if _last_failure_event else None
        ),
        "last_recovery_at": _serialize_dt(_last_recovery_at),
        "last_recovered_count": _last_recovered_count,
    }


def _build_payload(
    event_type: str,
    severity: str,
    message: str,
    server_id: int | None,
    server_name: str | None,
    metadata: dict | None,
) -> dict:
    return {
        "event_type": event_type,
        "severity": severity,
        "message": message,
        "server_id": server_id,
        "server_name": server_name,
        "event_metadata": metadata,
    }


async def log_event(
    event_type: str,
    severity: str,
    message: str,
    server_id: int | None = None,
    server_name: str | None = None,
    metadata: dict | None = None,
) -> None:
    global _last_failure_at, _last_failure_error, _last_failure_event
    global _last_recovery_at, _last_recovered_count

    current_event = _build_payload(
        event_type=event_type,
        severity=severity,
        message=message,
        server_id=server_id,
        server_name=server_name,
        metadata=metadata,
    )

    try:
        try:
            from .database import AsyncSessionLocal
            from .models import EventLog
        except ImportError:  # pragma: no cover - direct execution fallback
            from database import AsyncSessionLocal
            from models import EventLog

        async with _write_lock:
            backlog = list(_pending_events)
            _pending_events.clear()
            batch = backlog + [current_event]

            db = None
            try:
                async with AsyncSessionLocal() as db:
                    for item in batch:
                        db.add(EventLog(**item))
                    await db.commit()
            except Exception:
                if db is not None:
                    try:
                        await db.rollback()
                    except Exception:
                        logger.debug("Event log rollback failed", exc_info=True)
                raise

            if backlog:
                _last_recovery_at = _utcnow()
                _last_recovered_count = len(backlog)
                logger.warning(
                    "Recovered %d queued event log entries after database became writable again",
                    len(backlog),
                )
    except Exception as exc:
        _last_failure_at = _utcnow()
        _last_failure_error = str(exc)
        _last_failure_event = current_event
        _queue_failed_batch(batch if "batch" in locals() else [current_event])
        logger.exception(
            "Event log persistence failed for %s on server %s; queued=%d dropped_total=%d",
            event_type,
            server_id,
            len(_pending_events),
            _dropped_total,
        )
