"""`api/live_send_orchestration.py::dispatch_live_email_send` (V2-I-b, Phase
7) — the full dispatch ordering, request idempotency, allowance
denial-blocks-dispatch, and outcome-class -> execution-status wiring.
Exercised directly (never through the HTTP router, which still reaches the
unconditional `LiveExternalEmailSendDisabled` refusal at this point in the
checkpoint) — this is exactly the "build and independently test the
dispatch path before the refusal-removal gate" discipline the module's own
docstring describes.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from groundwork.api.live_send_orchestration import dispatch_live_email_send
from groundwork.models.tables import (
    ActionExecutionRow,
    ActionProposalRow,
    ApprovalRow,
    CompanyRow,
    OutreachDraftRow,
    PlayRow,
    ProspectRow,
    ReviewResultRow,
    RunRow,
)
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.live_send_allowance import LiveSendAllowanceRepository
from tests.live_gmail_send_helpers import make_provider


async def _seed_proposal(session_factory, *, recipient_suffix: str = "") -> tuple[dict, str]:
    """Returns `(proposal_dict, approval_id)` for a fresh, ELIGIBLE-shaped
    LIVE_EXTERNAL EMAIL_SEND proposal + APPROVED approval, written directly
    (mirrors `tests/test_live_send_allowance.py`'s fixture)."""
    async with session_factory() as session:
        play = PlayRow(id=str(uuid.uuid4()), name="p", objective_text="o", icp_spec={}, mode="live")
        session.add(play)
        await session.flush()
        run = RunRow(id=str(uuid.uuid4()), play_id=play.id, status="COMPLETED", mode="live", seed=1)
        session.add(run)
        company = CompanyRow(
            id=str(uuid.uuid4()), canonical_domain=f"{uuid.uuid4()}.com", normalized_name="x", display_name="X"
        )
        session.add(company)
        await session.flush()
        prospect = ProspectRow(id=str(uuid.uuid4()), run_id=run.id, company_id=company.id, dedupe_key="x", status="PASS")
        session.add(prospect)
        await session.flush()
        session.add(
            ReviewResultRow(id=str(uuid.uuid4()), prospect_id=prospect.id, verdict="PASS", checks=[], reasons=[])
        )
        draft = OutreachDraftRow(
            id=str(uuid.uuid4()), prospect_id=prospect.id, channel="email", subject="Hi", body="Hello there"
        )
        session.add(draft)
        await session.flush()
        recipient = f"rcpt{recipient_suffix}-{uuid.uuid4()}@example.com"
        proposal = ActionProposalRow(
            id=str(uuid.uuid4()),
            prospect_id=prospect.id,
            run_id=run.id,
            draft_id=draft.id,
            action_type="EMAIL_SEND",
            channel="email",
            sender_identifier="operator@example.com",
            recipient_identifier=recipient,
            recipient_identity_key=recipient,
            content_hash="hash-abc",
            hash_version="v1",
            policy_version="v1",
            policy_verdict="ELIGIBLE",
            origin="LIVE_EXTERNAL",
        )
        session.add(proposal)
        await session.flush()
        approval = ApprovalRow(
            id=str(uuid.uuid4()),
            prospect_id=prospect.id,
            decision="APPROVED",
            scope="ACTION",
            action_proposal_id=proposal.id,
            content_hash=proposal.content_hash,
            hash_version=proposal.hash_version,
        )
        session.add(approval)
        await session.commit()
        proposal_dict = {
            "id": proposal.id,
            "prospect_id": prospect.id,
            "run_id": run.id,
            "draft_id": draft.id,
            "recipient_identifier": recipient,
            "recipient_identity_key": recipient,
            "sender_identifier": "operator@example.com",
        }
        return proposal_dict, approval.id, draft


async def test_dispatch_ordering_claimed_before_allowance_before_in_flight_before_http(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        send_steps=[(200, {"id": "gmail-msg-1"})],
    )

    async with session_factory() as session:
        from sqlalchemy import select

        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions,
        allowance=allowance,
        provider=provider,
        proposal=proposal_row,
        approval=approval_row,
        draft=draft,
        idempotency_key=f"idem-{uuid.uuid4()}",
        live_max_sends_per_day=50,
    )

    assert settled.status == "SUCCEEDED"
    assert settled.claimed_at is not None
    assert settled.dispatched_at is not None
    assert settled.settled_at is not None
    assert settled.claimed_at <= settled.dispatched_at <= settled.settled_at
    assert settled.provider_message_id == "gmail-msg-1"
    # Exactly one HTTP send request — no blind retry, no double-dispatch.
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 1
    # Allowance was reserved.
    assert await allowance.count_reservations_since(now=datetime.now(timezone.utc), window_s=86400.0) == 1


