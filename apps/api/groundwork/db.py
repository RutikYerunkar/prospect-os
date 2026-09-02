from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from groundwork.config import settings
from groundwork.db_url import normalize_database_url
from groundwork.migration_status import schema_upgrade_problems as _schema_upgrade_problems
from groundwork.models.tables import Base


def _enable_wal(dbapi_connection, connection_record) -> None:
    """SQLite pragmas for WAL concurrency. See docs/ARCHITECTURE.md — SQLite section."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(database_url: str | None = None) -> AsyncEngine:
    """`database_url` is normalized through `groundwork/db_url.py` — never
    handed to `create_async_engine` raw. A malformed URL or an unsupported/
    misunderstood query parameter (`sslmode`, `channel_binding`, or anything
    else) raises `DatabaseConfigurationError` here, at engine-construction
    time (import time for the module-level `engine` below, i.e. application
    startup) — never discovered later as an opaque driver error on the first
    real query."""
    url, connect_args = normalize_database_url(database_url or settings.database_url)
    is_sqlite = url.startswith("sqlite")
    engine = create_async_engine(
        url,
        echo=False,
        connect_args=connect_args,
        pool_pre_ping=settings.db_pool_pre_ping,
        # SQLite is one file — SQLAlchemy's default `NullPool` (or a
        # single-connection singleton for `:memory:`) is already correct
        # there; pool sizing only matters for the Postgres dialect, and only
        # from ONE API instance with ONE uvicorn worker (§ "Runtime Postgres
        # pool: small pool appropriate for one API instance" — no PgBouncer
        # merely because it exists).
        **({} if is_sqlite else {"pool_size": settings.db_pool_size, "max_overflow": settings.db_max_overflow}),
    )
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _enable_wal)
    return engine


engine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def create_all() -> None:
    """SQLite-only local-dev convenience — see `create_all_if_sqlite()`
    below for the guarded entry point every real caller (`main.py`'s
    lifespan, scripts) should use instead of calling this directly.
    Production/Postgres schema management is exclusively `alembic upgrade
    head`, run explicitly (see docs/RUNBOOK.md) — never this function."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_all_if_sqlite() -> bool:
    """The guarded entry point: only actually creates tables when running
    against SQLite (Checkpoint I1 Phase 5 — "production never runs
    create_all silently"). Returns whether it did anything, so callers can
    log/print accordingly. Postgres schema state is instead verified via
    `schema_upgrade_problems()`/`GET /api/ready`, never silently created or
    altered by the running application."""
    if engine.dialect.name != "sqlite":
        return False
    await create_all()
    return True


async def drop_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def schema_upgrade_problems(target_engine: AsyncEngine | None = None) -> list[str]:
    """Delegates to `groundwork.migration_status` — see that module for the
    Alembic-based mechanism this replaced the old hand-maintained
    per-checkpoint column/table probe with. Kept as the same name/shape
    here so existing callers (`scripts/live_smoke.py`) need no changes."""
    return await _schema_upgrade_problems(target_engine or engine)
