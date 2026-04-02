"""Metrics endpoints — history, current status, WebSocket live feed."""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from ..database import get_db
    from ..models import GpuMetric
except ImportError:  # pragma: no cover - direct execution fallback
    from database import get_db
    from models import GpuMetric

logger = logging.getLogger(__name__)
router = APIRouter(tags=["metrics"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class GpuMetricOut(BaseModel):
    id: int
    server_id: int
    gpu_index: int
    utilization: int | None
    memory_used: int | None
    memory_total: int | None
    temperature: int | None
    power_draw: int | None
    active_users: str
    collected_at: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/servers/{server_id}/metrics/history", response_model=list[GpuMetricOut])
async def get_metric_history(
    server_id: int,
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
):
    since = datetime.utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(GpuMetric)
        .where(GpuMetric.server_id == server_id, GpuMetric.collected_at >= since)
        .order_by(GpuMetric.collected_at)
    )
    metrics = result.scalars().all()

    def _out(m: GpuMetric) -> GpuMetricOut:
        return GpuMetricOut(
            id=m.id,
            server_id=m.server_id,
            gpu_index=m.gpu_index,
            utilization=m.utilization,
            memory_used=m.memory_used,
            memory_total=m.memory_total,
            temperature=m.temperature,
            power_draw=m.power_draw,
            active_users=m.active_users or "[]",
            collected_at=m.collected_at.isoformat() if isinstance(m.collected_at, datetime) else str(m.collected_at),
        )

    return [_out(m) for m in metrics]


@router.get("/servers/status")
async def get_all_server_status():
    try:
        try:
            from ..collectors.manager import get_current_state
        except ImportError:  # pragma: no cover - direct execution fallback
            from collectors.manager import get_current_state
        return get_current_state()
    except ImportError:
        return {}


@router.websocket("/ws/metrics")
async def websocket_metrics(websocket: WebSocket):
    try:
        from ..ws_manager import ws_manager
    except ImportError:  # pragma: no cover - direct execution fallback
        from ws_manager import ws_manager

    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive; data is pushed by the collector
            await websocket.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)
