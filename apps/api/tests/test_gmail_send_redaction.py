"""V2-I-b — redaction: a send/reconcile attempt's `error_message` must be
redacted before persistence, and no OAuth token/secret ever appears in a
persisted `action_send_calls`/`action_events` row."""

from __future__ import annotations

import uuid

from groundwork.config import settings
from groundwork.repositories.actions import ActionRepository


async def test_insert_send_call_redacts_error_message_containing_configured_secret(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-super-secret-value-123456")
    from datetime import datetime, timezone

    from groundwork.models.tables import (
        ActionExecutionRow,
        ActionProposalRow,
        CompanyRow,
        OutreachDraftRow,
        PlayRow,
        ProspectRow,
        RunRow,
    )

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
        draft = OutreachDraftRow(id=str(uuid.uuid4()), prospect_id=prospect.id, channel="email", subject="Hi", body="Hello")
        session.add(draft)
        await session.flush()
        proposal = ActionProposalRow(
            id=str(uuid.uuid4()), prospect_id=prospect.id, run_id=run.id, draft_id=draft.id,
            action_type="EMAIL_SEND", channel="email", sender_identifier="op@example.com",
            recipient_identifier="rcpt@example.com", recipient_identity_key="rcpt@example.com",
            content_hash="h", hash_version="v1", policy_version="v1", policy_verdict="ELIGIBLE", origin="LIVE_EXTERNAL",
        )
        session.add(proposal)
        await session.flush()
        execution = ActionExecutionRow(
            id=str(uuid.uuid4()), action_proposal_id=proposal.id, prospect_id=prospect.id, run_id=run.id,
            action_type="EMAIL_SEND", status="CLAIMED", idempotency_key=str(uuid.uuid4()), origin="LIVE_EXTERNAL",
        )
        session.add(execution)
        await session.commit()
        execution_id = execution.id

    actions = ActionRepository(session_factory)
    now = datetime.now(timezone.utc)
    await actions.insert_send_call(
        action_execution_id=execution_id,
        call_group_id=str(uuid.uuid4()),
        provider="gmail",
        status="PROVIDER_ERROR",
        started_at=now,
        finished_at=now,
        error_type="GoogleOAuthError",
        error_message="refresh failed with key sk-super-secret-value-123456 attached",
    )
    calls = await actions.list_send_calls_for_execution(execution_id)
    assert len(calls) == 1
    # The redact() choke point is applied — confirmed indirectly by checking
    # the raw persisted row via a fresh session (bypassing any Python-side
    # attribute caching).
    from sqlalchemy import select

    from groundwork.models.tables import ActionSendCallRow

    async with session_factory() as session:
        row = (
            await session.execute(select(ActionSendCallRow).where(ActionSendCallRow.action_execution_id == execution_id))
        ).scalar_one()
        assert "sk-super-secret-value-123456" not in (row.error_message or "")
        assert "[REDACTED]" in (row.error_message or "")


async def test_action_event_payload_never_contains_full_email_address(session_factory):
    actions = ActionRepository(session_factory)
    from groundwork.models.tables import CompanyRow, PlayRow, ProspectRow, RunRow

    async with session_factory() as session:
        play = PlayRow(id=str(uuid.uuid4()), name="p", objective_text="o", icp_spec={}, mode="demo")
        session.add(play)
        await session.flush()
        run = RunRow(id=str(uuid.uuid4()), play_id=play.id, status="COMPLETED", mode="demo", seed=1)
        session.add(run)
        company = CompanyRow(id=str(uuid.uuid4()), canonical_domain=f"{uuid.uuid4()}.com", normalized_name="x", display_name="X")
        session.add(company)
        await session.flush()
        prospect = ProspectRow(id=str(uuid.uuid4()), run_id=run.id, company_id=company.id, dedupe_key="x")
        session.add(prospect)
        await session.commit()
        prospect_id = prospect.id

    await actions.record_event(
        prospect_id=prospect_id,
        type="test_event",
        actor="system",
        payload={"note": "sent to priya.natarajan@northwindlabs.com about the offer"},
    )
    from sqlalchemy import select

    from groundwork.models.tables import ActionEventRow

    async with session_factory() as session:
        row = (
            await session.execute(select(ActionEventRow).where(ActionEventRow.prospect_id == prospect_id))
        ).scalars().first()
        assert "priya.natarajan" not in row.payload["note"]
        assert "***@northwindlabs.com" in row.payload["note"]

    events = await actions.list_events_for_proposal("nonexistent")  # sanity: no crash on empty
    assert events == []
    events2 = await actions.list_events_for_execution("nonexistent")
    assert events2 == []
