"""`repositories/live_send_allowance.py` (V2-I-b, Phase 5) — the rolling 24h
Live send allowance: singleton seeding, the correctness transaction, missing/
invalid guard fail-closed, SQLite lock retry/exhaustion, rolling-window
boundaries, UTC-midnight non-reset, no release for any outcome, and the
dual-session final-slot race.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

from groundwork.models.tables import LiveSendAllowanceLockRow
from groundwork.repositories.live_send_allowance import (
    ALLOWANCE_LOCK_ID,
    AllowanceReservationOutcome,
    LiveSendAllowanceRepository,
    ensure_singleton_seeded,
)


async def test_singleton_seeded_exactly_once(session_factory):
    # conftest's session_factory fixture already seeds it once.
    async with session_factory() as session:
        row = await session.get(LiveSendAllowanceLockRow, ALLOWANCE_LOCK_ID)
    assert row is not None
    assert row.version == 0

    # Idempotent: calling again must not create a second row / reset version.
    async with session_factory() as session:
        row = await session.get(LiveSendAllowanceLockRow, ALLOWANCE_LOCK_ID)
        row.version = 5
        await session.commit()
    await ensure_singleton_seeded(session_factory)
    async with session_factory() as session:
        row = await session.get(LiveSendAllowanceLockRow, ALLOWANCE_LOCK_ID)
    assert row.version == 5


async def test_reservation_granted_under_limit(session_factory, _seed_execution_rows):
    repo = LiveSendAllowanceRepository(session_factory)
    now = datetime.now(timezone.utc)
    (exec_id,) = await _seed_execution_rows(1)
    outcome = await repo.try_reserve(exec_id, now=now, window_s=86400.0, limit=5)
    assert outcome is AllowanceReservationOutcome.GRANTED
    assert await repo.count_reservations_since(now=now, window_s=86400.0) == 1


async def test_reservation_denied_when_limit_reached(session_factory, _seed_execution_rows):
    repo = LiveSendAllowanceRepository(session_factory)
    now = datetime.now(timezone.utc)
    exec_ids = await _seed_execution_rows(3)
    for i in range(2):
        outcome = await repo.try_reserve(exec_ids[i], now=now, window_s=86400.0, limit=2)
        assert outcome is AllowanceReservationOutcome.GRANTED
    outcome = await repo.try_reserve(exec_ids[2], now=now, window_s=86400.0, limit=2)
    assert outcome is AllowanceReservationOutcome.DENIED_LIMIT_REACHED
    assert await repo.count_reservations_since(now=now, window_s=86400.0) == 2


async def test_missing_guard_row_fails_closed(session_factory):
    async with session_factory() as session:
        await session.execute(delete(LiveSendAllowanceLockRow).where(LiveSendAllowanceLockRow.id == ALLOWANCE_LOCK_ID))
        await session.commit()

    repo = LiveSendAllowanceRepository(session_factory)
    outcome = await repo.try_reserve("exec-x", now=datetime.now(timezone.utc), window_s=86400.0, limit=5)
    assert outcome is AllowanceReservationOutcome.DENIED_GUARD_MISSING


async def test_rolling_window_boundary_excludes_old_reservations(session_factory, _seed_execution_rows):
    from groundwork.models.tables import LiveSendReservationRow

    exec_ids = await _seed_execution_rows(2)
    now = datetime.now(timezone.utc)
    old_reservation_time = now - timedelta(hours=25)  # outside a 24h window
    async with session_factory() as session:
        session.add(LiveSendReservationRow(execution_id=exec_ids[0], reserved_at=old_reservation_time))
        await session.commit()

    repo = LiveSendAllowanceRepository(session_factory)
    # The old reservation must NOT count against the rolling window.
    assert await repo.count_reservations_since(now=now, window_s=86400.0) == 0
    outcome = await repo.try_reserve(exec_ids[1], now=now, window_s=86400.0, limit=1)
    assert outcome is AllowanceReservationOutcome.GRANTED


async def test_utc_midnight_does_not_reset_the_rolling_window(session_factory, _seed_execution_rows):
    """A reservation made at 23:50 UTC still counts against the limit at
    00:10 UTC the next day — the window is rolling (now - 24h), never
    calendar-aligned."""
    exec_ids = await _seed_execution_rows(2)
    just_before_midnight = datetime(2026, 3, 1, 23, 50, 0, tzinfo=timezone.utc)
    repo = LiveSendAllowanceRepository(session_factory)
    outcome = await repo.try_reserve(exec_ids[0], now=just_before_midnight, window_s=86400.0, limit=1)
    assert outcome is AllowanceReservationOutcome.GRANTED

    just_after_midnight = datetime(2026, 3, 2, 0, 10, 0, tzinfo=timezone.utc)
    outcome = await repo.try_reserve(exec_ids[1], now=just_after_midnight, window_s=86400.0, limit=1)
    assert outcome is AllowanceReservationOutcome.DENIED_LIMIT_REACHED


async def test_reservation_never_released_regardless_of_outcome(session_factory, _seed_execution_rows):
    """Reservations have no release method at all — proven structurally."""
    repo = LiveSendAllowanceRepository(session_factory)
    assert not hasattr(repo, "release")
    assert not hasattr(repo, "release_reservation")
    exec_ids = await _seed_execution_rows(1)
    now = datetime.now(timezone.utc)
    await repo.try_reserve(exec_ids[0], now=now, window_s=86400.0, limit=5)
    assert await repo.count_reservations_since(now=now, window_s=86400.0) == 1


async def test_lock_retry_exhaustion_denies_without_raising(session_factory, monkeypatch):
    repo = LiveSendAllowanceRepository(session_factory, max_attempts=3, retry_base_delay_s=0.001)
    call_count = 0
    original_attempt = repo._attempt_once

    async def _always_locked(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise OperationalError("UPDATE ...", {}, Exception("database is locked"))

    monkeypatch.setattr(repo, "_attempt_once", _always_locked)
    outcome = await repo.try_reserve("exec-lock", now=datetime.now(timezone.utc), window_s=86400.0, limit=5)
    assert outcome is AllowanceReservationOutcome.DENIED_LOCK_CONTENTION
    assert call_count == 3  # exactly max_attempts COMPLETE attempts, each restarting from the guard UPDATE


async def test_lock_retry_succeeds_after_transient_contention(session_factory, monkeypatch, _seed_execution_rows):
    repo = LiveSendAllowanceRepository(session_factory, max_attempts=3, retry_base_delay_s=0.001)
    call_count = 0
    real_attempt = LiveSendAllowanceRepository._attempt_once

    async def _locked_once_then_real(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise OperationalError("UPDATE ...", {}, Exception("database is locked"))
        return await real_attempt(self, *args, **kwargs)

    monkeypatch.setattr(LiveSendAllowanceRepository, "_attempt_once", _locked_once_then_real)
    (exec_id,) = await _seed_execution_rows(1)
    outcome = await repo.try_reserve(exec_id, now=datetime.now(timezone.utc), window_s=86400.0, limit=5)
    assert outcome is AllowanceReservationOutcome.GRANTED
    assert call_count == 2


async def test_non_lock_operational_error_is_reraised(session_factory, monkeypatch):
    repo = LiveSendAllowanceRepository(session_factory, max_attempts=3, retry_base_delay_s=0.001)

    async def _other_error(*args, **kwargs):
        raise OperationalError("UPDATE ...", {}, Exception("disk I/O error"))

    monkeypatch.setattr(repo, "_attempt_once", _other_error)
    with pytest.raises(OperationalError):
        await repo.try_reserve("exec-other", now=datetime.now(timezone.utc), window_s=86400.0, limit=5)


async def test_dual_session_final_slot_race_only_one_wins(session_factory, _seed_execution_rows):
    """Two concurrent reservation attempts for the LAST available slot —
    the guarded UPDATE serializes them; exactly one is GRANTED."""
    exec_ids = await _seed_execution_rows(2)
    now = datetime.now(timezone.utc)
    repo_a = LiveSendAllowanceRepository(session_factory)
    repo_b = LiveSendAllowanceRepository(session_factory)

    results = await asyncio.gather(
        repo_a.try_reserve(exec_ids[0], now=now, window_s=86400.0, limit=1),
        repo_b.try_reserve(exec_ids[1], now=now, window_s=86400.0, limit=1),
    )
    granted = [r for r in results if r is AllowanceReservationOutcome.GRANTED]
    denied = [r for r in results if r is AllowanceReservationOutcome.DENIED_LIMIT_REACHED]
    assert len(granted) == 1
    assert len(denied) == 1


@pytest.fixture
async def _seed_execution_rows(session_factory):
    """`live_send_reservations.execution_id` is an FK to `action_executions.id`
    — seed minimal, valid parent rows so the reservation insert doesn't
    violate the FK under `PRAGMA foreign_keys=ON`."""
    import uuid

    from groundwork.models.tables import (
        ActionExecutionRow,
        ActionProposalRow,
        CompanyRow,
        OutreachDraftRow,
        PlayRow,
        ProspectRow,
        RunRow,
    )

    async def _make(n: int) -> list[str]:
        ids = []
        async with session_factory() as session:
            play = PlayRow(id=str(uuid.uuid4()), name="p", objective_text="o", icp_spec={}, mode="live")
            session.add(play)
            await session.flush()
            run = RunRow(id=str(uuid.uuid4()), play_id=play.id, status="COMPLETED", mode="live", seed=1)
            session.add(run)
            company = CompanyRow(id=str(uuid.uuid4()), canonical_domain=f"{uuid.uuid4()}.com", normalized_name="x", display_name="X")
            session.add(company)
            await session.flush()
            prospect = ProspectRow(id=str(uuid.uuid4()), run_id=run.id, company_id=company.id, dedupe_key="x")
            session.add(prospect)
            await session.flush()
            draft = OutreachDraftRow(
                id=str(uuid.uuid4()), prospect_id=prospect.id, channel="email", subject="Hi", body="Hello"
            )
            session.add(draft)
            await session.flush()
            proposal = ActionProposalRow(
                id=str(uuid.uuid4()),
                prospect_id=prospect.id,
                run_id=run.id,
                draft_id=draft.id,
                action_type="EMAIL_SEND",
                channel="email",
                sender_identifier="op@example.com",
                recipient_identifier="rcpt@example.com",
                recipient_identity_key="rcpt@example.com",
                content_hash="h",
                hash_version="v1",
                policy_version="v1",
                policy_verdict="ELIGIBLE",
                origin="LIVE_EXTERNAL",
            )
            session.add(proposal)
            await session.flush()
            for i in range(n):
                execution_id = str(uuid.uuid4())
                session.add(
                    ActionExecutionRow(
                        id=execution_id,
                        action_proposal_id=proposal.id,
                        prospect_id=prospect.id,
                        run_id=run.id,
                        action_type="EMAIL_SEND",
                        status="CLAIMED",
                        idempotency_key=str(uuid.uuid4()),
                        origin="LIVE_EXTERNAL",
                        # Each row needs a DISTINCT recipient identity — the
                        # §3.5B partial unique index blocks a second CLAIMED
                        # row for the same (action_type, recipient) pair.
                        # This fixture is about the ALLOWANCE mechanism, not
                        # §3.5B, so distinct recipients sidestep it cleanly.
                        recipient_identity_key=f"rcpt{i}-{execution_id}@example.com",
                        sender_identifier="op@example.com",
                    )
                )
                ids.append(execution_id)
            await session.commit()
        return ids

    return _make
