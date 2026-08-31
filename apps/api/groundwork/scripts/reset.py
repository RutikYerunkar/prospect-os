"""`make demo-reset` — wipe the local SQLite file and reseed the schema."""

from __future__ import annotations

import asyncio
from pathlib import Path

from groundwork.config import settings
from groundwork.db import create_all, engine


async def main() -> None:
    await engine.dispose()
    if settings.database_url.startswith("sqlite"):
        db_path = settings.database_url.split("///")[-1]
        for suffix in ("", "-wal", "-shm"):
            path = Path(db_path + suffix)
            if path.exists():
                path.unlink()
    await create_all()
    print("database reset")


if __name__ == "__main__":
    asyncio.run(main())
