"""Alembic environment — Checkpoint I1 Phase 5.

The database URL is never read from `alembic.ini` (which would need a
copy-pasted, drifting duplicate of `DATABASE_URL`). It comes from
`groundwork.config.settings.database_url`, normalized through the exact same
`groundwork.db_url.normalize_database_url` seam the running application uses
— so `alembic upgrade head` and the app connect with identical URL handling
(sslmode/channel_binding included), and a malformed/unsupported
`DATABASE_URL` fails migrations with the same actionable error the app would
raise at startup, not a confusing separate one.

`target_metadata` is `groundwork.models.tables.Base.metadata` — the migration
drift test (`tests/test_migration_drift.py`) runs `alembic upgrade head` then
diffs the resulting schema against this same metadata, so the two can never
silently diverge.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from groundwork.config import settings
from groundwork.db_url import normalize_database_url
from groundwork.models.tables import Base

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """Allows `alembic -x database_url=...` to override
    `settings.database_url` for one invocation — used by the migration
    drift test and by Phase 6's Postgres verification, so neither has to
    mutate process environment variables to point Alembic at a scratch
    database."""
    override = context.get_x_argument(as_dictionary=True).get("database_url")
    return override or settings.database_url


def _normalized_url() -> str:
    url, _connect_args = normalize_database_url(_database_url())
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — emits SQL to stdout, no DBAPI
    needed. Not used by this project's workflow (see docs/RUNBOOK.md) but
    kept for completeness/tooling compatibility."""
    context.configure(
        url=_normalized_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url, connect_args = normalize_database_url(_database_url())
    connectable = create_async_engine(url, poolclass=pool.NullPool, connect_args=connect_args)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
