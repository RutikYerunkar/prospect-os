"""V2-I-a — execute-time enforcement of clause 15 (`recipient_suppressed`).

Proves the load-bearing execute-time ordering the task brief requires:
`approve -> suppress -> execute` fails BEFORE any execution/send can become
actionable. Suppression is applied directly via the database (mirroring
`test_content_hash_invalidation.py`'s `TestMismatchLinkedInBlockedNoOverride`
technique) to simulate a legal/privacy restriction discovered strictly
between approval and execution — the realistic ordering a real Hunter 451
could produce. Also proves suppression is NOT part of the content hash, and
that it cannot be bypassed via reproposal, a fresh Live authorization setup
("Gmail reconnect"), or a repeated/idempotent execute call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from groundwork.models.tables import ActionExecutionRow, ContactChannelRow, RunRow
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.timeutil import utcnow
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion
from tests.api_helpers import login_as_operator

OPERATOR_EMAIL = "operator@example.com"


async def _suppress_email_channel(session_factory, prospect_id: str) -> None:
    async with session_factory() as session:
        await session.execute(
            update(ContactChannelRow)
            .where(ContactChannelRow.prospect_id == prospect_id, ContactChannelRow.channel == "email")
            .values(
                send_suppressed_at=datetime.now(timezone.utc),
                send_suppression_reason="LEGAL_OR_PRIVACY_RESTRICTION",
                send_suppression_source="hunter",
                send_suppression_provider_code="claimed_email",
            )
        )
        await session.commit()


async def _execution_row_for(session_factory, proposal_id: str) -> ActionExecutionRow | None:
    async with session_factory() as session:
        result = await session.execute(
            select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal_id)
        )
        return result.scalar_one_or_none()


async def test_approve_suppress_execute_blocks_with_recipient_suppressed(client, session_factory):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text

    # Suppress AFTER approval — a legal/privacy restriction discovered
    # between approve and execute.
    await _suppress_email_channel(session_factory, northwind["id"])

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "RECIPIENT_SUPPRESSED"


async def test_no_execution_row_is_ever_created(client, session_factory):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    await _suppress_email_channel(session_factory, northwind["id"])

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text

    # The block happens at fresh policy evaluation (§3.9 step 7) — strictly
    # BEFORE the write-ahead `insert_claimed_execution` call (step 8), so no
    # execution row of any status (not even CLAIMED) is ever written.
    assert await _execution_row_for(session_factory, proposal["id"]) is None

    r2 = await client.get(f"/api/actions/proposals/{proposal['id']}")
    assert r2.status_code == 200
    assert r2.json()["execution"] is None


async def test_content_hash_is_unchanged_by_suppression(client, session_factory):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    original_hash = proposal["content_hash"]
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text

    await _suppress_email_channel(session_factory, northwind["id"])

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409

    # Suppression is deliberately NOT part of the content hash (task's
    # explicit invariant) — the proposal's own hash is untouched, and the
    # existing approval remains historically valid even though execution is
    # now blocked.
    r2 = await client.get(f"/api/actions/proposals/{proposal['id']}")
    assert r2.json()["content_hash"] == original_hash
    assert r2.json()["approval"]["state"] == "APPROVED"


async def test_repeated_execute_never_bypasses_via_idempotency_replay(client, session_factory):
    """No idempotency-key row is ever created while suppressed (it is only
    minted after the fresh policy check passes) — so there is nothing for a
    replayed execute call to match against. Every repeated attempt fails
    the SAME way, and none of them ever create an execution row."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    await _suppress_email_channel(session_factory, northwind["id"])

    for _ in range(3):
        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "RECIPIENT_SUPPRESSED"

    assert await _execution_row_for(session_factory, proposal["id"]) is None


async def test_reproposing_the_unchanged_draft_does_not_bypass_suppression(client, session_factory):
    """Re-proposing an UNCHANGED draft is idempotent (§Part 5) — it returns
    the SAME proposal row, never a fresh re-evaluation. Even so, execution
    is still blocked, because `execute_action` always re-evaluates policy
    fresh at execute time regardless of what verdict is cached on the
    proposal row itself."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    first = await propose(client, draft["id"])
    r = await approve(client, first["id"])
    assert r.status_code == 200, r.text
    await _suppress_email_channel(session_factory, northwind["id"])

    second = await propose(client, draft["id"])
    assert second["id"] == first["id"]
    assert second["created"] is False
    # The proposal's stored verdict was computed BEFORE suppression and is
    # not retroactively recomputed by a no-op repropose — this is exactly
    # why execute-time re-evaluation (not the stored proposal verdict) is
    # the actual enforcement point.
    assert second["policy_verdict"] == "ELIGIBLE"

    resp = await execute(client, second["id"])
    assert resp.status_code == 409
    assert resp.json()["code"] == "RECIPIENT_SUPPRESSED"
    assert await _execution_row_for(session_factory, first["id"]) is None


async def test_live_execute_blocked_by_suppression_before_reaching_structural_send_refusal(
    client, session_factory, monkeypatch
):
    """A fresh Live authorization setup ("Gmail reconnect") does not bypass
    suppression either: clause 15 is evaluated at step 7 of `execute_action`
    — strictly BEFORE `resolve_send_provider(Mode.LIVE)` is ever reached at
    step 9 — so a suppressed LIVE_EXTERNAL proposal never even reaches the
    `LiveExternalEmailSendDisabled` structural refusal; it is blocked by
    policy first, with the SAME `RECIPIENT_SUPPRESSED` code Demo gets."""
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)

    async with session_factory() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(mode="live"))
        await session.commit()

    now = utcnow()
    await GmailConnectionRepository(session_factory).upsert_connection(
        google_account_email=OPERATOR_EMAIL,
        encrypted_refresh_token="test-only-not-a-real-ciphertext",
        key_version=1,
        scopes=["gmail.send", "gmail.metadata"],
        connected_at=now,
        connected_by_actor="test-operator",
        last_refreshed_at=now,
    )

    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    assert proposal["origin"] == "LIVE_EXTERNAL", proposal
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text

    await _suppress_email_channel(session_factory, northwind["id"])

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "RECIPIENT_SUPPRESSED"
    assert await _execution_row_for(session_factory, proposal["id"]) is None
