"""`run_events` — the durable, append-only event log SSE replays (Checkpoint
C; database-correct sequencing added Checkpoint I1 Phase 3).

`seq` is minted by an atomic `UPDATE runs SET last_event_seq =
last_event_seq + 1 WHERE id = :run_id RETURNING last_event_seq`, in the same
transaction as the `run_events` insert it guards, committed once. This is
the entire correctness mechanism:

- No `asyncio.Lock`/process-local lock anywhere near this — the UPDATE takes
  a row lock on that one `runs` row for the transaction's duration (both
  SQLite/WAL and Postgres do this natively), so two concurrent appends to
  the SAME run serialize through the database itself, while appends to
  DIFFERENT runs never contend (different rows, no shared lock).
- No `SELECT MAX(seq)` — that pattern is exactly the TOCTOU race this
  design avoids: two concurrent readers of a MAX could compute the same
  "next" value before either writes.
- A run that doesn't exist means the UPDATE matches zero rows: `append()`
  rolls back and fails closed (`RunNotFoundError`) rather than inserting an
  event with a fabricated/`None` seq.
- If the transaction rolls back for any other reason (e.g. the insert
  itself fails a constraint), the `last_event_seq` increment rolls back
  with it — nothing was ever committed, so no reader can observe a gap, and
  the next successful append reuses that same seq number. There is no
  "permanently consumed but invisible" cursor value.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from groundwork.models.tables import RunEventRow, RunRow


class RunNotFoundError(Exception):
    """Raised by `EventRepository.append()` when `run_id` doesn't exist —
    fail closed rather than mint a seq for a row that was never reserved."""


class EventRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def append(self, *, run_id: str, type: str, prospect_id: str | None, payload: dict[str, Any]) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(last_event_seq=RunRow.last_event_seq + 1)
                .returning(RunRow.last_event_seq)
            )
            row = result.first()
            if row is None:
                # No matching run — nothing to roll back to commit (the
                # UPDATE matched zero rows), but be explicit rather than
                # rely on the session's implicit close-without-commit.
                await session.rollback()
                raise RunNotFoundError(f"no run with id {run_id!r} — cannot append an event to it")

            seq = row[0]
            session.add(
                RunEventRow(run_id=run_id, seq=seq, type=type, prospect_id=prospect_id, payload=payload)
            )
            await session.commit()
            return seq

    async def after(self, run_id: str, after_seq: int) -> list[RunEventRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.seq > after_seq)
                .order_by(RunEventRow.seq)
            )
            return list(result.scalars())
