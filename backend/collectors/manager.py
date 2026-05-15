"""Manages all per-server collector tasks."""
import asyncio
import logging
from datetime import datetime, timezone

try:
    from ..config import get_settings
    from ..event_logger import get_event_log_health
    from ..models import Server
    from .server_collector import ServerCollector
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from event_logger import get_event_log_health
    from models import Server
    from collectors.server_collector import ServerCollector

logger = logging.getLogger(__name__)

_collectors: dict[int, ServerCollector] = {}
_tasks: dict[int, asyncio.Task] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _stale_warn_seconds() -> int:
    settings = get_settings()
    return max(settings.collect_interval * 6, 60)


def _stale_offline_seconds() -> int:
    return max(_stale_warn_seconds() * 10, 600)


def _stale_reason(code: str, age_seconds: int, now: datetime) -> dict:
    if code == "stale_offline":
        message = f"상태 갱신 중단 ({age_seconds}s)"
    else:
        message = f"상태 갱신 지연 ({age_seconds}s)"
    return {
        "code": code,
        "source": "collector",
        "message": message,
        "retryable": True,
        "updated_at": now.isoformat(),
    }


def resolve_collector_export_state(
    collector: ServerCollector,
    *,
    now: datetime | None = None,
    stale_warn_seconds: int | None = None,
    stale_offline_seconds: int | None = None,
) -> tuple[str, datetime | None, dict | None]:
    status = collector.status
    offline_since = collector.offline_since
    reason = collector.status_reason

    if status == "offline" or collector.last_seen is None:
        return status, offline_since, reason

    now = now or _utcnow()
    age_seconds = max(0, int((now - collector.last_seen).total_seconds()))
    warn_after = stale_warn_seconds if stale_warn_seconds is not None else _stale_warn_seconds()
    offline_after = (
        stale_offline_seconds if stale_offline_seconds is not None else _stale_offline_seconds()
    )

    if age_seconds >= offline_after:
        return "offline", offline_since or collector.last_seen, _stale_reason("stale_offline", age_seconds, now)

    if age_seconds >= warn_after:
        return "degraded", offline_since, _stale_reason("stale_snapshot", age_seconds, now)

    return status, offline_since, reason


def get_current_state() -> dict:
    """Return {server_id: {status, last_seen, network, host, port, gpus, system}} for all collectors."""
    state = {}
    log_health = get_event_log_health()
    now = _utcnow()
    for server_id, collector in _collectors.items():
        data = collector.current_data or {}
        export_status, export_offline_since, export_reason = resolve_collector_export_state(
            collector,
            now=now,
        )
        state[server_id] = {
            "server_id": server_id,
            "status": export_status,
            "last_seen": (
                collector.last_seen.isoformat() if collector.last_seen else None
            ),
            "server_name": data.get("server_name", collector.server.name),
            "host": collector.server.host,
            "port": collector.server.port,
            "network": collector.server.network,
            "display_order": collector.server.display_order,
            "offline_since": (
                export_offline_since.isoformat() if export_offline_since else None
            ),
            "status_reason": export_reason,
            "gpus": data.get("gpus", []),
            "system": data.get("system"),
            "storage": data.get("storage"),
            "event_log_health": log_health,
        }
    return state


async def start_all(servers: list[Server]) -> None:
    """Create and start a collector task for each server."""
    for server in servers:
        await add_server(server)


async def add_server(server: Server) -> None:
    """Add and start a collector for a newly registered server."""
    if server.id in _collectors:
        logger.warning("Collector for server %d already running.", server.id)
        return

    if not server.ssh_password and not server.ssh_private_key:
        logger.info(
            "Skipping collector for server %s (id=%d): no SSH credentials configured",
            server.name,
            server.id,
        )
        return

    collector = ServerCollector(server)
    _collectors[server.id] = collector
    task = asyncio.create_task(collector.run(), name=f"collector-{server.id}")
    _tasks[server.id] = task
    logger.info("Started collector for server %s (id=%d)", server.name, server.id)


async def remove_server(server_id: int) -> None:
    """Stop and remove the collector for the given server ID."""
    task = _tasks.pop(server_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    collector = _collectors.pop(server_id, None)
    if collector:
        collector._ssh.close()
        logger.info("Removed collector for server id=%d", server_id)


async def update_server(server: Server) -> None:
    """Restart collector when server config changes (host, port, credentials, etc.)."""
    await remove_server(server.id)
    await add_server(server)
