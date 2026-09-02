"""Alembic-based schema-currency check — Checkpoint I1 Phase 5.

Replaces the old hand-maintained `db.py::_inspect_schema_problems`, which
probed for specific columns/tables named after whichever checkpoint last
changed the schema (`runs.provider_profile`, `llm_calls`, `signals.grounded`,
...) and had to be hand-extended forever after. With a real migration history
in place, "is this database current?" has exactly one general answer: does
its `alembic_version` row match the migrations directory's head revision?

Used by:
- `groundwork/db.py::schema_upgrade_problems()` (kept as the same name/shape
  `scripts/live_smoke.py` already calls, so that script needs no changes).
- `GET /api/ready` (Phase 9B) — a database behind its migration head fails
  readiness, the same "actionable, not a raw driver error" spirit as
  `live_smoke.py`'s pre-flight check.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

_API_ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "alembic"))
    return cfg


def head_revision() -> str | None:
    """The migrations directory's own head — independent of any database."""
    return ScriptDirectory.from_config(_alembic_config()).get_current_head()


async def current_revision(engine: AsyncEngine) -> str | None:
    """The revision stamped in the target database's `alembic_version`
    table, or `None` if that table doesn't exist (a brand-new database that
    was never migrated, or one whose schema predates Alembic entirely —
    `schema_upgrade_problems` below tells those two apart)."""

    def _read(sync_conn):
        return MigrationContext.configure(sync_conn).get_current_revision()

    async with engine.connect() as conn:
        return await conn.run_sync(_read)


async def _has_any_tables(engine: AsyncEngine) -> bool:
    def _inspect(sync_conn) -> bool:
        return len(inspect(sync_conn).get_table_names()) > 0

    async with engine.connect() as conn:
        return await conn.run_sync(_inspect)


async def schema_upgrade_problems(engine: AsyncEngine) -> list[str]:
    """Read-only, never mutates. Returns a human-readable problem list
    (empty = current):

    - A brand-new, empty database (no tables at all) is not "stale" —
      `create_all()`/`alembic upgrade head` both handle that case, so this
      returns no problems for it.
    - A database with real tables but no `alembic_version` row predates
      Alembic entirely (a pre-Phase-5 local file).
    - A database whose `alembic_version` doesn't match the migrations
      directory's head is behind.
    """
    current = await current_revision(engine)
    head = head_revision()

    if current is None:
        if await _has_any_tables(engine):
            return [
                "database has existing tables but no alembic_version row (schema predates Alembic) — "
                "run `alembic upgrade head` (or `make demo-reset` for a disposable local SQLite file)"
            ]
        return []

    if current != head:
        return [
            f"database schema is at revision {current!r}, but the migrations directory's head is "
            f"{head!r} — run `alembic upgrade head`"
        ]

    return []
