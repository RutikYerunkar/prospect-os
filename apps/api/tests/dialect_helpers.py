"""Dual-dialect test helpers — Checkpoint I1 Phase 6.

Tests whose whole point is "prove this works identically on SQLite and
Postgres" (event sequencing, the execution lease) parametrize over
`available_dialects()` rather than hardcoding SQLite. Postgres is only
included when `GROUNDWORK_TEST_POSTGRES_DSN` is set (a local Postgres
container/instance, never a cloud database — see docs/RUNBOOK.md) — so the
default `pytest`/`make test` run (no Postgres required) still exercises
every one of these tests against SQLite, and CI's Postgres service container
(Phase 10B) exercises the identical test bodies against real Postgres by
setting that one env var.
"""

from __future__ import annotations

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from groundwork.models.tables import Base

POSTGRES_DSN_ENV = "GROUNDWORK_TEST_POSTGRES_DSN"


def postgres_dsn() -> str | None:
    return os.environ.get(POSTGRES_DSN_ENV)


def available_dialects() -> list[str]:
    dialects = ["sqlite"]
    if postgres_dsn():
        dialects.append("postgres")
    return dialects


def _enable_sqlite_wal(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def connection_url(dialect: str, sqlite_path: str) -> str:
    if dialect == "sqlite":
        return f"sqlite+aiosqlite:///{sqlite_path}"
    if dialect == "postgres":
        dsn = postgres_dsn()
        if not dsn:
            raise RuntimeError(f"{POSTGRES_DSN_ENV} not set")
        return dsn
    raise ValueError(f"unknown dialect {dialect!r}")


def make_engine(dialect: str, sqlite_path: str) -> AsyncEngine:
    """A NEW engine pointed at the dialect's target — call this more than
    once against the same `sqlite_path`/the same Postgres DSN to simulate
    multiple independent processes/connections sharing one database."""
    engine = create_async_engine(connection_url(dialect, sqlite_path))
    if dialect == "sqlite":
        event.listen(engine.sync_engine, "connect", _enable_sqlite_wal)
    return engine


async def create_schema(dialect: str, sqlite_path: str) -> None:
    engine = make_engine(dialect, sqlite_path)
    try:
        async with engine.begin() as conn:
            if dialect == "postgres":
                # The Postgres target is one shared, persistent test
                # database (not a fresh file per test like SQLite gets) —
                # start every test from a clean slate.
                await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def drop_schema(dialect: str, sqlite_path: str) -> None:
    if dialect != "postgres":
        return  # the SQLite file itself is deleted by the caller's teardown
    engine = make_engine(dialect, sqlite_path)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()
