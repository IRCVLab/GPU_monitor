"""이벤트 로그 조회 API."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

try:
    from ..database import AsyncSessionLocal
    from ..models import EventLog
except ImportError:  # pragma: no cover - direct execution fallback
    from database import AsyncSessionLocal
    from models import EventLog

router = APIRouter(prefix="/logs", tags=["logs"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class EventLogOut(BaseModel):
    id: int
    server_id: Optional[int]
    server_name: Optional[str]
    event_type: str
    severity: str
    message: str
    metadata: Optional[dict]
    created_at: str


class EventLogListResponse(BaseModel):
    items: list[EventLogOut]
    total: int


def _serialize_created_at(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=EventLogListResponse)
async def list_logs(
    server_id: Optional[int] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = min(limit, 200)

    async with AsyncSessionLocal() as db:
        query = select(EventLog)
        count_query = select(func.count()).select_from(EventLog)

        if server_id is not None:
            query = query.where(EventLog.server_id == server_id)
            count_query = count_query.where(EventLog.server_id == server_id)
        if event_type is not None:
            query = query.where(EventLog.event_type == event_type)
            count_query = count_query.where(EventLog.event_type == event_type)
        if severity is not None:
            query = query.where(EventLog.severity == severity)
            count_query = count_query.where(EventLog.severity == severity)

        total = (await db.execute(count_query)).scalar_one()
        rows = (
            await db.execute(
                query.order_by(EventLog.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars().all()

    return EventLogListResponse(
        items=[
            EventLogOut(
                id=row.id,
                server_id=row.server_id,
                server_name=row.server_name,
                event_type=row.event_type,
                severity=row.severity,
                message=row.message,
                metadata=row.event_metadata,
                created_at=_serialize_created_at(row.created_at),
            )
            for row in rows
        ],
        total=total,
    )


@router.get("/event-types", response_model=list[str])
async def list_event_types():
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(EventLog.event_type).distinct().order_by(EventLog.event_type)
            )
        ).scalars().all()
    return list(rows)
