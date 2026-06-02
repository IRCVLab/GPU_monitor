from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from .models import Note
except ImportError:  # pragma: no cover - direct execution fallback
    from models import Note


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_expires_at(expires_at: datetime, now: datetime | None = None) -> datetime:
    normalized = coerce_utc(expires_at)
    current = coerce_utc(now or utc_now())
    if normalized <= current:
        raise ValueError("expires_at must be in the future")
    return normalized


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = coerce_utc(value)
    return normalized.isoformat().replace("+00:00", "Z")


async def delete_expired_notes(
    session: AsyncSession,
    now: datetime | None = None,
) -> int:
    result = await session.execute(expired_notes_delete_statement(now))
    return int(result.rowcount or 0)


def expired_notes_delete_statement(now: datetime | None = None):
    current = coerce_utc(now or utc_now())
    return delete(Note).where(
        Note.expires_at.is_not(None),
        Note.expires_at <= current,
    )
