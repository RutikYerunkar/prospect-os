from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select

from groundwork.models.tables import RunRow


class RunRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(self, *, play_id: str, mode: str, seed: int) -> str:
        run_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            session.add(RunRow(id=run_id, play_id=play_id, status="RUNNING", mode=mode, seed=seed))
            await session.commit()
        return run_id

    async def set_plan(self, run_id: str, plan: list[Any]) -> None:
        async with self._session_factory() as session:
            row = await session.get(RunRow, run_id)
            row.plan = plan
            await session.commit()

    async def finalize(self, run_id: str, *, status: str, counters: dict[str, Any], error: str | None = None) -> None:
        async with self._session_factory() as session:
            row = await session.get(RunRow, run_id)
            row.status = status
            row.counters = counters
            row.finished_at = datetime.utcnow()
            row.error = error
            await session.commit()

    async def sweep_interrupted(self) -> int:
        """Startup honesty check: any run left RUNNING from a prior process
        crash is marked INTERRUPTED rather than silently left RUNNING."""
        async with self._session_factory() as session:
            result = await session.execute(select(RunRow).where(RunRow.status == "RUNNING"))
            rows = list(result.scalars())
            for row in rows:
                row.status = "INTERRUPTED"
                row.finished_at = datetime.utcnow()
            await session.commit()
            return len(rows)

    async def get(self, run_id: str) -> RunRow | None:
        async with self._session_factory() as session:
            return await session.get(RunRow, run_id)
