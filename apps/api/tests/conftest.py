from __future__ import annotations

import os
import tempfile
import uuid

import httpx
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from groundwork.api.deps import get_session_factory
from groundwork.main import app
from groundwork.models.tables import Base


def _enable_wal(dbapi_connection, connection_record) -> None:
    """Mirrors `db.py::_enable_wal` exactly. Checkpoint G's first FK-ordering
    bug (`create_play_with_attempts` — see `repositories/llm_calls.py`)
    shipped past a 129-test suite specifically because this fixture had
    drifted from production: SQLite does not enforce foreign keys per
    connection unless `PRAGMA foreign_keys=ON` is set explicitly, and this
    function never set it, while `db.py`'s real one always has. Every test
    using `session_factory` ran against a DB that silently accepted
    FK-violating insert order — the real bug was invisible here until a real
    `PRAGMA foreign_keys=ON` connection (the live smoke test's actual
    `groundwork.db`) hit it. Do not let this drift again."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
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
    # No real lifespan runs under `ASGITransport` (deliberately — it would
    # touch the real `groundwork.db`/live provider runtimes), so the one
    # piece of `app.state` a request-time dependency reads unconditionally
    # (`get_executor_id`, Checkpoint I1 Phase 4) needs a stand-in value.
    # `live_runtime`/`live_search_runtime` don't need this: their
    # dependencies default to `None` via `getattr(..., None)`.
    app.state.executor_id = f"test-executor-{uuid.uuid4()}"
    transport = httpx.ASGITransport(app=app)
    # A default `Origin` header matching `settings.cors_origins`' default
    # (`http://localhost:3000`) — stands in for what a real browser always
    # sends on an unsafe request from the actual frontend origin. Without
    # this, every operator-session/Live-mode POST in the suite would need
    # to set it individually to pass Phase 8's Origin/CSRF guard.
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
    ) as c:
        yield c
    app.dependency_overrides.clear()
