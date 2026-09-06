"""V2-H — the five server-side Live authorization gates (Part 9), each
bypassed/violated independently, plus the frozen acceptance criterion 4:
Demo succeeds with no operator session while Live is refused, proven as two
independent cases (4A/4B).

Setup technique: a real Demo run is executed to completion (so review,
`contact_channels`, and drafts are all genuine, engine-produced state), then
the run's `mode` column is flipped to `"live"` directly via the DB — the
same technique any Live-authorization white-box test needs, since starting
a genuine Live run requires real OpenAI/Tavily credentials this suite must
never use. A Gmail connection row is written directly through
`GmailConnectionRepository` (its own real code path — not bypassed) to
supply "sufficient fabricated/test-only authorization state" per the task
brief's criterion 4B, without ever touching real token crypto/OAuth.
"""

from __future__ import annotations

from sqlalchemy import update

from groundwork.models.tables import OutreachDraftRow, ReviewResultRow, RunRow
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.timeutil import utcnow
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion
from tests.api_helpers import login_as_operator

OPERATOR_EMAIL = "operator@example.com"


async def _flip_run_to_live(session_factory, run_id: str) -> None:
    async with session_factory() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(mode="live"))
        await session.commit()


async def _connect_gmail(session_factory, *, email: str = OPERATOR_EMAIL) -> None:
    repo = GmailConnectionRepository(session_factory)
    now = utcnow()
    await repo.upsert_connection(
        google_account_email=email,
        encrypted_refresh_token="test-only-not-a-real-ciphertext",
        key_version=1,
        scopes=["gmail.send", "gmail.metadata"],
        connected_at=now,
        connected_by_actor="test-operator",
        last_refreshed_at=now,
    )


async def _live_northwind_email_setup(client, session_factory, monkeypatch):
    """Runs Demo to completion, flips the run to `live`, connects Gmail as
    the operator's own address, then proposes+approves a Live `EMAIL_SEND`
    for Northwind's email draft. Returns `(proposal, run_id)`."""
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    await _flip_run_to_live(session_factory, run_id)
    await _connect_gmail(session_factory)

    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    assert proposal["origin"] == "LIVE_EXTERNAL", proposal
    assert proposal["sender_identifier"] == OPERATOR_EMAIL
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal

    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    return proposal, run_id


class TestCriterion4ADemoNoOperatorNoGmail:
    """4A — Demo: no operator session, no Gmail connection, propose ->
    approve -> execute succeeds; `DemoEmailSendProvider` used; result
    contains a `demo://` message id; zero network (Demo Mode never wires a
    live runtime at all in this test client)."""

    async def test_demo_action_succeeds_end_to_end_with_no_operator_and_no_gmail(self, client):
        run_id, prospects = await run_demo_play_to_completion(client)
        northwind = prospects["Northwind Labs"]
        aggregate = await get_aggregate(client, northwind["id"])
        draft = find_draft(aggregate, channel="email")

        proposal = await propose(client, draft["id"])
        assert proposal["origin"] == "DEMO_SIMULATED"
        assert proposal["policy_verdict"] == "ELIGIBLE"

        approve_resp = await approve(client, proposal["id"])
        assert approve_resp.status_code == 200, approve_resp.text

        execute_resp = await execute(client, proposal["id"])
        assert execute_resp.status_code == 200, execute_resp.text
        body = execute_resp.json()
        assert body["execution"]["status"] == "SUCCEEDED"
        assert body["execution"]["origin"] == "DEMO_SIMULATED"
        assert body["execution"]["provider"] == "demo"
        assert body["execution"]["dispatched"] is True
        assert body["execution"]["provider_message_id"].startswith("demo://")


class TestCriterion4BLiveStructuralRefusal:
    """4B — Live: valid operator session, otherwise sufficient fabricated/
    test-only authorization state, execution reaches the V2-H-specific
    structural refusal; no provider `send()`/network dispatch occur."""

    async def test_live_execute_reaches_structural_refusal_with_no_execution_row_created(
        self, client, session_factory, monkeypatch
    ):
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)

        execute_resp = await execute(client, proposal["id"])
        assert execute_resp.status_code == 403, execute_resp.text
        body = execute_resp.json()
        assert body["code"] == "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"

        async with session_factory() as session:
            from sqlalchemy import select

            from groundwork.models.tables import ActionExecutionRow

            result = await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
            assert result.scalar_one_or_none() is None, "no execution row must ever be created for a Live send"


class TestOrdinaryLiveWithoutOperatorIsUnauthorized:
    async def test_live_execute_without_operator_session_is_401(self, client, session_factory, monkeypatch):
        """Kept separate from the five-gates suite below per the task
        brief's explicit instruction to "separately retain the ordinary
        test.\""""
        # Build the Live proposal AS an operator (propose/approve both
        # require operator in Live Mode), then attempt to execute with a
        # FRESH, unauthenticated client — proving the gate is per-request,
        # not merely "was ever logged in once."
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)

        import httpx

        from groundwork.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
        ) as anon_client:
            resp = await execute(anon_client, proposal["id"])
            assert resp.status_code == 401, resp.text


class TestFiveLiveGatesIndependently:
    """Each of the five server-side gates rejects on its own — proven by
    violating exactly one at a time while leaving the other four intact."""

    async def test_gate_1_no_operator_session_401(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)
        import httpx

        from groundwork.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
        ) as anon_client:
            resp = await execute(anon_client, proposal["id"])
            assert resp.status_code == 401

    async def test_gate_2_no_approval_yields_not_approved(self, client, session_factory, monkeypatch):
        await login_as_operator(client, monkeypatch)
        run_id, prospects = await run_demo_play_to_completion(client)
        await _flip_run_to_live(session_factory, run_id)
        await _connect_gmail(session_factory)
        northwind = prospects["Northwind Labs"]
        aggregate = await get_aggregate(client, northwind["id"])
        draft = find_draft(aggregate, channel="email")
        proposal = await propose(client, draft["id"])
        # deliberately never approved
        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "NOT_APPROVED"

    async def test_gate_3_sender_changed_after_approval(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)
        # Reconnect Gmail to a DIFFERENT account after approval.
        await _connect_gmail(session_factory, email="someone-else@example.com")
        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "SENDER_CHANGED"

    async def test_gate_4_content_changed_after_approval(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)
        async with session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(OutreachDraftRow).where(OutreachDraftRow.id == proposal["draft_id"])
            )
            draft_row = result.scalar_one()
            draft_row.body = draft_row.body + "\nEdited after approval."
            await session.commit()

        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONTENT_CHANGED"

    async def test_gate_5_fresh_policy_evaluation_rejects_on_review_regression(
        self, client, session_factory, monkeypatch
    ):
        proposal, _ = await _live_northwind_email_setup(client, session_factory, monkeypatch)
        async with session_factory() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(ReviewResultRow).where(ReviewResultRow.prospect_id == proposal["prospect_id"])
            )
            review_row = result.scalars().first()
            review_row.verdict = "FAIL"
            await session.commit()

        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "REVIEW_NOT_PASSED"
