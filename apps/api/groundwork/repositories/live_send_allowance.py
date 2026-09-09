"""`live_send_allowance_lock` / `live_send_reservations` — the rolling 24h
Live send allowance (V2-I-b, Phase 5).

Correctness transaction, in order, inside ONE database transaction:

1. guarded `UPDATE live_send_allowance_lock SET version = version + 1
   WHERE id = 'default'` — the row-level write lock. `rowcount != 1` (the
   singleton is missing or malformed) fails closed: deny, seed nothing.
2. `COUNT(*)` of `live_send_reservations` with `reserved_at >= now - 24h`.
3. deny if the count has already reached the configured limit.
4. `INSERT` the new reservation.
5. `COMMIT`.

Only after a `GRANTED` outcome may the caller dispatch. Reservations are
NEVER released — not for `PROVEN_NOT_DISPATCHED`, not for
`DEFINITIVE_REJECTION`, not for `SUCCEEDED`, not for `UNCERTAIN`, not for
`ABANDONED`. `asyncio.Semaphore`, if used anywhere above this, is load
shaping only — this transaction is the actual guarantee, exactly like the
recipient-level partial unique index is the actual §3.5B guarantee rather
than `domain/action_policy.py`'s pre-check.

SQLite lock retry: at most `allowance_lock_max_attempts` COMPLETE
transaction attempts (each one restarting from the guard UPDATE, never
resuming mid-transaction); retry exhaustion denies the reservation — grants
nothing, dispatches nothing — rather than raising.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from groundwork.models.tables import LiveSendAllowanceLockRow, LiveSendReservationRow

ALLOWANCE_LOCK_ID = "default"

# Substrings SQLite's DBAPI driver uses for lock-contention errors —
# matched case-insensitively against `str(exc)`. Any OTHER OperationalError
# is a genuine failure and is re-raised immediately, never swallowed as a
# "just retry" case.
_SQLITE_LOCK_MESSAGES = ("database is locked", "database table is locked")


class AllowanceReservationOutcome(StrEnum):
    GRANTED = "GRANTED"
    DENIED_LIMIT_REACHED = "DENIED_LIMIT_REACHED"
    #: The singleton lock row is missing/invalid — fails closed rather than
    #: silently creating it mid-reservation.
    DENIED_GUARD_MISSING = "DENIED_GUARD_MISSING"
    #: Every retry attempt hit SQLite lock contention.
    DENIED_LOCK_CONTENTION = "DENIED_LOCK_CONTENTION"


async def ensure_singleton_seeded(session_factory) -> None:
    """Idempotent: inserts the one `id='default', version=0` row iff it
    doesn't already exist. Called from every schema-creation path
    (`db.py::create_all`, the Alembic migration's own `upgrade()`, and
    `tests/conftest.py`'s per-test schema setup) so the singleton is never
    silently absent."""
    async with session_factory() as session:
        existing = await session.get(LiveSendAllowanceLockRow, ALLOWANCE_LOCK_ID)
        if existing is not None:
            return
        session.add(LiveSendAllowanceLockRow(id=ALLOWANCE_LOCK_ID, version=0))
        await session.commit()


class LiveSendAllowanceRepository:
    def __init__(
        self,
        session_factory,
        *,
        max_attempts: int = 3,
        retry_base_delay_s: float = 0.05,
    ) -> None:
        self._session_factory = session_factory
        self._max_attempts = max(1, max_attempts)
        self._retry_base_delay_s = retry_base_delay_s

    async def try_reserve(
        self, execution_id: str, *, now: datetime, window_s: float, limit: int
    ) -> AllowanceReservationOutcome:
        window_start = now - timedelta(seconds=window_s)
        last_lock_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                return await self._attempt_once(execution_id, now=now, window_start=window_start, limit=limit)
            except OperationalError as exc:
                message = str(exc).lower()
                if not any(m in message for m in _SQLITE_LOCK_MESSAGES):
                    raise
                last_lock_error = exc
                if attempt < self._max_attempts - 1:
                    jitter = random.uniform(0, self._retry_base_delay_s)
                    await asyncio.sleep(self._retry_base_delay_s * (attempt + 1) + jitter)
        assert last_lock_error is not None
        return AllowanceReservationOutcome.DENIED_LOCK_CONTENTION

    async def _attempt_once(
        self, execution_id: str, *, now: datetime, window_start: datetime, limit: int
    ) -> AllowanceReservationOutcome:
        async with self._session_factory() as session:
            guard_result = await session.execute(
                update(LiveSendAllowanceLockRow)
                .where(LiveSendAllowanceLockRow.id == ALLOWANCE_LOCK_ID)
                .values(version=LiveSendAllowanceLockRow.version + 1)
            )
            if guard_result.rowcount != 1:
                await session.rollback()
                return AllowanceReservationOutcome.DENIED_GUARD_MISSING

            count_result = await session.execute(
                select(func.count())
                .select_from(LiveSendReservationRow)
                .where(LiveSendReservationRow.reserved_at >= window_start)
            )
            count = int(count_result.scalar_one())
            if count >= limit:
                await session.rollback()
                return AllowanceReservationOutcome.DENIED_LIMIT_REACHED

            session.add(LiveSendReservationRow(execution_id=execution_id, reserved_at=now))
            await session.commit()
            return AllowanceReservationOutcome.GRANTED

    async def count_reservations_since(self, *, now: datetime, window_s: float) -> int:
        """Read-only helper for the policy pre-check / audit surfaces — never
        itself a reservation."""
        window_start = now - timedelta(seconds=window_s)
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(LiveSendReservationRow)
                .where(LiveSendReservationRow.reserved_at >= window_start)
            )
            return int(result.scalar_one())
