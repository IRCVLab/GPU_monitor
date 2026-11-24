"""UTC conversions for telemetry API timestamps and SQLite UTC columns."""

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return utc(value).replace(tzinfo=None)


def parse_at(value: str) -> datetime:
    return utc(datetime.fromisoformat(value.replace('Z', '+00:00')))


def iso(value: datetime | None) -> str | None:
    return utc(value).isoformat() if value is not None else None
