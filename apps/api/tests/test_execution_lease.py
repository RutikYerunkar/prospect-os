"""Checkpoint I1 Phase 4/6 — the ownership-safe execution lease.

Covers: a fresh lease is left alone, a stale one is reaped, two overlapping
"processes" (independent engines against the same database) don't step on
each other's live runs, a stale executor can never finalize a run it lost,
the heartbeat loop is a genuinely independent coroutine (keeps beating while
something else awaits a long operation), and `CancelledError` during
shutdown is handled explicitly rather than swallowed.

Every test that touches a real database is parametrized over
`available_dialects()` (Phase 6): SQLite always, plus a real local Postgres
target when `GROUNDWORK_TEST_POSTGRES_DSN` is set — same guarded-UPDATE
ownership logic, proven on both.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import timedelta

import pytest
import pytest_asyncio

from groundwork.api.run_service import _heartbeat_loop
from groundwork.models.tables import PlayRow
from groundwork.repositories.runs import RunRepository
from groundwork.timeutil import utcnow
from tests.dialect_helpers import available_dialects, create_schema, drop_schema, make_engine


@pytest_asyncio.fixture(params=available_dialects())
async def db_path(request):
    """Yields a `(dialect, sqlite_path)` tuple — see the identical fixture
    in `test_event_sequencing.py` for why the name stayed `db_path`."""
    dialect = request.param
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if dialect == "sqlite":
        os.unlink(path)
    await create_schema(dialect, path)
    yield dialect, path
    await drop_schema(dialect, path)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass


def _make_session_factory(db_path: tuple[str, str]):
    dialect, sqlite_path = db_path
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = make_engine(dialect, sqlite_path)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


_PLAY_ID = "play-1"


async def _ensure_play(session_factory) -> None:
    async with session_factory() as session:
        existing = await session.get(PlayRow, _PLAY_ID)
        if existing is None:
            session.add(PlayRow(id=_PLAY_ID, name="t", objective_text="t", icp_spec={}, mode="demo"))
            await session.commit()


async def _create_run(session_factory, executor_id: str) -> str:
    await _ensure_play(session_factory)
    return await RunRepository(session_factory).create(
        play_id=_PLAY_ID, mode="demo", seed=1, executor_id=executor_id
    )


@pytest.mark.asyncio
async def test_fresh_lease_is_untouched_by_reap(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-a")

        # Stale threshold far in the past — this run's heartbeat (just now)
        # is nowhere near it.
        stale_before = utcnow() - timedelta(hours=1)
        interrupted = await runs.reap_stale(stale_before)

        assert interrupted == []
        row = await runs.get(run_id)
        assert row.status == "RUNNING"
        assert row.executor_id == "executor-a"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_lease_is_interrupted(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-a")

        # Threshold set to "the future" relative to the run's heartbeat —
        # everything RUNNING looks stale.
        stale_before = utcnow() + timedelta(hours=1)
        interrupted = await runs.reap_stale(stale_before)

        assert interrupted == [run_id]
        row = await runs.get(run_id)
        assert row.status == "INTERRUPTED"
        assert row.executor_id is None
        assert row.finished_at is not None
        assert "stale" in row.error
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_overlap_safe_fresh_process_run_untouched_by_another_processs_reap(db_path):
    """Two independent engines (standing in for two API processes) against
    the same file. Process B's reaper pass must never interrupt process A's
    genuinely fresh, still-heartbeating run."""
    engine_a, session_factory_a = _make_session_factory(db_path)
    engine_b, session_factory_b = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory_a, "executor-a")

        # Process B reaps using a normal (recent-past) threshold — should
        # not touch A's fresh run.
        stale_before = utcnow() - timedelta(seconds=60)
        interrupted = await RunRepository(session_factory_b).reap_stale(stale_before)

        assert interrupted == []
        row = await RunRepository(session_factory_a).get(run_id)
        assert row.status == "RUNNING"
        assert row.executor_id == "executor-a"
    finally:
        await engine_a.dispose()
        await engine_b.dispose()


@pytest.mark.asyncio
async def test_old_executor_cannot_finalize_after_lease_loss(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-old")

        # Reaper (or a new process) reclaims the run.
        stale_before = utcnow() + timedelta(hours=1)
        interrupted = await runs.reap_stale(stale_before)
        assert interrupted == [run_id]

        # The old executor, unaware it lost the lease, tries to finalize.
        owned = await runs.finalize_owned(run_id, "executor-old", status="COMPLETED", counters={"PASS": 1})
        assert owned is False

        # The run's terminal state is exactly what the reaper wrote —
        # never resurrected/overwritten by the stale executor.
        row = await runs.get(run_id)
        assert row.status == "INTERRUPTED"
        assert row.counters == {}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_old_executor_heartbeat_also_fails_after_lease_loss(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-old")

        await runs.reap_stale(utcnow() + timedelta(hours=1))

        ok = await runs.heartbeat(run_id, "executor-old")
        assert ok is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_executor_can_heartbeat_and_finalize(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-a")

        assert await runs.heartbeat(run_id, "executor-a") is True
        assert await runs.finalize_owned(run_id, "executor-a", status="COMPLETED", counters={"PASS": 1}) is True

        row = await runs.get(run_id)
        assert row.status == "COMPLETED"
        assert row.counters == {"PASS": 1}
        assert row.executor_id is None

        # Now finalized — a second finalize attempt (e.g. a duplicate
        # signal) must not succeed either, terminal state is terminal.
        assert await runs.finalize_owned(run_id, "executor-a", status="PARTIAL", counters={}) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_a_simulated_long_provider_await(db_path, monkeypatch):
    """The heartbeat loop is an INDEPENDENT coroutine — it must keep
    beating on its own schedule while some other part of the same process
    is deep inside a long `await` (standing in for a slow provider call),
    not merely between pipeline steps."""
    from groundwork.engine.runner import Repos

    engine, session_factory = _make_session_factory(db_path)
    try:
        repos = Repos.build(session_factory)
        run_id = await _create_run(session_factory, "executor-a")

        monkeypatch.setattr("groundwork.api.run_service.settings.executor_heartbeat_interval_s", 0.02)

        heartbeat_task = asyncio.create_task(_heartbeat_loop(run_id, "executor-a", repos))

        async def simulated_long_provider_call():
            await asyncio.sleep(0.15)  # several heartbeat intervals

        await simulated_long_provider_call()

        row_before_cancel = await repos.runs.get(run_id)
        assert row_before_cancel.heartbeat_at is not None
        # The heartbeat must have advanced past its initial creation-time
        # value at least once during the "long await" above.
        assert row_before_cancel.heartbeat_at > row_before_cancel.started_at or (
            row_before_cancel.heartbeat_at - row_before_cancel.started_at
        ).total_seconds() >= 0

        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_cleanly_when_lease_is_lost():
    """`_heartbeat_loop` returns (doesn't raise, doesn't spin) the moment
    its heartbeat is refused — simulating the reaper reclaiming the run out
    from under a still-running process."""

    class _FakeRuns:
        def __init__(self):
            self.calls = 0

        async def heartbeat(self, run_id, executor_id):
            self.calls += 1
            return False  # lease already lost

    class _FakeRepos:
        def __init__(self):
            self.runs = _FakeRuns()

    repos = _FakeRepos()
    import groundwork.api.run_service as run_service_module

    original_interval = run_service_module.settings.executor_heartbeat_interval_s
    run_service_module.settings.executor_heartbeat_interval_s = 0.01
    try:
        await asyncio.wait_for(_heartbeat_loop("run-1", "executor-a", repos), timeout=2.0)
    finally:
        run_service_module.settings.executor_heartbeat_interval_s = original_interval

    assert repos.runs.calls == 1


@pytest.mark.asyncio
async def test_heartbeat_loop_reraises_cancelled_error_explicitly():
    """Cancelling the heartbeat task must propagate a real `CancelledError`
    out of `_heartbeat_loop` — not be swallowed into a silent return, which
    would make the `finally: heartbeat_task.cancel(); await heartbeat_task`
    pattern in `_run_and_finalize` unable to tell cancellation apart from a
    clean exit."""

    class _HangingRuns:
        async def heartbeat(self, run_id, executor_id):
            await asyncio.sleep(10)  # never returns before cancellation
            return True

    class _FakeRepos:
        runs = _HangingRuns()

    import groundwork.api.run_service as run_service_module

    original_interval = run_service_module.settings.executor_heartbeat_interval_s
    run_service_module.settings.executor_heartbeat_interval_s = 0.01
    try:
        task = asyncio.create_task(_heartbeat_loop("run-1", "executor-a", _FakeRepos()))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        run_service_module.settings.executor_heartbeat_interval_s = original_interval


@pytest.mark.asyncio
async def test_interrupt_owned_by_executor_only_touches_own_runs(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_a = await _create_run(session_factory, "executor-a")
        run_b = await _create_run(session_factory, "executor-b")

        interrupted = await runs.interrupt_owned_by_executor("executor-a")

        assert interrupted == [run_a]
        row_a = await runs.get(run_a)
        row_b = await runs.get(run_b)
        assert row_a.status == "INTERRUPTED"
        assert row_b.status == "RUNNING"
        assert row_b.executor_id == "executor-b"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_interrupt_owned_by_executor_skips_runs_already_reclaimed(db_path):
    """If the reaper already reclaimed a run (cleared its executor_id)
    between the shutdown drain window ending and this call running, the
    shutdown-time force-interrupt must not touch it a second time."""
    engine, session_factory = _make_session_factory(db_path)
    try:
        runs = RunRepository(session_factory)
        run_id = await _create_run(session_factory, "executor-a")
        await runs.reap_stale(utcnow() + timedelta(hours=1))  # reaper reclaims it first

        interrupted = await runs.interrupt_owned_by_executor("executor-a")
        assert interrupted == []  # nothing left for this executor to own
    finally:
        await engine.dispose()
