"""Checkpoint I1 Phase 5: `schema_upgrade_problems()`/`migration_status.py`
replaced the old hand-maintained per-checkpoint column/table probe with a
generic Alembic-revision check — is the database's `alembic_version` the
same as the migrations directory's head? Read-only, never mutates, never
resets automatically. Used by `scripts/live_smoke.py` (refuse before any
paid call) and `GET /api/ready` (Phase 9B).

Plain (non-`async def`) test functions throughout: `alembic.command.upgrade`
runs its own internal `asyncio.run()` (see `alembic/env.py`), which raises
if called from inside a loop pytest-asyncio already started for an `async
def` test — so each test below opens its own loop via `asyncio.run()` for
the async parts, exactly like `test_migration_drift.py`.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import contextmanager

from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from groundwork.db import schema_upgrade_problems
from groundwork.migration_status import current_revision, head_revision
from groundwork.models.tables import Base
from tests.test_migration_drift import _config_targeting


@contextmanager
def _fresh_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass


def test_freshly_migrated_database_reports_no_problems():
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(_config_targeting(async_url), "head")

        async def _check():
            test_engine = create_async_engine(async_url)
            try:
                return await schema_upgrade_problems(test_engine)
            finally:
                await test_engine.dispose()

        assert asyncio.run(_check()) == []


def test_brand_new_empty_database_reports_no_problems():
    """An empty DB (no tables, no alembic_version row) is not "stale" —
    `create_all()`/`alembic upgrade head` both handle that case; this only
    flags an *existing*, outdated schema, never a not-yet-initialized one."""
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"

        async def _check():
            test_engine = create_async_engine(async_url)
            try:
                return await schema_upgrade_problems(test_engine)
            finally:
                await test_engine.dispose()

        assert asyncio.run(_check()) == []


def test_database_with_tables_but_no_alembic_version_is_flagged():
    """A pre-Alembic local file: real tables (created via the old
    `create_all()` path), but no `alembic_version` row at all — must be
    flagged, not silently treated as current."""
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"

        async def _check():
            test_engine = create_async_engine(async_url)
            try:
                async with test_engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                return await schema_upgrade_problems(test_engine)
            finally:
                await test_engine.dispose()

        problems = asyncio.run(_check())
        assert problems != []
        assert any("alembic_version" in p for p in problems)


def test_database_behind_head_is_flagged(monkeypatch):
    """Simulates a database stamped at a revision that isn't the migrations
    directory's current head (e.g. a deploy that ran an old migration set)."""
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(_config_targeting(async_url), "head")

        real_head = head_revision()
        monkeypatch.setattr("groundwork.migration_status.head_revision", lambda: "not-" + str(real_head))

        async def _check():
            test_engine = create_async_engine(async_url)
            try:
                return await schema_upgrade_problems(test_engine)
            finally:
                await test_engine.dispose()

        problems = asyncio.run(_check())
        assert problems != []
        assert any("revision" in p for p in problems)


def test_current_revision_matches_head_after_upgrade():
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(_config_targeting(async_url), "head")

        async def _check():
            test_engine = create_async_engine(async_url)
            try:
                return await current_revision(test_engine)
            finally:
                await test_engine.dispose()

        current = asyncio.run(_check())
        assert current == head_revision()
        assert current is not None


def test_never_mutates_the_database():
    with _fresh_db_path() as path:
        async_url = f"sqlite+aiosqlite:///{path}"
        command.upgrade(_config_targeting(async_url), "head")

        async def _tables_before_and_after():
            from sqlalchemy import inspect

            test_engine = create_async_engine(async_url)
            try:
                def _read(sync_conn):
                    return set(inspect(sync_conn).get_table_names())

                async with test_engine.connect() as conn:
                    before = await conn.run_sync(_read)

                await schema_upgrade_problems(test_engine)

                async with test_engine.connect() as conn:
                    after = await conn.run_sync(_read)
                return before, after
            finally:
                await test_engine.dispose()

        before, after = asyncio.run(_tables_before_and_after())
        assert before == after
