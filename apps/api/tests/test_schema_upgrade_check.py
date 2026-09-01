"""Post-smoke-test hardening: `db.py::schema_upgrade_problems()` detects a
pre-Checkpoint-G local SQLite file (missing `runs.provider_profile` and/or
`llm_calls`) so `live_smoke.py` can refuse with an actionable message
BEFORE making any paid API call, instead of surfacing a raw
`sqlite3.OperationalError` mid-run. Read-only — never mutates, never
resets automatically.
"""

from __future__ import annotations

import os
import tempfile

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from groundwork.db import schema_upgrade_problems
from groundwork.models.tables import Base


@pytest_asyncio.fixture
async def pre_checkpoint_g_engine():
    """A minimal hand-built schema mimicking a pre-Checkpoint-G local DB:
    `runs` exists but without `provider_profile`, and `llm_calls` doesn't
    exist at all."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE runs (id VARCHAR PRIMARY KEY, play_id VARCHAR, status VARCHAR, "
                "mode VARCHAR, seed INTEGER, plan JSON, counters JSON, started_at DATETIME, "
                "finished_at DATETIME, error TEXT)"
            )
        )
    yield test_engine
    await test_engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


async def test_detects_missing_provider_profile_column_and_llm_calls_table(pre_checkpoint_g_engine):
    problems = await schema_upgrade_problems(pre_checkpoint_g_engine)
    assert any("provider_profile" in p for p in problems)
    assert any("llm_calls" in p for p in problems)


async def test_current_schema_reports_no_problems(session_factory):
    # `session_factory`'s underlying engine (conftest.py) was created via
    # `Base.metadata.create_all` — the full current schema.
    engine = session_factory.kw["bind"]
    problems = await schema_upgrade_problems(engine)
    assert problems == []


async def test_brand_new_empty_database_reports_no_problems():
    """An empty DB (no tables at all yet) is not "stale" — `create_all()`
    handles that case; this function only flags an *existing*, outdated
    schema, never a not-yet-initialized one."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        problems = await schema_upgrade_problems(test_engine)
        assert problems == []
    finally:
        await test_engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass


@pytest_asyncio.fixture
async def pre_h1_engine():
    """A schema mimicking a pre-H1 local DB: the full Checkpoint G schema,
    but `signals` predates the H1 `grounded` column and `search_calls`/
    `source_documents` don't exist yet."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    test_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with test_engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE runs (id VARCHAR PRIMARY KEY, play_id VARCHAR, status VARCHAR, "
                "mode VARCHAR, seed INTEGER, plan JSON, counters JSON, provider_profile JSON, "
                "started_at DATETIME, finished_at DATETIME, error TEXT)"
            )
        )
        await conn.execute(text("CREATE TABLE llm_calls (id VARCHAR PRIMARY KEY)"))
        await conn.execute(
            text(
                "CREATE TABLE signals (id VARCHAR PRIMARY KEY, prospect_id VARCHAR, type VARCHAR, "
                "summary TEXT, occurred_at DATETIME, confidence FLOAT, evidence_ids JSON)"
            )
        )
    yield test_engine
    await test_engine.dispose()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


async def test_detects_missing_h1_search_tables_and_signals_grounded_column(pre_h1_engine):
    problems = await schema_upgrade_problems(pre_h1_engine)
    assert any("search_calls" in p for p in problems)
    assert any("source_documents" in p for p in problems)
    assert any("signals.grounded" in p for p in problems)
    # This fixture's `runs`/`llm_calls` ARE current — must not false-positive.
    assert not any("provider_profile" in p for p in problems)
    assert not any(p == "llm_calls table is missing" for p in problems)


async def test_never_mutates_the_database(pre_checkpoint_g_engine):
    await schema_upgrade_problems(pre_checkpoint_g_engine)
    async with pre_checkpoint_g_engine.connect() as conn:
        result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        tables = {row[0] for row in result}
    assert tables == {"runs"}  # unchanged — no llm_calls table was created, no column added
