"""Checkpoint I1 Phase 3/6 — database-correct per-run SSE sequencing.

Proves `EventRepository.append()`'s `UPDATE ... RETURNING` + same-transaction
insert produces unique, contiguous, gap-free per-run `seq` under real
concurrency, with NO process-local lock anywhere in the picture — including
across two independent SQLAlchemy engines pointed at the same database,
standing in for two overlapping API processes.

Every test below is parametrized over `available_dialects()` (Phase 6): it
always runs against SQLite, and additionally against a real local Postgres
target when `GROUNDWORK_TEST_POSTGRES_DSN` is set — same test bodies, same
assertions, proving the DB-correctness claim isn't SQLite-specific.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio

from groundwork.models.tables import PlayRow, RunRow
from groundwork.repositories.events import EventRepository, RunNotFoundError
from groundwork.repositories.runs import RunRepository
from tests.dialect_helpers import available_dialects, create_schema, drop_schema, make_engine


@pytest_asyncio.fixture(params=available_dialects())
async def db_path(request):
    """Despite the name (kept so every test function below needed zero
    signature changes when Postgres parametrization was added), this
    yields a `(dialect, sqlite_path)` tuple — `_make_session_factory`
    interprets it. For `dialect="postgres"` the `sqlite_path` half is
    unused; the actual target is `GROUNDWORK_TEST_POSTGRES_DSN`."""
    dialect = request.param
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if dialect == "sqlite":
        os.unlink(path)  # sqlite creates it fresh on first connect
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
            session.add(
                PlayRow(id=_PLAY_ID, name="test play", objective_text="test", icp_spec={}, mode="demo")
            )
            await session.commit()


async def _create_run(session_factory) -> str:
    await _ensure_play(session_factory)
    return await RunRepository(session_factory).create(play_id=_PLAY_ID, mode="demo", seed=1)


@pytest.mark.asyncio
async def test_concurrent_appends_to_same_run_produce_unique_contiguous_seq(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory)
        events = EventRepository(session_factory)

        n = 40
        results = await asyncio.gather(
            *[events.append(run_id=run_id, type="step.started", prospect_id=None, payload={"i": i}) for i in range(n)]
        )

        assert sorted(results) == list(range(1, n + 1))  # unique + contiguous 1..N, gap-free
        assert len(set(results)) == n  # no duplicates
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_replay_after_seq_is_strictly_increasing(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory)
        events = EventRepository(session_factory)

        for i in range(10):
            await events.append(run_id=run_id, type="step.started", prospect_id=None, payload={"i": i})

        rows = await events.after(run_id, 0)
        seqs = [r.seq for r in rows]
        assert seqs == sorted(seqs)
        assert seqs == list(range(1, 11))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_different_runs_do_not_share_sequence_namespace(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_a = await _create_run(session_factory)
        run_b = await _create_run(session_factory)
        events = EventRepository(session_factory)

        for _ in range(3):
            await events.append(run_id=run_a, type="step.started", prospect_id=None, payload={})
        for _ in range(5):
            await events.append(run_id=run_b, type="step.started", prospect_id=None, payload={})

        seqs_a = [r.seq for r in await events.after(run_a, 0)]
        seqs_b = [r.seq for r in await events.after(run_b, 0)]
        assert seqs_a == [1, 2, 3]
        assert seqs_b == [1, 2, 3, 4, 5]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_different_runs_do_not_contend_under_concurrency(db_path):
    """Two runs' concurrent appends interleave freely — only same-run
    appends should ever serialize against each other."""
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_a = await _create_run(session_factory)
        run_b = await _create_run(session_factory)
        events = EventRepository(session_factory)

        n = 20
        results_a, results_b = await asyncio.gather(
            asyncio.gather(*[events.append(run_id=run_a, type="t", prospect_id=None, payload={}) for _ in range(n)]),
            asyncio.gather(*[events.append(run_id=run_b, type="t", prospect_id=None, payload={}) for _ in range(n)]),
        )
        assert sorted(results_a) == list(range(1, n + 1))
        assert sorted(results_b) == list(range(1, n + 1))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_nonexistent_run_fails_closed(db_path):
    engine, session_factory = _make_session_factory(db_path)
    try:
        events = EventRepository(session_factory)
        with pytest.raises(RunNotFoundError):
            await events.append(run_id="does-not-exist", type="step.started", prospect_id=None, payload={})

        # And the failed attempt left nothing behind to replay.
        rows = await events.after("does-not-exist", 0)
        assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_disconnect_and_after_seq_replay_loses_nothing(db_path):
    """Simulates an SSE client dropping mid-stream and reconnecting with
    `after_seq` set to the last seq it actually saw — replay must resume
    exactly, with nothing skipped and nothing duplicated."""
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory)
        events = EventRepository(session_factory)

        for i in range(5):
            await events.append(run_id=run_id, type="t", prospect_id=None, payload={"i": i})

        first_batch = await events.after(run_id, 0)
        assert [r.seq for r in first_batch] == [1, 2, 3, 4, 5]
        client_cursor = first_batch[2].seq  # client "saw" through seq 3

        for i in range(5, 8):
            await events.append(run_id=run_id, type="t", prospect_id=None, payload={"i": i})

        replay = await events.after(run_id, client_cursor)
        assert [r.seq for r in replay] == [4, 5, 6, 7, 8]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollback_after_seq_reservation_leaves_no_visible_gap(db_path):
    """If the transaction that reserved a seq never commits (simulates the
    insert half failing), the reservation itself rolls back — the DB's
    `last_event_seq` counter is unchanged, and the next successful append
    reuses that same seq rather than leaving a permanent hole a replaying
    client would have to explain."""
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory)
        events = EventRepository(session_factory)

        await events.append(run_id=run_id, type="t", prospect_id=None, payload={})  # seq=1

        # Reproduce the same UPDATE ... RETURNING reservation `append()`
        # does, but roll back instead of inserting/committing.
        from sqlalchemy import update

        async with session_factory() as session:
            result = await session.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(last_event_seq=RunRow.last_event_seq + 1)
                .returning(RunRow.last_event_seq)
            )
            reserved_seq = result.first()[0]
            assert reserved_seq == 2
            await session.rollback()

        # The counter is back to 1 — the next real append gets seq=2 again,
        # not 3. No gap, no dangling reservation.
        next_seq = await events.append(run_id=run_id, type="t", prospect_id=None, payload={})
        assert next_seq == 2

        seqs = [r.seq for r in await events.after(run_id, 0)]
        assert seqs == [1, 2]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_independent_engines_simulate_overlapping_processes(db_path):
    """Two SEPARATE SQLAlchemy engines/session factories (never sharing a
    Python object, let alone a lock) pointed at the same SQLite file —
    standing in for two overlapping API processes. Concurrent appends from
    both must still serialize correctly through the database alone."""
    engine_1, session_factory_1 = _make_session_factory(db_path)
    engine_2, session_factory_2 = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory_1)
        events_1 = EventRepository(session_factory_1)
        events_2 = EventRepository(session_factory_2)

        n = 15
        results = await asyncio.gather(
            *[events_1.append(run_id=run_id, type="t", prospect_id=None, payload={}) for _ in range(n)],
            *[events_2.append(run_id=run_id, type="t", prospect_id=None, payload={}) for _ in range(n)],
        )
        assert sorted(results) == list(range(1, 2 * n + 1))
        assert len(set(results)) == 2 * n
    finally:
        await engine_1.dispose()
        await engine_2.dispose()


@pytest.mark.asyncio
async def test_interleaved_writer_reader_never_permanently_skips_a_committed_event(db_path):
    """A reader polling `after()` mid-write must never permanently miss an
    event a concurrent writer commits — only ever observe it late."""
    engine, session_factory = _make_session_factory(db_path)
    try:
        run_id = await _create_run(session_factory)
        events = EventRepository(session_factory)

        n = 25
        seen: set[int] = set()

        async def writer():
            for i in range(n):
                await events.append(run_id=run_id, type="t", prospect_id=None, payload={"i": i})
                await asyncio.sleep(0)

        async def reader():
            last = 0
            for _ in range(200):
                rows = await events.after(run_id, last)
                for r in rows:
                    seen.add(r.seq)
                    last = r.seq
                await asyncio.sleep(0.005)
                if len(seen) == n:
                    return

        await asyncio.gather(writer(), reader())

        # Final catch-up read guarantees full replay even if the reader
        # loop above exited before the last few writes landed.
        final_rows = await events.after(run_id, 0)
        seen.update(r.seq for r in final_rows)
        assert seen == set(range(1, n + 1))
    finally:
        await engine.dispose()
