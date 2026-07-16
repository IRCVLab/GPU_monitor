from pathlib import Path
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine

try:
    from .config import get_settings
    from .models import Base
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from models import Base


settings = get_settings()

# data/ 디렉토리 자동 생성
db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def ensure_notes_expiry_schema_sync(conn: Connection) -> None:
    result = conn.execute(text("PRAGMA table_info(notes)"))
    columns = {row[1] for row in result.fetchall()}

    if "expires_at" not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN expires_at DATETIME"))
    if "kind" not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN kind TEXT NOT NULL DEFAULT 'memo'"))
    if "gpu_indices" not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN gpu_indices TEXT NOT NULL DEFAULT '[]'"))
    if "priority" not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN priority TEXT DEFAULT 'normal'"))
    if "display_name" not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN display_name TEXT"))

    conn.execute(text("UPDATE notes SET kind = 'memo' WHERE kind IS NULL OR kind = ''"))
    conn.execute(text("UPDATE notes SET gpu_indices = '[]' WHERE gpu_indices IS NULL OR gpu_indices = ''"))
    conn.execute(text("UPDATE notes SET priority = 'normal' WHERE priority IS NULL OR TRIM(priority) = '' OR priority NOT IN ('normal', 'high', 'urgent')"))
    conn.execute(text("UPDATE notes SET display_name = SUBSTR(TRIM(display_name), 1, 40) WHERE display_name IS NOT NULL"))
    conn.execute(text("UPDATE notes SET display_name = NULL WHERE display_name IS NOT NULL AND display_name = ''"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_notes_expires_at ON notes (expires_at)"))


async def ensure_notes_expiry_schema(conn: AsyncConnection) -> None:
    await conn.run_sync(ensure_notes_expiry_schema_sync)


async def init_db():
    """앱 시작 시 테이블 생성 + WAL 모드 활성화."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)
        await ensure_notes_expiry_schema(conn)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
