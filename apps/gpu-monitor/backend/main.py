"""FastAPI application entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import delete

try:
    from .config import get_settings
    from .database import AsyncSessionLocal, init_db
    from .live_database import prepare_live_database
    from .models import EventLog
    from .note_expiry import delete_expired_notes
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import AsyncSessionLocal, init_db
    from live_database import prepare_live_database
    from models import EventLog
    from note_expiry import delete_expired_notes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


async def cleanup_old_logs() -> None:
    """7일 이상 지난 이벤트 로그 삭제."""
    try:
        cutoff = datetime.utcnow() - timedelta(days=7)
        async with AsyncSessionLocal() as db:
            await db.execute(delete(EventLog).where(EventLog.created_at < cutoff))
            await db.commit()
        logger.debug("Event log cleanup done (cutoff=%s)", cutoff.date())
    except Exception as exc:
        logger.error("Event log cleanup failed: %s", exc)


async def cleanup_expired_notes() -> None:
    try:
        async with AsyncSessionLocal() as db:
            deleted = await delete_expired_notes(db)
            await db.commit()
        if deleted > 0:
            logger.info("Expired note cleanup removed %d note(s)", deleted)
    except Exception as exc:
        logger.error("Expired note cleanup failed: %s", exc)


async def _log_cleanup_loop() -> None:
    await cleanup_old_logs()
    while True:
        await asyncio.sleep(60)
        await cleanup_old_logs()


async def _note_cleanup_loop() -> None:
    await cleanup_expired_notes()
    while True:
        await asyncio.sleep(5)
        await cleanup_expired_notes()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.monitoring_expected_server_count > 0:
        logger.info("Starting up — preflighting live database before schema initialization")
        snapshot = await asyncio.to_thread(
            prepare_live_database,
            settings.database_url,
            settings.monitoring_expected_server_count,
            settings.monitoring_database_backup_dir or None,
            settings.monitoring_database_backup_keep,
        )
        logger.info(
            "Live database preflight passed with %d registered server(s)",
            snapshot.server_count,
        )

    logger.info("Starting up — initialising database")
    await init_db()
    slack_socket_service = None

    if settings.monitoring_disable_collectors:
        logger.info("Collector manager disabled by MONITORING_DISABLE_COLLECTORS")
    else:
        try:
            from sqlalchemy import select
            try:
                from .database import AsyncSessionLocal
                from .models import Server
                from .collectors import manager
            except ImportError:  # pragma: no cover - direct execution fallback
                from database import AsyncSessionLocal
                from models import Server
                import collectors.manager as manager

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Server))
                servers = result.scalars().all()

            await manager.start_all(servers)
            logger.info("Collector manager started for %d server(s)", len(servers))
        except Exception as exc:
            logger.warning("Collector manager not available: %s", exc)

    if settings.monitoring_disable_slack:
        logger.info("Slack Socket Mode disabled by MONITORING_DISABLE_SLACK")
    else:
        try:
            try:
                from .slack_socket import slack_socket_service as _slack_socket_service
            except ImportError:  # pragma: no cover - direct execution fallback
                from slack_socket import slack_socket_service as _slack_socket_service
            slack_socket_service = _slack_socket_service
            slack_socket_service.start()
        except Exception as exc:
            logger.warning("Slack Socket Mode not available: %s", exc)

    log_cleanup_task = asyncio.create_task(_log_cleanup_loop())
    note_cleanup_task = asyncio.create_task(_note_cleanup_loop())

    yield

    log_cleanup_task.cancel()
    note_cleanup_task.cancel()

    logger.info("Shutting down — cancelling collector tasks")
    try:
        try:
            from .collectors import manager
        except ImportError:  # pragma: no cover - direct execution fallback
            import collectors.manager as manager
        # Cancel all running collector tasks
        for server_id in list(manager._tasks.keys()):
            await manager.remove_server(server_id)
    except Exception as exc:
        logger.warning("Error during collector shutdown: %s", exc)
    if slack_socket_service is not None:
        slack_socket_service.stop()


app = FastAPI(title="GPU Monitor", version="3.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    from .routers import logs, metrics, notes, servers, slack  # noqa: E402
except ImportError:  # pragma: no cover - direct execution fallback
    from routers import logs, metrics, notes, servers, slack  # noqa: E402

app.include_router(servers.router)
app.include_router(notes.router)
app.include_router(metrics.router)
app.include_router(slack.router)
app.include_router(logs.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
