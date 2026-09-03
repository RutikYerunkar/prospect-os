"""Checkpoint I1 Phase 5 — migration drift.

`alembic upgrade head` against a fresh, empty database must produce a schema
that is byte-for-byte what `Base.metadata` (the ORM models) says it should
be — no unexplained autogenerate diff. This is what keeps
`models/tables.py` and `alembic/versions/*.py` from silently drifting apart
after a model change ships without a matching migration.

Runs against SQLite unconditionally; when `GROUNDWORK_TEST_POSTGRES_DSN` is
set (Phase 6 — a real local Postgres instance/container, never cloud), the
identical `alembic upgrade head` -> `compare_metadata` check also runs
against it, proving the migration produces a byte-identical schema on both
dialects, not just SQLite.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine as create_sync_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from groundwork.models.tables import Base
from tests.dialect_helpers import postgres_dsn

_API_ROOT = Path(__file__).resolve().parent.parent


def _config_targeting(async_db_url: str) -> Config:
    """An Alembic `Config` pointed at THIS repo's `alembic/` directory, with
    `-x database_url=<async_db_url>` set programmatically — alembic/env.py
    reads exactly that `-x` argument instead of `settings.database_url`, so
    this never touches the real dev `groundwork.db`."""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "alembic"))
    cfg.cmd_opts = type("Opts", (), {"x": [f"database_url={async_db_url}"]})()
    return cfg


@pytest.fixture
def sqlite_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # alembic/sqlalchemy create it fresh
    yield path
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def test_alembic_upgrade_head_matches_orm_metadata_exactly(sqlite_path):
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")

    sync_engine = create_sync_engine(f"sqlite:///{sqlite_path}")
    try:
        with sync_engine.connect() as connection:
            migration_ctx = MigrationContext.configure(connection)
            diff = compare_metadata(migration_ctx, Base.metadata)
        # A non-empty diff means either the migration is missing something
        # the models declare, or the models are missing something the
        # migration created — either way, drift.
        assert diff == [], f"schema drift between alembic head and ORM metadata: {diff!r}"
    finally:
        sync_engine.dispose()


def test_alembic_history_has_exactly_one_head():
    """More than one head means a branched migration history — the
    `alembic upgrade head` command used everywhere else in this project
    (tests, CI, deploy) is ambiguous/wrong the moment that happens."""
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"expected exactly one alembic head, found {heads!r}"


def test_alembic_upgrade_head_is_idempotent(sqlite_path):
    """Running `upgrade head` twice against the same database must be a
    no-op the second time, not an error — this is what makes the command
    safe to run unconditionally as a deploy step."""
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")
    command.upgrade(_config_targeting(async_url), "head")  # must not raise


def test_alembic_upgrade_head_matches_orm_metadata_on_postgres():
    """The Postgres half of the drift guarantee (Phase 6) — skipped unless
    a real local Postgres target is configured via
    `GROUNDWORK_TEST_POSTGRES_DSN`; CI's Postgres service container (Phase
    10B) sets this so the check always runs there."""
    dsn = postgres_dsn()
    if not dsn:
        pytest.skip("GROUNDWORK_TEST_POSTGRES_DSN not set — no local Postgres target configured")

    async def _drop_all() -> None:
        from sqlalchemy import text

        engine = create_async_engine(dsn)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                # `alembic_version` isn't part of `Base.metadata` — drop it
                # too, or a stale stamp from a previous run makes the next
                # `upgrade head` below a silent no-op against a database
                # that (thanks to the drop_all above) actually has no
                # tables at all.
                await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        finally:
            await engine.dispose()

    async def _diff() -> list:
        engine = create_async_engine(dsn)
        try:
            async with engine.connect() as conn:
                def _compute(sync_conn):
                    return compare_metadata(MigrationContext.configure(sync_conn), Base.metadata)

                return await conn.run_sync(_compute)
        finally:
            await engine.dispose()

    asyncio.run(_drop_all())  # start from a clean slate — a shared, reused test database
    try:
        command.upgrade(_config_targeting(dsn), "head")
        diff = asyncio.run(_diff())
        assert diff == [], f"schema drift between alembic head and ORM metadata (Postgres): {diff!r}"
    finally:
        asyncio.run(_drop_all())


# =====================================================================
# V2-B migration compatibility coverage (Part 13/§K).
#
# Proves the *migrated* schema behaves as V2-B's acceptance criteria
# require: a pre-existing v1-shaped `approvals` row stays a valid
# PROSPECT-scope row with the three new columns NULL, and an ACTION-scope
# row missing any of the three required fields is rejected by the CHECK
# constraint — the structural enforcement, not a convention. Runs against
# `alembic upgrade head` output (not `create_all()`), because the
# acceptance criterion is specifically about the migration path.
# =====================================================================


def _seed_minimal_prospect_chain(connection, *, table_for) -> str:
    """Inserts the minimal FK chain (play -> run -> company -> prospect)
    every `approvals` row needs, using the real ORM table objects so column
    lists never drift from `models/tables.py`. Returns the new prospect id."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    play_id, run_id, company_id, prospect_id = (str(uuid.uuid4()) for _ in range(4))

    connection.execute(
        table_for("plays").insert(),
        {
            "id": play_id,
            "name": "n",
            "objective_text": "o",
            "icp_spec": {},
            "mode": "demo",
            "created_at": now,
        },
    )
    connection.execute(
        table_for("runs").insert(),
        {
            "id": run_id,
            "play_id": play_id,
            "status": "COMPLETED",
            "mode": "demo",
            "seed": 1,
            "plan": [],
            "counters": {},
            "provider_profile": {},
            "started_at": now,
            "last_event_seq": 0,
        },
    )
    connection.execute(
        table_for("companies").insert(),
        {
            "id": company_id,
            "canonical_domain": f"{company_id}.example",
            "normalized_name": "x",
            "display_name": "X",
            "profile": {},
            "origin": "demo_fixture",
            "first_seen_at": now,
        },
    )
    connection.execute(
        table_for("prospects").insert(),
        {
            "id": prospect_id,
            "run_id": run_id,
            "company_id": company_id,
            "status": "PASS",
            "current_stage": "DONE",
            "dedupe_key": prospect_id,
            "created_at": now,
        },
    )
    return prospect_id


