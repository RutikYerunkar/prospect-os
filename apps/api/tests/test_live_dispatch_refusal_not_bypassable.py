"""V2-I-b gate-order correction (see `docs/PROGRESS.md`) — the regression
test this correction requires: merely having a fully working, real-Fernet-
encrypted Gmail connection (i.e. `build_gmail_send_provider` WOULD succeed)
and a Gmail transport scripted to accept the send must NEVER be enough to
bypass `LiveExternalEmailSendDisabled`. `execute_action`'s `LIVE_EXTERNAL`
branch calls `resolve_send_provider(Mode.LIVE)` — the unconditional refusal
— BEFORE `build_gmail_send_provider`/`dispatch_live_email_send` are ever
reached, so this proves the refusal is structural and independent of
whether the rest of the (fully implemented, independently tested) dispatch
path would have worked.

No real Google API call is ever made — `ScriptedGoogleTransport`/
`make_runtime`'s scripted handler stands in throughout.
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
    """A GENUINELY working connection — real Fernet ciphertext that WILL
    decrypt, exactly what `build_gmail_send_provider` needs to succeed.
    This is deliberately the strongest possible case for a bypass attempt."""
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


def _gmail_send_handler_ready_to_succeed():
    """Scripted to ACCEPT the send (200 + a Message.id) — if the refusal
    were somehow bypassed, this handler would make the test look like a
    real send happened. It must never be reached."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "test-access-token"})
        if "messages/send" in url:
            return httpx.Response(200, json={"id": "18shouldneverbecalled"}, request=request)
        raise AssertionError(f"unexpected Gmail call: {url}")

    return handler


class TestRefusalNotBypassableByWorkingCredential:
    async def test_working_gmail_connection_still_hits_structural_refusal(self, client, session_factory, monkeypatch):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, transport = make_runtime(handler=_gmail_send_handler_ready_to_succeed())
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert resp.status_code == 403, resp.text
        assert resp.json()["code"] == "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"

        # Zero Gmail calls of any kind — the refusal fires before the OAuth
        # token refresh, let alone the send itself.
        assert transport.requests == []

    async def test_no_execution_row_ever_created_despite_working_credential(
        self, client, session_factory, monkeypatch
    ):
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, _ = make_runtime(handler=_gmail_send_handler_ready_to_succeed())
        _override_runtime(runtime)
        try:
            await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        async with session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
            assert result.scalar_one_or_none() is None

    async def test_repeated_execute_attempts_all_refused_identically(self, client, session_factory, monkeypatch):
        """Not a one-time gate — every attempt hits the same refusal,
        proving it's not a stateful check that could be exhausted/bypassed
        on a retry."""
        proposal, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, transport = make_runtime(handler=_gmail_send_handler_ready_to_succeed())
        _override_runtime(runtime)
        try:
            for _ in range(3):
                resp = await execute(client, proposal["id"])
                assert resp.status_code == 403
                assert resp.json()["code"] == "LIVE_EXTERNAL_EMAIL_SEND_DISABLED"
        finally:
            _clear_runtime_override()
        assert transport.requests == []

    async def test_resolve_send_provider_live_still_unconditionally_raises(self):
        """Direct proof at the function level, independent of the router:
        `resolve_send_provider(Mode.LIVE)` itself was never changed by this
        correction — it never returned a provider to begin with."""
        from groundwork.models.enums import Mode
        from groundwork.providers.send_base import LiveExternalEmailSendDisabled
        from groundwork.providers.send_registry import resolve_send_provider

        try:
            resolve_send_provider(Mode.LIVE)
            raised = False
        except LiveExternalEmailSendDisabled:
            raised = True
        assert raised
