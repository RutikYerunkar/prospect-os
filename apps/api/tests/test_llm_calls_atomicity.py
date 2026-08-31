"""Regression test for the real FK-ordering bug the second real OpenAI smoke
test exposed: `LLMCallRepository.create_play_with_attempts()` must insert
the `Play` and its `objective_parse` `llm_calls` rows as ONE atomic
transaction, under real foreign-key enforcement.

Root cause (see `repositories/llm_calls.py`'s docstring and
`docs/PROGRESS.md`'s Checkpoint G section for the full writeup): no ORM
`relationship()` exists between `PlayRow`/`LLMCallRow` anywhere in
`models/tables.py` — this codebase uses raw FK columns + manual joins
throughout — so SQLAlchemy's unit-of-work has no dependency processor to
guarantee the `plays` INSERT happens before the `llm_calls` INSERT. Under
`PRAGMA foreign_keys=ON` (real production behavior — see `db.py`), that
surfaced as a genuine `sqlite3.IntegrityError: FOREIGN KEY constraint
failed`. The fix is an explicit `session.flush()` after adding the `Play`,
before adding any `LLMCallRow`.

Why the original 129-test suite didn't catch this: `tests/conftest.py`'s
`_enable_wal` (the fixture every test's `session_factory` uses) had drifted
from `db.py`'s real one — it never set `PRAGMA foreign_keys=ON`. SQLite does
not enforce foreign keys per connection unless that pragma is set
explicitly; every test ran against a DB that silently accepted
FK-violating insert order. `conftest.py` now mirrors `db.py` exactly, and
this file additionally locks down the specific atomicity contract with the
real repository, not a mock.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from groundwork.providers.base import LLMAttemptKind, LLMAttemptStatus, LLMAttemptTelemetry
from groundwork.repositories.llm_calls import LLMCallRepository


def _attempt(**overrides) -> LLMAttemptTelemetry:
    now = datetime.now(timezone.utc)
    defaults = dict(
        attempt=1, attempt_kind=LLMAttemptKind.INITIAL, schema_round=0, transport_retry_index=0,
        status=LLMAttemptStatus.OK, started_at=now, finished_at=now, latency_ms=1.0,
        model="gpt-5.6-terra", input_digest="deadbeef",
    )
    defaults.update(overrides)
    return LLMAttemptTelemetry(**defaults)


async def _counts(session_factory) -> tuple[int, int]:
    async with session_factory() as session:
        plays = (await session.execute(text("SELECT COUNT(*) FROM plays"))).scalar_one()
        calls = (await session.execute(text("SELECT COUNT(*) FROM llm_calls"))).scalar_one()
    return plays, calls


async def test_success_play_and_llm_call_committed_atomically_with_correct_fks(session_factory):
    repo = LLMCallRepository(session_factory)
    play_id = await repo.create_play_with_attempts(
        play_kwargs=dict(name="t", objective_text="t", icp_spec={}, mode="live"),
        call_group_id="grp-success", operation="objective_parse", provider="openai",
        prompt_version="objective_parse-v1", attempts=[_attempt()],
    )

    plays_count, calls_count = await _counts(session_factory)
    assert plays_count == 1
    assert calls_count == 1

    rows = await repo.for_play(play_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.play_id == play_id  # references the ACTUAL play, not a stale/foreign id
    assert row.run_id is None
    assert row.prospect_id is None
    assert row.operation == "objective_parse"
    assert row.status == "OK"


async def test_rollback_no_orphan_play_or_llm_call_when_insert_fails_after_play_flush(session_factory):
    """Two attempts sharing the same (call_group_id, attempt) violate the
    `UNIQUE(call_group_id, attempt)` constraint on the SECOND llm_calls
    insert — which happens in the same flush/commit as the FIRST llm_calls
    insert, both occurring AFTER the Play was already flushed (a separate,
    earlier `flush()` call inside `create_play_with_attempts`). This proves
    the whole transaction — including the already-flushed-but-uncommitted
    Play — rolls back together on a later failure, not just that the first
    insert succeeded."""
    repo = LLMCallRepository(session_factory)
    duplicate_attempts = [_attempt(attempt=1), _attempt(attempt=1)]  # same attempt number -> UNIQUE violation

    with pytest.raises(Exception):  # sqlalchemy.exc.IntegrityError
        await repo.create_play_with_attempts(
            play_kwargs=dict(name="t", objective_text="t", icp_spec={}, mode="live"),
            call_group_id="grp-rollback", operation="objective_parse", provider="openai",
            prompt_version="objective_parse-v1", attempts=duplicate_attempts,
        )

    plays_count, calls_count = await _counts(session_factory)
    assert plays_count == 0, "the Play must not survive a failed transaction, even though it was flushed first"
    assert calls_count == 0, "no orphan llm_calls row may remain either"


async def test_multiple_successful_calls_do_not_interfere(session_factory):
    """Two separate successful `create_play_with_attempts()` calls each get
    their own Play and their own correctly-linked llm_calls row — the fix
    doesn't accidentally serialize/corrupt state across calls."""
    repo = LLMCallRepository(session_factory)
    play_id_1 = await repo.create_play_with_attempts(
        play_kwargs=dict(name="one", objective_text="one", icp_spec={}, mode="live"),
        call_group_id="grp-a", operation="objective_parse", provider="openai",
        prompt_version="objective_parse-v1", attempts=[_attempt()],
    )
    play_id_2 = await repo.create_play_with_attempts(
        play_kwargs=dict(name="two", objective_text="two", icp_spec={}, mode="live"),
        call_group_id="grp-b", operation="objective_parse", provider="openai",
        prompt_version="objective_parse-v1", attempts=[_attempt()],
    )
    assert play_id_1 != play_id_2

    plays_count, calls_count = await _counts(session_factory)
    assert plays_count == 2
    assert calls_count == 2

    rows_1 = await repo.for_play(play_id_1)
    rows_2 = await repo.for_play(play_id_2)
    assert len(rows_1) == 1 and rows_1[0].play_id == play_id_1
    assert len(rows_2) == 1 and rows_2[0].play_id == play_id_2
