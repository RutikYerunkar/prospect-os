"""V2-I-b Phase 8/9 — reconciliation and stale recovery endpoints.

Setup mirrors `test_action_authorization.py`: a real Demo run executed to
completion, its `mode` flipped to `live` directly via the DB, and a Gmail
connection written through `GmailConnectionRepository`'s own code path (never
bypassed). An `ActionExecutionRow` is then inserted directly — the only way
to get a LIVE_EXTERNAL execution row into a specific state without a real
Gmail send, since `resolve_send_provider(Mode.LIVE)` still unconditionally
refuses at this checkpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select, update

from cryptography.fernet import Fernet

from groundwork.api.deps import get_google_oauth_runtime
from groundwork.config import settings
from groundwork.main import app
from groundwork.models.tables import ActionExecutionRow, ApprovalRow, RunRow
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.timeutil import utcnow
from groundwork.token_crypto import encrypt_refresh_token
from tests.action_helpers import approve, find_draft, get_aggregate, propose, run_demo_play_to_completion
from tests.api_helpers import login_as_operator
from tests.gmail_oauth_helpers import make_runtime

OPERATOR_EMAIL = "operator@example.com"


def _make_test_fernet_key() -> str:
    return Fernet.generate_key().decode("utf-8")


async def _flip_run_to_live(session_factory, run_id: str) -> None:
    async with session_factory() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(mode="live"))
        await session.commit()


async def _connect_gmail(session_factory, monkeypatch, *, email: str = OPERATOR_EMAIL) -> None:
    # A REAL Fernet-encrypted (fake) refresh token — `build_gmail_send_
    # provider` decrypts it via the real `token_crypto` code path, so a
    # placeholder string would fail decryption and mask the reconciliation
    # behavior under test with an unrelated 422.
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


async def _live_approved_proposal(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    await _flip_run_to_live(session_factory, run_id)
    await _connect_gmail(session_factory, monkeypatch)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    assert proposal["origin"] == "LIVE_EXTERNAL", proposal
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    return proposal, run_id, draft


async def _insert_execution(
    session_factory,
    *,
    proposal: dict,
    status: str,
    claimed_at: datetime,
    dispatched_at: datetime | None,
    settled_at: datetime | None = None,
    message_id_header: str | None = "<test-exec@example.com>",
    pre_dispatch_history_id: str | None = "1000",
    reconcile_attempts: int = 0,
) -> str:
    async with session_factory() as session:
        result = await session.execute(select(ApprovalRow).where(ApprovalRow.action_proposal_id == proposal["id"]))
        approval = result.scalars().first()
        execution_id = str(uuid.uuid4())
        session.add(
            ActionExecutionRow(
                id=execution_id,
                action_proposal_id=proposal["id"],
                approval_id=approval.id,
                prospect_id=proposal["prospect_id"],
                run_id=proposal["run_id"],
                action_type="EMAIL_SEND",
                provider="gmail",
                status=status,
                idempotency_key=str(uuid.uuid4()),
                recipient_identity_key=proposal["recipient_identifier"],
                sender_identifier=proposal["sender_identifier"],
                origin="LIVE_EXTERNAL",
                message_id_header=message_id_header,
                pre_dispatch_history_id=pre_dispatch_history_id,
                dispatched=dispatched_at is not None,
                claimed_at=claimed_at,
                dispatched_at=dispatched_at,
                settled_at=settled_at,
                reconcile_attempts=reconcile_attempts,
            )
        )
        await session.commit()
    return execution_id


def _rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def _override_runtime(runtime) -> None:
    app.dependency_overrides[get_google_oauth_runtime] = lambda: runtime


def _clear_runtime_override() -> None:
    app.dependency_overrides.pop(get_google_oauth_runtime, None)


def _history_body(message_ids: list[str]) -> dict:
    return {"history": [{"id": "h1", "messagesAdded": [{"message": {"id": mid}} for mid in message_ids]}]}


def _gmail_handler_history(*, history_body, get_body_by_id):
    """V2-I-b correction (post-smoke) — history.list + messages.get, never
    messages.list, never `q`."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "tok"})
        assert "q=" not in url  # never uses the q parameter
        path = url.split("?")[0]
        if path.endswith("/history"):
            return httpx.Response(200, json=history_body, request=request)
        msg_id = path.rstrip("/").rsplit("/", 1)[-1]
        body = get_body_by_id.get(msg_id, {"payload": {"headers": []}})
        return httpx.Response(200, json=body, request=request)

    return handler