async def test_request_idempotency_duplicate_call_never_dispatches_twice(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        send_steps=[(200, {"id": "gmail-msg-2"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    idem_key = f"idem-{uuid.uuid4()}"
    first = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=idem_key, live_max_sends_per_day=50,
    )
    second = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=idem_key, live_max_sends_per_day=50,
    )
    assert first.id == second.id
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 1  # never a second dispatch for the same idempotency key
    assert await allowance.count_reservations_since(now=datetime.now(timezone.utc), window_s=86400.0) == 1


async def test_allowance_denied_blocks_dispatch_entirely(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(send_steps=[(200, {"id": "should-not-be-used"})])

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=0,  # limit already exhausted
    )
    assert settled.status == "FAILED"
    assert settled.outcome_class == "PROVEN_NOT_DISPATCHED"
    assert settled.dispatched is False
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 0  # no HTTP call was ever made


async def test_ambiguous_outcome_settles_uncertain_never_auto_resent(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        send_steps=[(503, {"error": "backend error"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "UNCERTAIN"
    assert settled.outcome_class == "ACCEPTANCE_UNKNOWN"
    # The allowance reservation is NEVER released for an UNCERTAIN outcome.
    assert await allowance.count_reservations_since(now=datetime.now(timezone.utc), window_s=86400.0) == 1
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 1  # exactly one attempt — no automatic resend


async def test_definitive_rejection_settles_failed_and_frees_recipient(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, _ = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        send_steps=[(400, {"error": "malformed"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "FAILED"
    assert settled.outcome_class == "DEFINITIVE_REJECTION"
    # FAILED is the only status that frees the recipient identity (§3.5B) —
    # confirm no lingering CLAIMED/IN_FLIGHT row blocks a retry proposal.
    conflict = await actions.recipient_conflict(proposal_dict["recipient_identity_key"])
    assert conflict.value == "NONE"


# --- V2-I-b correction (post-smoke): pre-dispatch history checkpoint -------


async def test_checkpoint_captured_and_persisted_before_send(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        profile_steps=[(200, {"historyId": "999888777"})],
        send_steps=[(200, {"id": "gmail-msg-checkpoint"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "SUCCEEDED"
    assert settled.pre_dispatch_history_id == "999888777"
    # The checkpoint (profile GET) happened before the one send call.
    profile_calls = [r for r in transport.requests if str(r.url).endswith("/profile")]
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(profile_calls) == 1
    assert len(send_calls) == 1
    assert transport.requests.index(profile_calls[0]) < transport.requests.index(send_calls[0])


async def test_checkpoint_acquisition_failure_makes_zero_send_calls(session_factory):
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        profile_steps=[(500, {"error": "server error"})],
        send_steps=[(200, {"id": "should-not-be-used"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "FAILED"
    assert settled.outcome_class == "PROVEN_NOT_DISPATCHED"
    assert settled.last_error_type == "HISTORY_CHECKPOINT_UNAVAILABLE"
    assert settled.pre_dispatch_history_id is None
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 0  # zero messages.send calls
    # The allowance was never touched either — a checkpoint failure must
    # never burn a scarce daily send slot.
    assert await allowance.count_reservations_since(now=datetime.now(timezone.utc), window_s=86400.0) == 0


async def test_checkpoint_persistence_failure_makes_zero_send_calls(session_factory, monkeypatch):
    """Simulates the extremely-unlikely persistence race (row no longer
    CLAIMED by the time the checkpoint write runs) by monkeypatching
    `record_pre_dispatch_history_checkpoint` to return `None`, exactly as
    it would on a guarded-update miss."""
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        profile_steps=[(200, {"historyId": "1"})],
        send_steps=[(200, {"id": "should-not-be-used"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    async def _fake_record(*args, **kwargs):
        return None

    monkeypatch.setattr(actions, "record_pre_dispatch_history_checkpoint", _fake_record)

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "CLAIMED"  # never advanced — persistence never confirmed
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 0
    assert await allowance.count_reservations_since(now=datetime.now(timezone.utc), window_s=86400.0) == 0


async def test_successful_send_path_remains_exactly_one_send_call(session_factory):
    """Restates the full-path guarantee explicitly in checkpoint terms:
    two provider calls total (checkpoint + send), never more."""
    proposal_dict, approval_id, draft = await _seed_proposal(session_factory)
    actions = ActionRepository(session_factory)
    allowance = LiveSendAllowanceRepository(session_factory)
    provider, transport = make_provider(
        token_steps=[(200, {"access_token": "tok"}), (200, {"access_token": "tok"})],
        profile_steps=[(200, {"historyId": "42"})],
        send_steps=[(200, {"id": "gmail-msg-single"})],
    )

    from sqlalchemy import select

    async with session_factory() as session:
        proposal_row = (
            await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_dict["id"]))
        ).scalar_one()
        approval_row = (await session.execute(select(ApprovalRow).where(ApprovalRow.id == approval_id))).scalar_one()

    settled = await dispatch_live_email_send(
        actions=actions, allowance=allowance, provider=provider, proposal=proposal_row, approval=approval_row,
        draft=draft, idempotency_key=f"idem-{uuid.uuid4()}", live_max_sends_per_day=50,
    )
    assert settled.status == "SUCCEEDED"
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    profile_calls = [r for r in transport.requests if str(r.url).endswith("/profile")]
    assert len(send_calls) == 1
    assert len(profile_calls) == 1
