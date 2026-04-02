from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


async def init_db():
    """앱 시작 시 테이블 생성 + WAL 모드 활성화."""
    async with engine.begin() as conn:
        await conn.execute(text("PRAGMA journal_mode=WAL"))
        await conn.execute(text("PRAGMA synchronous=NORMAL"))
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