def test_v1_prospect_scope_approval_survives_migration_unchanged(sqlite_path):
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")

    sync_engine = create_sync_engine(f"sqlite:///{sqlite_path}")
    try:
        approvals = Base.metadata.tables["approvals"]
        with sync_engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            prospect_id = _seed_minimal_prospect_chain(
                connection, table_for=lambda name: Base.metadata.tables[name]
            )
            approval_id = str(uuid.uuid4())
            # A pre-v2 write site never sets scope/action_proposal_id/
            # content_hash/hash_version — exactly like every real v1
            # `/approve` call site still doesn't.
            connection.execute(
                approvals.insert(),
                {
                    "id": approval_id,
                    "prospect_id": prospect_id,
                    "decision": "approve",
                    "actor": "demo_user",
                    "decided_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                },
            )

        with sync_engine.connect() as connection:
            row = connection.execute(
                approvals.select().where(approvals.c.id == approval_id)
            ).mappings().one()
            assert row["scope"] == "PROSPECT"
            assert row["action_proposal_id"] is None
            assert row["content_hash"] is None
            assert row["hash_version"] is None
    finally:
        sync_engine.dispose()


def test_action_scope_approval_missing_required_field_is_rejected(sqlite_path):
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")

    sync_engine = create_sync_engine(f"sqlite:///{sqlite_path}")
    try:
        approvals = Base.metadata.tables["approvals"]
        with sync_engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.commit()
            prospect_id = _seed_minimal_prospect_chain(
                connection, table_for=lambda name: Base.metadata.tables[name]
            )
            connection.commit()

            # scope='ACTION' with all three required fields NULL must be
            # rejected by ck_approvals_action_scope_complete — the CHECK
            # constraint, not application-level validation.
            with pytest.raises(IntegrityError):
                connection.execute(
                    approvals.insert(),
                    {
                        "id": str(uuid.uuid4()),
                        "prospect_id": prospect_id,
                        "decision": "approve",
                        "actor": "demo_user",
                        "decided_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                        "scope": "ACTION",
                    },
                )
                connection.commit()
    finally:
        sync_engine.dispose()


def test_migrated_schema_has_action_scope_check_constraint(sqlite_path):
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")

    sync_engine = create_sync_engine(f"sqlite:///{sqlite_path}")
    try:
        with sync_engine.connect() as connection:
            inspector = inspect(connection)
            check_names = {c["name"] for c in inspector.get_check_constraints("approvals")}
            assert "ck_approvals_action_scope_complete" in check_names
    finally:
        sync_engine.dispose()


def test_migrated_schema_has_live_recipient_partial_unique_index(sqlite_path):
    async_url = f"sqlite+aiosqlite:///{sqlite_path}"
    command.upgrade(_config_targeting(async_url), "head")

    sync_engine = create_sync_engine(f"sqlite:///{sqlite_path}")
    try:
        with sync_engine.connect() as connection:
            row = connection.execute(
                text("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_action_executions_live_recipient'")
            ).fetchone()
            assert row is not None, "uq_action_executions_live_recipient index missing from migrated schema"
            sql = row[0]
            assert "UNIQUE" in sql
            assert "LIVE_EXTERNAL" in sql
            assert "EMAIL_SEND" in sql
            for status in ("CLAIMED", "IN_FLIGHT", "SUCCEEDED", "UNCERTAIN", "ABANDONED"):
                assert status in sql
            # FAILED is deliberately excluded — it is the only state that
            # frees a recipient identity (§3.5B).
            assert "FAILED" not in sql
    finally:
        sync_engine.dispose()
