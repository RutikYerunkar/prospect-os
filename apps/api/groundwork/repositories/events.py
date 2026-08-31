"""`run_events` — the durable, append-only event log SSE will replay in
Checkpoint C. Never updated, only inserted. `after_seq` stays a resumable
cursor because the DB, not a socket, is the source of truth."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from groundwork.models.tables import RunEventRow


class EventRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def append(self, *, run_id: str, type: str, prospect_id: str | None, payload: dict[str, Any]) -> int:
        async with self._session_factory() as session:
            row = RunEventRow(run_id=run_id, type=type, prospect_id=prospect_id, payload=payload)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.seq

    async def after(self, run_id: str, after_seq: int) -> list[RunEventRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.seq > after_seq)
                .order_by(RunEventRow.seq)
            )
            return list(result.scalars())
