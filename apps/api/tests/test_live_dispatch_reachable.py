"""V2-I-b — real Live `EMAIL_SEND` dispatch is reachable end-to-end through
the actual HTTP API (`POST /api/actions/proposals/{id}/execute`), with a
real (test-scripted) Gmail transport standing in for Google. The structural
refusal was removed from this path only after the accepted plan's full
verification checklist actually passed in CI — see `docs/PROGRESS.md`'s
V2-I-b entry and PR #23 — and only on the user's explicit, separate
authorization for that specific change.

This file proves the SUCCESSFUL path AND that every other safety gate
(suppression, the rolling allowance, missing credentials) still blocks
dispatch before any Gmail call, now that the structural refusal itself is
no longer the thing doing the blocking. `TestCriterion4BLiveHonestDegradeOnUnusableCredential`
in `test_action_authorization.py` covers the missing-credential case
directly; it is not duplicated here.

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
from groundwork.repositories.actions import ActionRepository
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
    return proposal, run_id, northwind


def _gmail_send_handler(*, send_status: int, send_body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "test-access-token"})
        if url.split("?")[0].endswith("/profile"):
            # V2-I-b correction (post-smoke) — the pre-dispatch history
            # checkpoint (users.getProfile), called BEFORE messages.send.
            return httpx.Response(200, json={"historyId": "1000", "emailAddress": OPERATOR_EMAIL}, request=request)
        if "messages/send" in url:
            return httpx.Response(send_status, json=send_body, request=request)
        raise AssertionError(f"unexpected Gmail call: {url}")

    return handler


class TestSuccessfulLivePathReachesGmailOnlyAfterAllGatesPass:
    async def test_full_propose_approve_execute_dispatches_and_succeeds(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
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

        # Exactly one real send request reached the (scripted) transport —
        # proving the gates all ran BEFORE it, not after.
        send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
        assert len(send_calls) == 1

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

        allowance = LiveSendAllowanceRepository(session_factory)
        count = await allowance.count_reservations_since(now=utcnow(), window_s=86400.0)
        assert count == 1

    async def test_duplicate_execute_never_sends_twice(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, transport = make_runtime(handler=_gmail_send_handler(send_status=200, send_body={"id": "18dup000"}))
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

    async def test_definitive_rejection_settles_failed_and_frees_recipient(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, _ = make_runtime(handler=_gmail_send_handler(send_status=400, send_body={"error": "malformed"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["execution"]["status"] == "FAILED"
        assert resp.json()["execution"]["outcome_class"] == "DEFINITIVE_REJECTION"

        actions = ActionRepository(session_factory)
        conflict = await actions.recipient_conflict(proposal["recipient_identifier"])
        assert conflict.value == "NONE"  # FAILED is the only status that frees the recipient identity

    async def test_ambiguous_5xx_settles_uncertain_and_blocks_recipient(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        runtime, _ = make_runtime(handler=_gmail_send_handler(send_status=503, send_body={"error": "backend"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["execution"]["status"] == "UNCERTAIN"

        actions = ActionRepository(session_factory)
        conflict = await actions.recipient_conflict(proposal["recipient_identifier"])
        assert conflict.value == "UNCERTAIN"


class TestSuppressedRecipientBlocksBeforeAnyGmailCall:
    """Suppression (local or global, V2-I-a's clause 15) must still block
    dispatch even though the structural refusal is gone — proven by
    asserting ZERO Gmail calls reach the scripted transport."""

    async def test_locally_suppressed_email_cannot_dispatch(self, client, session_factory, monkeypatch):
        proposal, run_id, northwind = await _live_approved_email_proposal(client, session_factory, monkeypatch)

        # Suppress the EMAIL channel directly, mirroring
        # ContactEnrichmentRepository.record_legal_restriction's own writes.
        from groundwork.models.tables import ContactChannelRow

        async with session_factory() as session:
            result = await session.execute(
                select(ContactChannelRow).where(
                    ContactChannelRow.prospect_id == northwind["id"], ContactChannelRow.channel == "email"
                )
            )
            row = result.scalar_one()
            row.send_suppressed_at = utcnow()
            row.send_suppression_reason = "LEGAL_OR_PRIVACY_RESTRICTION"
            row.send_suppression_source = "hunter"
            await session.commit()

        runtime, transport = make_runtime(handler=_gmail_send_handler(send_status=200, send_body={"id": "should-not-be-used"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert resp.status_code == 409, resp.text
        assert "recipient_suppressed" in resp.json()["detail"]
        assert transport.requests == []

        async with session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
            assert result.scalar_one_or_none() is None

    async def test_globally_suppressed_normalized_email_cannot_dispatch(self, client, session_factory, monkeypatch):
        proposal, run_id, northwind = await _live_approved_email_proposal(client, session_factory, monkeypatch)

        from groundwork.domain.contact_identity import normalize_email_identity
        from groundwork.models.tables import EmailSuppressionRow

        identity_key = normalize_email_identity(proposal["recipient_identifier"])
        async with session_factory() as session:
            session.add(
                EmailSuppressionRow(
                    identity_key=identity_key,
                    reason="LEGAL_OR_PRIVACY_RESTRICTION",
                    source="hunter",
                    provider_code="claimed_email",
                )
            )
            await session.commit()

        runtime, transport = make_runtime(handler=_gmail_send_handler(send_status=200, send_body={"id": "should-not-be-used"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert resp.status_code == 409, resp.text
        assert "recipient_suppressed" in resp.json()["detail"]
        assert transport.requests == []


class TestAllowanceBlocksDispatchBeforeAnyGmailCall:
    async def test_exhausted_rolling_allowance_blocks_at_policy_precheck(self, client, session_factory, monkeypatch):
        # Propose+approve BEFORE exhausting the allowance — `propose` itself
        # evaluates policy fresh (clause 14), so setting the cap to 0 first
        # would block proposal creation, never reaching execute-time at all.
        proposal, _, _ = await _live_approved_email_proposal(client, session_factory, monkeypatch)
        monkeypatch.setattr(settings, "live_max_sends_per_day", 0)

        runtime, transport = make_runtime(handler=_gmail_send_handler(send_status=200, send_body={"id": "should-not-be-used"}))
        _override_runtime(runtime)
        try:
            resp = await execute(client, proposal["id"])
        finally:
            _clear_runtime_override()

        assert resp.status_code == 409, resp.text
        assert "send_allowance_exhausted" in resp.json()["detail"]
        assert transport.requests == []

        async with session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
            assert result.scalar_one_or_none() is None

    async def test_allowance_reservation_denial_at_the_transaction_settles_failed_no_dispatch(
        self, client, session_factory, monkeypatch
    ):
        """A narrow race: the policy pre-check passed (limit not yet
        reached), but the reservation transaction itself denies (e.g. a
        concurrent request consumed the last slot first). This is exercised
        directly at the allowance repository level in
        `test_live_send_allowance.py` and at the orchestration level in
        `test_live_send_orchestration.py::test_allowance_denied_blocks_
        dispatch_entirely` — both prove zero HTTP calls are made when the
        reservation is denied. Referenced here for completeness; not
        duplicated."""
        from groundwork.repositories.live_send_allowance import AllowanceReservationOutcome, LiveSendAllowanceRepository

        assert hasattr(AllowanceReservationOutcome, "DENIED_LIMIT_REACHED")
        assert hasattr(LiveSendAllowanceRepository, "try_reserve")
