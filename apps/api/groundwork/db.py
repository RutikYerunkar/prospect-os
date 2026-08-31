from sqlalchemy import event, inspect
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


def _inspect_schema_problems(sync_conn) -> list[str]:
    inspector = inspect(sync_conn)
    table_names = set(inspector.get_table_names())
    problems: list[str] = []

    if "runs" in table_names:
        run_columns = {c["name"] for c in inspector.get_columns("runs")}
        if "provider_profile" not in run_columns:
            problems.append("runs.provider_profile column is missing")
    # If `runs` itself doesn't exist yet, this is a brand-new (empty) DB,
    # not a stale one — `create_all()` handles that case fine, nothing to
    # flag here.

    if table_names and "llm_calls" not in table_names:
        problems.append("llm_calls table is missing")

    return problems


async def schema_upgrade_problems(target_engine: AsyncEngine | None = None) -> list[str]:
    """Checkpoint G added `runs.provider_profile` and the `llm_calls` table.
    `create_all()` only *creates missing tables* — it never alters an
    existing table to add a new column — so a pre-Checkpoint-G local SQLite
    file left `runs` without `provider_profile`, and the first write to it
    surfaced as a raw `sqlite3.OperationalError` deep in a live smoke run,
    with no explanation of what a developer needed to do about it.

    Returns a list of human-readable problems (empty = current). Read-only —
    never mutates the database, never deletes anything. Callers that find
    problems should tell the user to run `make demo-reset` (or
    `python -m groundwork.scripts.reset`) themselves; this function never
    resets automatically, since that would delete local data without
    asking.
    """
    target = target_engine or engine
    async with target.connect() as conn:
        return await conn.run_sync(_inspect_schema_problems)
