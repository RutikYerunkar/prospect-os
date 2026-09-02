"""`runs` — lifecycle + the ownership-safe execution lease (Checkpoint I1
Phase 4).

A run is owned by exactly one API process at a time, identified by
`executor_id` (minted once per process at startup — see `main.py`'s
lifespan). Every lifecycle transition that matters for correctness —
heartbeat, terminal finalize — is a guarded `UPDATE ... WHERE id=:run_id AND
executor_id=:executor_id AND status='RUNNING'`. A zero-rowcount result means
this process has lost the lease (a reaper elsewhere already reclaimed it, or
it never held it) and MUST NOT be treated as success: the caller stops, it
never retries as an unconditional write. That is the entire mechanism that
prevents a stale/killed-and-restarted executor from resurrecting or
finalizing a run it no longer owns — there is deliberately no
`asyncio.Lock`/process-local lock anywhere in this file.

`reap_stale()` replaces the old unconditional `sweep_interrupted()`: instead
of marking every RUNNING row INTERRUPTED on process start (correct only
because the old model assumed exactly one process ever existed), it marks
only rows whose heartbeat has gone stale — safe to call at startup AND
periodically, safe to call from multiple overlapping processes at once (the
UPDATE's own `WHERE status='RUNNING'` means only the first committer
actually flips a given row), and it never touches a row another process is
still genuinely heartbeating.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update

from groundwork.models.tables import RunRow
from groundwork.observability.redact import redact
from groundwork.timeutil import utcnow

logger = logging.getLogger(__name__)

# The terminal-write guard: only a run this executor still owns (RUNNING,
# and stamped with this process's executor_id) may be finalized/heartbeat by
# it. Shared by `heartbeat` and `finalize_owned` so the two can't drift.
_OWNED_RUNNING = lambda run_id, executor_id: (  # noqa: E731
    RunRow.id == run_id,
    RunRow.executor_id == executor_id,
    RunRow.status == "RUNNING",
)


class RunRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        play_id: str,
        mode: str,
        seed: int,
        provider_profile: dict[str, Any] | None = None,
        executor_id: str | None = None,
    ) -> str:
        """`executor_id` is the creating process's lease claim, taken out
        immediately — this app runs one in-process executor per run, so the
        process that creates a run is also the process that will drive it.
        Callers that don't participate in lease ownership (scripts, tests
        exercising the engine directly) may omit it; a random one-off id is
        minted so the row is still schema-valid, it just never gets
        heartbeated or ownership-checked by anything that cares."""
        run_id = str(uuid.uuid4())
        now = utcnow()
        async with self._session_factory() as session:
            session.add(
                RunRow(
                    id=run_id,
                    play_id=play_id,
                    status="RUNNING",
                    mode=mode,
                    seed=seed,
                    provider_profile=provider_profile or {},
                    executor_id=executor_id or str(uuid.uuid4()),
                    heartbeat_at=now,
                )
            )
            await session.commit()
        return run_id

    async def set_plan(self, run_id: str, plan: list[Any]) -> None:
        async with self._session_factory() as session:
            row = await session.get(RunRow, run_id)
            row.plan = plan
            await session.commit()

    async def heartbeat(self, run_id: str, executor_id: str) -> bool:
        """`True` iff this process still owns the run and the heartbeat
        landed. `False` means the lease is gone — the caller (see
        `api/run_service.py`'s heartbeat loop) must stop, not retry."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(RunRow).where(*_OWNED_RUNNING(run_id, executor_id)).values(heartbeat_at=utcnow())
            )
            await session.commit()
            return result.rowcount == 1

    async def finalize_owned(
        self, run_id: str, executor_id: str, *, status: str, counters: dict[str, Any], error: str | None = None
    ) -> bool:
        """The ownership-guarded terminal transition — what every real,
        API-launched run finalizes through. `False` means this executor no
        longer owns the run (a reaper already interrupted it, or another
        process holds the lease now); the caller must NOT fall back to an
        unconditional write — that would be exactly the "stale executor
        resurrects/finalizes a run it lost" bug this phase exists to
        prevent. It also must not raise: losing a race with the reaper is
        an expected outcome, not an application error."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(RunRow)
                .where(*_OWNED_RUNNING(run_id, executor_id))
                # Checkpoint I1 Phase 9: `error` here is typically
                # `str(exception)` from `api/run_service.py`'s catch-all —
                # redacted at this persistence boundary, the same choke
                # point `llm_calls`/`search_calls`/`agent_tasks` already
                # route through, so no caller needs to remember to redact
                # before calling this.
                .values(status=status, counters=counters, error=redact(error), finished_at=utcnow(), executor_id=None)
            )
            await session.commit()
            return result.rowcount == 1

    async def finalize(self, run_id: str, *, status: str, counters: dict[str, Any], error: str | None = None) -> None:
        """Unconditional finalize, kept for callers that don't participate
        in lease ownership at all (headless scripts, tests exercising
        `engine/runner.py::execute_run` directly without a real
        process-scoped `executor_id`). Every real API-launched run goes
        through `finalize_owned` instead — see `api/run_service.py`."""
        async with self._session_factory() as session:
            row = await session.get(RunRow, run_id)
            row.status = status
            row.counters = counters
            row.finished_at = utcnow()
            row.error = redact(error)
            row.executor_id = None
            await session.commit()

    async def reap_stale(self, stale_before: datetime) -> list[str]:
        """Marks every RUNNING run whose heartbeat is older than
        `stale_before` (or was never set — a row from before this feature
        existed, or one that crashed before its first heartbeat) as
        INTERRUPTED, clearing `executor_id` atomically in the same UPDATE.
        Returns the ids actually interrupted. Never auto-resumes provider
        work — a rerun is a new run, always.

        Runs on ANY genuinely-stale row regardless of which executor
        claims it — a dead process's runs are exactly the ones whose
        heartbeat stopped advancing, so this needs no notion of "my"
        executor id to be correct. A row with a recent heartbeat (a
        genuinely live, possibly-overlapping process) is never touched,
        which is what makes this safe to call at both startup and on a
        periodic interval, from any number of concurrent processes, without
        an old process's still-running work ever getting cut off just
        because a new process also happened to start up.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                update(RunRow)
                .where(
                    RunRow.status == "RUNNING",
                    or_(RunRow.heartbeat_at.is_(None), RunRow.heartbeat_at < stale_before),
                )
                .values(
                    status="INTERRUPTED",
                    finished_at=utcnow(),
                    executor_id=None,
                    error="run interrupted — executor lease went stale (process likely crashed or was killed)",
                )
                .returning(RunRow.id)
            )
            interrupted = [row[0] for row in result.all()]
            await session.commit()
            return interrupted

    async def interrupt_owned_by_executor(self, executor_id: str) -> list[str]:
        """Shutdown-time force transition (Phase 4): every RUNNING row THIS
        process still owns — regardless of heartbeat freshness, since the
        process is going away right now, not because its heartbeat went
        stale — becomes INTERRUPTED. The `executor_id` equality in the
        WHERE clause is the ownership guard: if the reaper (or another
        process) already reclaimed a given row between the drain window
        ending and this call running, that row's `executor_id` no longer
        matches and it's silently skipped rather than double-transitioned.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                update(RunRow)
                .where(RunRow.executor_id == executor_id, RunRow.status == "RUNNING")
                .values(
                    status="INTERRUPTED",
                    finished_at=utcnow(),
                    executor_id=None,
                    error="run interrupted — API process shutting down",
                )
                .returning(RunRow.id)
            )
            interrupted = [row[0] for row in result.all()]
            await session.commit()
            return interrupted

    async def count_active_by_mode(self, mode: str) -> int:
        """Checkpoint I1 Phase 8B — backs `LIVE_MAX_ACTIVE_RUNS`. Counts
        RUNNING rows of the given mode right now, straight from the
        database — no in-process counter to drift from reality across
        restarts or (if this ever runs on more than one process) across
        processes."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(RunRow).where(RunRow.status == "RUNNING", RunRow.mode == mode)
            )
            return result.scalar_one()

    async def count_started_since(self, mode: str, since: datetime) -> int:
        """Checkpoint I1 Phase 8B — backs `LIVE_DAILY_RUN_ALLOWANCE`. A
        rolling window (`since` is typically "now - 24h"), not a
        calendar-day counter reset at UTC midnight — simpler to compute
        correctly (one DB predicate, no timezone-of-day edge cases) and
        avoids a burst-at-midnight gaming vector a fixed reset would allow.
        DB-backed by construction (no Redis, no in-process counter)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(RunRow).where(RunRow.mode == mode, RunRow.started_at >= since)
            )
            return result.scalar_one()

    async def get(self, run_id: str) -> RunRow | None:
        async with self._session_factory() as session:
            return await session.get(RunRow, run_id)

    async def for_play(self, play_id: str) -> list[RunRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(RunRow).where(RunRow.play_id == play_id).order_by(RunRow.started_at.desc())
            )
            return list(result.scalars())
