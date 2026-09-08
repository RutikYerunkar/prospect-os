"""V2-I-b — the refusal-removal gate's own proof: real Live `EMAIL_SEND`
dispatch is now reachable end-to-end through the actual HTTP API
(`POST /api/actions/proposals/{id}/execute`), with a real (test-scripted)
Gmail transport standing in for Google. This is the test that could not
exist before the refusal was removed — `resolve_send_provider(Mode.LIVE)`
used to make this structurally impossible; now the real seam
(`build_gmail_send_provider` + `dispatch_live_email_send`) carries it
through.

No real Google API call is ever made — `ScriptedGoogleTransport`/
`ScriptedGmailTransport`-style scripted handlers stand in throughout,
exactly like every other Live provider test in this suite.
"""

from __future__ import annotations

import httpx
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from groundwork.api.deps import get_google_oauth_runtime
from groundwork.config import settings
from groundwork.main import app
from groundwork.models.tables import ActionExecutionRow, RunRow
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.repositories.live_send_allowance import LiveSendAllowanceRepository
from groundwork.timeutil import utcnow
from groundwork.token_crypto import encrypt_refresh_token
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion
from tests.api_helpers import login_as_operator
from tests.gmail_oauth_helpers import make_runtime

OPERATOR_EMAIL = "operator@example.com"


def _make_test_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")


async def _flip_run_to_live(session_factory, run_id: str) -> None:
    async with session_factory() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(mode="live"))
        await session.commit()


async def _connect_gmail_for_real(session_factory, monkeypatch, *, email: str = OPERATOR_EMAIL) -> None:
    monkeypatch.setattr(settings, "token_encryption_key", _make_test_fernet_key())
    monkeypatch.setattr(settings, "token_encryption_key_old", None)
    ciphertext, key_version = encrypt_refresh_token("test-only-refresh-token-not-real")
    repo = GmailConnectionRepository(session_factory)
    now = utcnow()
    await repo.upsert_connection(
        google_account_email=email,
        encrypted_refresh_token=ciphertext,
        key_version=key_version,
        scopes=["gmail.send", "gmail.metadata"],
        connected_at=now,
        connected_by_actor="test-operator",
        last_refreshed_at=now,
    )


def _override_runtime(runtime) -> None:
    app.dependency_overrides[get_google_oauth_runtime] = lambda: runtime


def _clear_runtime_override() -> None:
    app.dependency_overrides.pop(get_google_oauth_runtime, None)


async def _live_approved_email_proposal(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    await _flip_run_to_live(session_factory, run_id)
    await _connect_gmail_for_real(session_factory, monkeypatch)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    assert proposal["origin"] == "LIVE_EXTERNAL", proposal
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    return proposal, run_id


def _gmail_send_handler(*, send_status: int, send_body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "test-access-token"})
        if "messages/send" in url:
            return httpx.Response(send_status, json=send_body, request=request)
        raise AssertionError(f"unexpected Gmail call: {url}")

    return handler


class TestLiveDispatchNowReachable:
    async def test_full_propose_approve_execute_dispatches_and_succeeds(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, transport = make_runtime(
            handler=_gmail_send_handler(send_status=200, send_body={"id": "18abc123def"})
        )
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert resp.status_code == 200, resp.text
        body = resp.json()
        execution = body["execution"]
        assert execution["status"] == "SUCCEEDED"
        assert execution["origin"] == "LIVE_EXTERNAL"
        assert execution["provider"] == "gmail"
        assert execution["dispatched"] is True
        assert execution["provider_message_id"] == "18abc123def"

        # Exactly one real send request reached the (scripted) transport.
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 1

        # Message-ID was generated and persisted before dispatch.
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
                )
            ).scalar_one()
            assert row.message_id_header is not None
            assert row.message_id_header.startswith("<")
            assert row.claimed_at is not None
            assert row.dispatched_at is not None
            assert row.settled_at is not None
            assert row.claimed_at <= row.dispatched_at <= row.settled_at

        # The allowance was actually reserved.
        allowance = LiveSendAllowanceRepository(session_factory)
        count = await allowance.count_reservations_since(now=utcnow(), window_s=86400.0)
        assert count == 1

    async def test_duplicate_execute_never_sends_twice(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, transport = make_runtime(
            handler=_gmail_send_handler(send_status=200, send_body={"id": "18dup000"})
        )
        _override_runtime(runtime)
        try:
            first = await execute(client, proposal["id"])
            second = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["execution"]["id"] == second.json()["execution"]["id"]
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 1

    async def test_definitive_rejection_settles_failed_and_frees_recipient_for_a_new_proposal(
        self, client, session_factory, monkeypatch
    ):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, _ = make_runtime(handler=_gmail_send_handler(send_status=400, send_body={"error": "malformed"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["execution"]["status"] == "FAILED"
        assert resp.json()["execution"]["outcome_class"] == "DEFINITIVE_REJECTION"

    async def test_ambiguous_5xx_settles_uncertain_and_blocks_recipient(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, _ = make_runtime(handler=_gmail_send_handler(send_status=503, send_body={"error": "backend"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["execution"]["status"] == "UNCERTAIN"

        from groundwork.repositories.actions import ActionRepository

        actions = ActionRepository(session_factory)
        conflict = await actions.recipient_conflict(proposal["recipient_identifier"])
        assert conflict.value == "UNCERTAIN"
