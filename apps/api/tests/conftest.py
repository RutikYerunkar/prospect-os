from __future__ import annotations

import os
import tempfile

import httpx
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from groundwork.api.deps import get_session_factory
from groundwork.main import app
from groundwork.models.tables import Base


def _enable_wal(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


@pytest_asyncio.fixture
async def session_factory():
    """A fresh, isolated SQLite DB per test, file-backed (not `:memory:`) so
    concurrent prospect coroutines get real, independent connections from
    the pool — same as production — instead of contending for one shared
    in-memory connection under `StaticPool`, which produces spurious
    "could not refresh instance" errors under genuine concurrency.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(test_engine.sync_engine, "connect", _enable_wal)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    yield factory
    await test_engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


@pytest_asyncio.fixture
async def client(session_factory):
    """An httpx client against the real FastAPI app, retargeted at the
    isolated per-test SQLite file above via dependency override — no app
    lifespan (so no touching the real `groundwork.db`), and background
    `execute_run` tasks launched by a request run against this same DB.
    """
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
