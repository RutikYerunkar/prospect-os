"""`make demo-reset` — wipe the local SQLite file and reseed the schema.

Checkpoint I1 Phase 5: refuses to run against anything but SQLite. This is a
destructive, unconditional wipe-and-recreate — exactly right for a
disposable local demo database, and exactly wrong to ever point at a shared
Postgres instance (production or otherwise). Schema changes against Postgres
go through `alembic upgrade head` instead — see docs/RUNBOOK.md.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from groundwork.config import settings
from groundwork.db import create_all, engine


class NotSqliteError(RuntimeError):
    pass


async def main() -> None:
    if not settings.database_url.startswith("sqlite"):
        raise NotSqliteError(
            f"demo-reset refuses to run against a non-SQLite DATABASE_URL ({settings.database_url!r}). "
            "This command unconditionally wipes and recreates the schema — safe only for the disposable "
            "local SQLite file. Manage a Postgres schema with `alembic upgrade head` instead."
        )

    await engine.dispose()
    db_path = settings.database_url.split("///")[-1]
    for suffix in ("", "-wal", "-shm"):
        path = Path(db_path + suffix)
        if path.exists():
            path.unlink()
    await create_all()
    print("database reset")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except NotSqliteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