class TestReconcileEndpoint:
    async def test_reconcile_found_settles_succeeded(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )
        runtime, _ = make_runtime(
            handler=_gmail_handler_history(
                history_body=_history_body(["m1"]),
                get_body_by_id={
                    "m1": {
                        "payload": {
                            "headers": [
                                {"name": "Subject", "value": draft["subject"]},
                                {"name": "To", "value": proposal["recipient_identifier"]},
                                {"name": "From", "value": proposal["sender_identifier"]},
                                {"name": "Date", "value": _rfc2822(dispatched_at)},
                            ]
                        }
                    }
                },
            )
        )
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "FOUND"
        assert body["execution"]["status"] == "SUCCEEDED"
        assert body["execution"]["provider_message_id"] == "m1"

    async def test_reconcile_survives_absent_message_id_headers(self, client, session_factory, monkeypatch):
        """Regression test — the real V2-I-b smoke send's exact message
        carried NEITHER `Message-ID` NOR `X-Google-Original-Message-ID`.
        Reconciliation through the real endpoint must still succeed."""
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )
        runtime, _ = make_runtime(
            handler=_gmail_handler_history(
                history_body=_history_body(["m1"]),
                get_body_by_id={
                    "m1": {
                        "payload": {
                            # Deliberately no Message-ID / X-Google-Original-Message-ID key.
                            "headers": [
                                {"name": "Subject", "value": draft["subject"]},
                                {"name": "To", "value": proposal["recipient_identifier"]},
                                {"name": "From", "value": proposal["sender_identifier"]},
                                {"name": "Date", "value": _rfc2822(dispatched_at)},
                            ]
                        }
                    }
                },
            )
        )
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        assert resp.json()["reconcile_status"] == "FOUND"

    async def test_reconcile_not_found_stays_uncertain_never_failed(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )
        runtime, _ = make_runtime(
            handler=_gmail_handler_history(
                history_body=_history_body(["m1"]),
                get_body_by_id={
                    "m1": {
                        "payload": {
                            "headers": [
                                {"name": "Subject", "value": "a completely different subject"},
                                {"name": "To", "value": proposal["recipient_identifier"]},
                                {"name": "From", "value": proposal["sender_identifier"]},
                                {"name": "Date", "value": _rfc2822(dispatched_at)},
                            ]
                        }
                    }
                },
            )
        )
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "NOT_FOUND_WITHIN_BOUNDS"
        assert body["execution"]["status"] == "UNCERTAIN"

    async def test_reconcile_ambiguous_never_picks_one_stays_uncertain(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )
        matching_headers = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": draft["subject"]},
                    {"name": "To", "value": proposal["recipient_identifier"]},
                    {"name": "From", "value": proposal["sender_identifier"]},
                    {"name": "Date", "value": _rfc2822(dispatched_at)},
                ]
            }
        }
        runtime, _ = make_runtime(
            handler=_gmail_handler_history(
                history_body=_history_body(["m1", "m2"]),
                get_body_by_id={"m1": matching_headers, "m2": matching_headers},
            )
        )
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "AMBIGUOUS"
        assert body["execution"]["status"] == "UNCERTAIN"

    async def test_reconcile_history_expired_stays_uncertain(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "oauth2.googleapis.com/token" in url:
                return httpx.Response(200, json={"access_token": "tok"})
            if url.split("?")[0].endswith("/history"):
                return httpx.Response(404, json={"error": "startHistoryId too old"}, request=request)
            raise AssertionError("must not fetch any candidate after a 404 on history.list")

        runtime, _ = make_runtime(handler=handler)
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "HISTORY_EXPIRED"
        assert body["execution"]["status"] == "UNCERTAIN"

    async def test_reconcile_lookup_failed_never_converts_to_failed(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if "oauth2.googleapis.com/token" in str(request.url):
                return httpx.Response(200, json={"access_token": "tok"})
            raise httpx.ConnectError("refused", request=request)

        runtime, _ = make_runtime(handler=handler)
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "LOOKUP_FAILED"
        assert body["execution"]["status"] == "UNCERTAIN"

    async def test_reconcile_not_reconcilable_without_pre_dispatch_history_id(
        self, client, session_factory, monkeypatch
    ):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
            pre_dispatch_history_id=None,  # execution predates this correction
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "NOT_RECONCILABLE"

    async def test_succeeded_execution_cannot_be_reconciled(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="SUCCEEDED",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=now - timedelta(minutes=2),
            settled_at=now - timedelta(minutes=1),
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "NOT_UNCERTAIN"

    async def test_repeated_reconcile_is_idempotent_bookkeeping_only(self, client, session_factory, monkeypatch):
        """Two NOT_FOUND_WITHIN_BOUNDS calls in a row — attempts increments
        correctly each time, status never changes, never crashes, and
        never makes more than the expected Gmail calls per attempt."""
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
        )
        no_match = {
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "not it"},
                    {"name": "To", "value": proposal["recipient_identifier"]},
                    {"name": "From", "value": proposal["sender_identifier"]},
                    {"name": "Date", "value": _rfc2822(dispatched_at)},
                ]
            }
        }

        for expected_attempts in (1, 2):
            runtime, _ = make_runtime(
                handler=_gmail_handler_history(history_body=_history_body(["m1"]), get_body_by_id={"m1": no_match})
            )
            _override_runtime(runtime)
            try:
                resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
            finally:
                _clear_runtime_override()
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["reconcile_status"] == "NOT_FOUND_WITHIN_BOUNDS"
            assert body["execution"]["status"] == "UNCERTAIN"
            assert body["execution"]["reconcile_attempts"] == expected_attempts

    async def test_attempts_exhausted_window_open_is_zero_egress(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        dispatched_at = now - timedelta(minutes=2)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=now - timedelta(minutes=3),
            dispatched_at=dispatched_at,
            settled_at=dispatched_at,
            reconcile_attempts=settings.reconcile_max_attempts,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("zero-egress: no Gmail call should be made")

        runtime, transport = make_runtime(handler=handler)
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "ATTEMPTS_EXHAUSTED_WINDOW_OPEN"
        assert body["attempts_remaining"] == 0
        assert body["next_terminalization_at"] is not None
        assert body["execution"]["status"] == "UNCERTAIN"
        assert transport.requests == []

    async def test_window_expired_abandons_with_zero_egress(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        long_ago = now - timedelta(seconds=settings.reconcile_window_s + 60)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=long_ago,
            dispatched_at=long_ago,
            settled_at=long_ago,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("zero-egress: no Gmail call should be made")

        runtime, transport = make_runtime(handler=handler)
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reconcile_status"] == "ABANDONED"
        assert body["execution"]["status"] == "ABANDONED"
        assert transport.requests == []

    async def test_abandoned_still_blocks_recipient(self, client, session_factory, monkeypatch):
        """ABANDONED remains in the §3.5B blocking status set — a later
        proposal for the same recipient is still blocked."""
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        long_ago = now - timedelta(seconds=settings.reconcile_window_s + 60)
        execution_id = await _insert_execution(
            session_factory,
            proposal=proposal,
            status="UNCERTAIN",
            claimed_at=long_ago,
            dispatched_at=long_ago,
            settled_at=long_ago,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("zero-egress")

        runtime, _ = make_runtime(handler=handler)
        _override_runtime(runtime)
        try:
            resp = await client.post(f"/api/actions/executions/{execution_id}/reconcile")
        finally:
            _clear_runtime_override()
        assert resp.json()["execution"]["status"] == "ABANDONED"

        from groundwork.repositories.actions import ActionRepository

        actions = ActionRepository(session_factory)
        conflict = await actions.recipient_conflict(proposal["recipient_identifier"])
        assert conflict.value == "ABANDONED"

    async def test_reconcile_requires_operator(self, client, session_factory, monkeypatch):
        proposal, _, draft = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="UNCERTAIN", claimed_at=now, dispatched_at=now, settled_at=now
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
        ) as anon_client:
            resp = await anon_client.post(f"/api/actions/executions/{execution_id}/reconcile")
            assert resp.status_code == 401


class TestRecoverEndpoint:
    async def test_stale_claimed_recovers_to_failed(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        stale_before = utcnow() - timedelta(seconds=settings.execution_stale_lease_s + 60)
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="CLAIMED", claimed_at=stale_before, dispatched_at=None
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recovered"] is True
        assert body["execution"]["status"] == "FAILED"
        assert body["execution"]["outcome_class"] == "PROVEN_NOT_DISPATCHED"

    async def test_stale_in_flight_recovers_to_uncertain(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        stale_before = utcnow() - timedelta(seconds=settings.execution_stale_lease_s + 60)
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="IN_FLIGHT", claimed_at=stale_before, dispatched_at=stale_before
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recovered"] is True
        assert body["execution"]["status"] == "UNCERTAIN"

    async def test_younger_than_stale_lease_is_refused(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="CLAIMED", claimed_at=now, dispatched_at=None
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recovered"] is False
        assert body["execution"]["status"] == "CLAIMED"

    async def test_never_dispatches_never_releases_allowance(self, client, session_factory, monkeypatch):
        from groundwork.repositories.live_send_allowance import LiveSendAllowanceRepository

        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        stale_before = utcnow() - timedelta(seconds=settings.execution_stale_lease_s + 60)
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="CLAIMED", claimed_at=stale_before, dispatched_at=None
        )
        allowance = LiveSendAllowanceRepository(session_factory)
        outcome = await allowance.try_reserve(execution_id, now=utcnow(), window_s=86400.0, limit=50)
        assert outcome.value == "GRANTED"

        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200

        # Reservation still counted — never released for any recovery outcome.
        assert await allowance.count_reservations_since(now=utcnow(), window_s=86400.0) == 1

    async def test_impossible_shape_settles_uncertain_with_anomaly_event(self, client, session_factory, monkeypatch):
        """CLAIMED with dispatched_at SET is a contradiction (transition_to_
        in_flight always sets both atomically) — recover must never crash on
        it, and must settle it to UNCERTAIN with an anomaly audit event."""
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        stale_before = utcnow() - timedelta(seconds=settings.execution_stale_lease_s + 60)
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="CLAIMED", claimed_at=stale_before, dispatched_at=stale_before
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["recovered"] is True
        assert body["execution"]["status"] == "UNCERTAIN"

        from groundwork.repositories.actions import ActionRepository

        actions = ActionRepository(session_factory)
        events = await actions.list_events_for_execution(execution_id)
        assert any(e.type == "execution_recovery_anomaly" for e in events)

    async def test_terminal_status_not_eligible_for_recovery(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        now = utcnow()
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="SUCCEEDED", claimed_at=now, dispatched_at=now
        )
        resp = await client.post(f"/api/actions/executions/{execution_id}/recover")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recovered"] is False

    async def test_recover_requires_operator(self, client, session_factory, monkeypatch):
        proposal, _, _ = await _live_approved_proposal(client, session_factory, monkeypatch)
        stale_before = utcnow() - timedelta(seconds=settings.execution_stale_lease_s + 60)
        execution_id = await _insert_execution(
            session_factory, proposal=proposal, status="CLAIMED", claimed_at=stale_before, dispatched_at=None
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
        ) as anon_client:
            resp = await anon_client.post(f"/api/actions/executions/{execution_id}/recover")
            assert resp.status_code == 401
