"""`groundwork.db.create_engine` — proves the normalization seam is actually
wired in (not just unit-tested in isolation in `test_db_url.py`), and that a
bad `DATABASE_URL` fails at engine-construction time (startup), not deep
inside a request. Never opens a real network connection — `create_async_engine`
is lazy for both dialects."""

from __future__ import annotations

import pytest

from groundwork.db import create_engine
from groundwork.db_url import DatabaseConfigurationError


@pytest.mark.asyncio
async def test_create_engine_sqlite_default():
    engine = create_engine("sqlite+aiosqlite:///./_test_engine_construction.db")
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_engine_postgres_url_constructs_without_connecting():
    engine = create_engine("postgresql://user:pass@nonexistent-host:5432/dbname?sslmode=require")
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
    finally:
        await engine.dispose()


def test_create_engine_rejects_unsupported_query_parameter():
    with pytest.raises(DatabaseConfigurationError):
        create_engine("postgresql://user:pass@host/dbname?options=-c%20timezone%3DUTC")


def test_create_engine_rejects_malformed_scheme():
    with pytest.raises(DatabaseConfigurationError):
        create_engine("mysql://user:pass@host/dbname")
