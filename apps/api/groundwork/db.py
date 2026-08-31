from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from groundwork.config import settings
from groundwork.models.tables import Base


def _enable_wal(dbapi_connection, connection_record) -> None:
    """SQLite pragmas for WAL concurrency. See docs/ARCHITECTURE.md — SQLite section."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine() -> AsyncEngine:
    engine = create_async_engine(settings.database_url, echo=False)
    if settings.database_url.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _enable_wal)
    return engine


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def create_all() -> None:
    """`create_all()` only — no Alembic yet (P2, per §17)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
