"""V2-I-c — dedicated `LINKEDIN_COPY_AND_OPEN` action-path invariants,
proven end-to-end through the real API against the real (Demo/Live) engine
output, complementing `test_action_policy.py`'s pure-function coverage and
`test_action_policy_integration.py`'s single happy-path LinkedIn execute.

LinkedIn is COPY_AND_OPEN only (no `LINKEDIN_SEND` action exists anywhere —
see `models/enums.py::ActionType`): executing it never touches a send
provider, never dispatches anything, and never consumes or is blocked by
any of the EMAIL_SEND-only recipient-identity machinery (clause 12's
`recipient_conflict`, clause 14's live allowance, clause 15's suppression).
This file proves those boundaries hold through the real router, not just in
`domain/action_policy.py::evaluate()`'s pure kwargs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from groundwork.api.deps import get_google_oauth_runtime
from groundwork.config import settings
from groundwork.domain.action_policy import RecipientConflict
from groundwork.domain.contact_identity import normalize_email_identity
from groundwork.engine.runner import Repos
from groundwork.main import app
from groundwork.models.enums import EnrichmentAttemptStatus, EnrichmentOperation, LinkedInIdentityState
from groundwork.models.tables import ActionExecutionRow, ContactChannelRow, RunRow
from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptTelemetry
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.gmail_connection import GmailConnectionRepository
from groundwork.timeutil import utcnow
from groundwork.token_crypto import encrypt_refresh_token
from tests.action_helpers import (
    approve,
    execute,
    find_draft,
    get_aggregate,
    propose,
    propose_approve,
    run_demo_play_to_completion,
)
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


def _override_gmail_runtime(runtime) -> None:
    app.dependency_overrides[get_google_oauth_runtime] = lambda: runtime


def _clear_gmail_runtime_override() -> None:
    app.dependency_overrides.pop(get_google_oauth_runtime, None)


def _forbidden_resolve_send_provider(mode):
    raise AssertionError("resolve_send_provider must never be called for LINKEDIN_COPY_AND_OPEN")


async def _forbidden_build_gmail_send_provider(gmail, oauth_runtime):
    raise AssertionError("build_gmail_send_provider must never be called for LINKEDIN_COPY_AND_OPEN")


def _patch_forbid_send_provider_resolvers():
    """Directly patches (not via the `monkeypatch` fixture, whose undo is
    scoped to test teardown, not to a point mid-test) BOTH send-provider
    resolution seams the actions router can call (`resolve_send_provider`
    for Demo, `build_gmail_send_provider` for Live) to raise if invoked at
    all. Used to prove the LinkedIn execute path (D6/D2) never reaches
    either — not merely that it doesn't happen to send anything. Returns
    the originals so a test that ALSO needs a real send afterward (e.g. a
    later real EMAIL_SEND in the same test) can restore them."""
    import groundwork.api.routers.actions as actions_module

    originals = (actions_module.resolve_send_provider, actions_module.build_gmail_send_provider)
    actions_module.resolve_send_provider = _forbidden_resolve_send_provider
    actions_module.build_gmail_send_provider = _forbidden_build_gmail_send_provider
    return originals


def _restore_send_provider_resolvers(originals) -> None:
    import groundwork.api.routers.actions as actions_module

    actions_module.resolve_send_provider, actions_module.build_gmail_send_provider = originals


def _gmail_send_handler(*, send_status: int, send_body: dict):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(200, json={"access_token": "test-access-token"})
        if url.split("?")[0].endswith("/profile"):
            return httpx.Response(200, json={"historyId": "1000", "emailAddress": OPERATOR_EMAIL}, request=request)
        if "messages/send" in url:
            return httpx.Response(send_status, json=send_body, request=request)
        raise AssertionError(f"unexpected Gmail call: {url}")

    return handler


async def _restrict_own_email_channel(session_factory, *, run_id: str, prospect_id: str) -> None:
    """Reports a legal/privacy restriction (V2-I-a) directly against a
    prospect's OWN, already-observed email channel — the real repository
    path `providers/live/hunter_enrichment.py`'s HTTP 451 routes through
    (`ContactEnrichmentRepository.record_legal_restriction`), used here
    purely to put the EMAIL channel into a suppressed state so we can prove
    the sibling LinkedIn channel's own proposal is untouched by it."""
    repos = Repos.build(session_factory)
    restricted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    telemetry = [
        EnrichmentAttemptTelemetry(
            provider="hunter",
            operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id="cg-restrict-linkedin-test",
            attempt=1,
            attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=EnrichmentAttemptStatus.LEGAL_RESTRICTION,
            started_at=restricted_at,
            finished_at=restricted_at,
            latency_ms=1.0,
            input_digest="digest",
        )
    ]
    await repos.contact_enrichment.record_legal_restriction(
        run_id=run_id,
        prospect_id=prospect_id,
        provider="hunter",
        call_group_id="cg-restrict-linkedin-test",
        telemetry=telemetry,
        provider_code="claimed_email",
    )


async def _set_linkedin_identity_match_state(session_factory, *, prospect_id: str, state: str) -> None:
    async with session_factory() as session:
        await session.execute(
            update(ContactChannelRow)
            .where(ContactChannelRow.prospect_id == prospect_id, ContactChannelRow.channel == "linkedin")
            .values(identity_match_state=state)
        )
        await session.commit()


# --- A. Demo execute: null provenance, zero send calls, resolvers untouched ---


async def test_linkedin_execute_has_null_provenance_and_touches_no_send_provider(client, session_factory):
    _run_id, prospects = await run_demo_play_to_completion(client)
    sable = prospects["Sable Compute"]
    aggregate = await get_aggregate(client, sable["id"])
    draft = find_draft(aggregate, channel="linkedin")

    proposal = await propose_approve(client, draft["id"])
    originals = _patch_forbid_send_provider_resolvers()
    try:
        resp = await execute(client, proposal["id"])
    finally:
        _restore_send_provider_resolvers(originals)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    execution = body["execution"]
    assert execution["status"] == "SUCCEEDED"
    assert execution["provider"] is None
    assert execution["dispatched"] is False
    assert execution["provider_message_id"] is None

    async with session_factory() as session:
        row = (
            await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
        ).scalar_one()
        assert row.sender_identifier is None
        assert row.recipient_identity_key is None
        assert row.provider is None
        assert row.dispatched is False

    audit = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    assert audit.status_code == 200, audit.text
    assert audit.json()["send_calls"] == []


# --- B. Idempotency: a duplicate execute never creates a second execution ---


async def test_linkedin_duplicate_execute_returns_same_execution_zero_send_calls(client, session_factory):
    _run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="linkedin")

    proposal = await propose_approve(client, draft["id"])
    first = await execute(client, proposal["id"])
    second = await execute(client, proposal["id"])
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["execution"]["id"] == second.json()["execution"]["id"]

    async with session_factory() as session:
        count = (
            await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.action_proposal_id == proposal["id"])
            )
        ).scalars().all()
        assert len(count) == 1

    audit = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    assert audit.json()["send_calls"] == []


# --- C. Live: no recipient identity consumed; a later real EMAIL_SEND to
# the same person is unaffected by the earlier LinkedIn execute. ---


async def test_linkedin_live_execute_consumes_no_recipient_identity_and_email_send_still_succeeds(
    client, session_factory, monkeypatch
):
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    await _flip_run_to_live(session_factory, run_id)
    await _connect_gmail_for_real(session_factory, monkeypatch)

    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    email_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "email")
    assert email_channel["verification_state"] == "VERIFIED"
    email_identity_key = normalize_email_identity(email_channel["identifier"])

    linkedin_draft = find_draft(aggregate, channel="linkedin")
    linkedin_proposal = await propose(client, linkedin_draft["id"])
    assert linkedin_proposal["origin"] == "LIVE_EXTERNAL"
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE", linkedin_proposal
    r = await approve(client, linkedin_proposal["id"])
    assert r.status_code == 200, r.text

    originals = _patch_forbid_send_provider_resolvers()
    try:
        li_exec_resp = await execute(client, linkedin_proposal["id"])
    finally:
        _restore_send_provider_resolvers(originals)
    assert li_exec_resp.status_code == 200, li_exec_resp.text
    assert li_exec_resp.json()["execution"]["status"] == "SUCCEEDED"
    assert li_exec_resp.json()["execution"]["provider"] is None

    # No live recipient identity was reserved/consumed by the LinkedIn
    # execute — the SAME email identity this prospect's email channel would
    # use is still completely free.
    actions_repo = ActionRepository(session_factory)
    conflict = await actions_repo.recipient_conflict(email_identity_key)
    assert conflict is RecipientConflict.NONE

    # A subsequent Live EMAIL_SEND to that same person is NOT blocked by
    # the earlier LinkedIn action, and really dispatches.
    email_draft = find_draft(aggregate, channel="email")
    email_proposal = await propose(client, email_draft["id"])
    assert email_proposal["policy_verdict"] == "ELIGIBLE", email_proposal
    r = await approve(client, email_proposal["id"])
    assert r.status_code == 200, r.text

    runtime, transport = make_runtime(
        handler=_gmail_send_handler(send_status=200, send_body={"id": "18abc123def"})
    )
    _override_gmail_runtime(runtime)
    try:
        email_exec_resp = await execute(client, email_proposal["id"])
    finally:
        _clear_gmail_runtime_override()
    assert email_exec_resp.status_code == 200, email_exec_resp.text
    email_execution = email_exec_resp.json()["execution"]
    assert email_execution["status"] == "SUCCEEDED"
    assert email_execution["dispatched"] is True
    send_calls = [r for r in transport.requests if "messages/send" in str(r.url)]
    assert len(send_calls) == 1


# --- D. A prior real email send to the recipient does not block a sibling
# LinkedIn proposal for the same prospect (clause 12 is EMAIL_SEND-only). ---


async def test_prior_live_email_send_does_not_block_sibling_linkedin_proposal(client, session_factory, monkeypatch):
    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    await _flip_run_to_live(session_factory, run_id)
    await _connect_gmail_for_real(session_factory, monkeypatch)

    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    email_draft = find_draft(aggregate, channel="email")
    email_proposal = await propose(client, email_draft["id"])
    assert email_proposal["policy_verdict"] == "ELIGIBLE", email_proposal
    r = await approve(client, email_proposal["id"])
    assert r.status_code == 200, r.text

    runtime, _transport = make_runtime(
        handler=_gmail_send_handler(send_status=200, send_body={"id": "18already000"})
    )
    _override_gmail_runtime(runtime)
    try:
        email_exec_resp = await execute(client, email_proposal["id"])
    finally:
        _clear_gmail_runtime_override()
    assert email_exec_resp.status_code == 200, email_exec_resp.text
    assert email_exec_resp.json()["execution"]["status"] == "SUCCEEDED"

    # This recipient now genuinely has a SUCCEEDED prior Live send on
    # record — clause 12 would block a genuinely NEW email proposal
    # (different content, so NOT the same idempotent-on-content-hash
    # proposal) to the same recipient outright.
    actions_repo = ActionRepository(session_factory)
    email_identity_key = normalize_email_identity(
        next(c for c in aggregate["contact_channels"] if c["channel"] == "email")["identifier"]
    )
    assert await actions_repo.recipient_conflict(email_identity_key) is RecipientConflict.SUCCEEDED

    # A second, genuinely different email draft (different body -> different
    # content hash, so `create_proposal`'s idempotent-on-content-hash lookup
    # does NOT just hand back the already-executed proposal) for the SAME
    # prospect/recipient is blocked by clause 12.
    async with session_factory() as session:
        from groundwork.models.tables import OutreachDraftRow

        new_draft = OutreachDraftRow(
            id="new-email-draft-same-recipient",
            prospect_id=northwind["id"],
            channel="email",
            step_index=1,
            subject="A different subject",
            body="A completely different body for the same recipient.",
        )
        session.add(new_draft)
        await session.commit()
    second_email_proposal = await propose(client, "new-email-draft-same-recipient")
    assert second_email_proposal["policy_verdict"] == "BLOCKED", second_email_proposal
    assert "already_sent_to_recipient" in second_email_proposal["blocked_reasons"]

    # But the sibling LinkedIn proposal for the SAME prospect is completely
    # unaffected — clause 12 never applies to LINKEDIN_COPY_AND_OPEN.
    linkedin_draft = find_draft(aggregate, channel="linkedin")
    linkedin_proposal = await propose(client, linkedin_draft["id"])
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE", linkedin_proposal


# --- E. A legal/privacy suppression on the EMAIL channel does not affect
# the sibling LinkedIn proposal (clause 15 is EMAIL_SEND-only), proven
# through the real API/repository wiring rather than the pure clause. ---


async def test_email_suppression_does_not_affect_sibling_linkedin_proposal(client, session_factory):
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    email_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "email")
    assert email_channel["verification_state"] == "VERIFIED"
    assert email_channel["send_suppressed_at"] is None

    await _restrict_own_email_channel(session_factory, run_id=run_id, prospect_id=northwind["id"])

    email_draft = find_draft(aggregate, channel="email")
    email_proposal = await propose(client, email_draft["id"])
    assert email_proposal["policy_verdict"] == "BLOCKED", email_proposal
    assert "recipient_suppressed" in email_proposal["blocked_reasons"]

    linkedin_draft = find_draft(aggregate, channel="linkedin")
    linkedin_proposal = await propose(client, linkedin_draft["id"])
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE", linkedin_proposal
    assert "recipient_suppressed" not in linkedin_proposal["blocked_reasons"]

    r = await approve(client, linkedin_proposal["id"])
    assert r.status_code == 200, r.text
    r = await execute(client, linkedin_proposal["id"])
    assert r.status_code == 200, r.text
    assert r.json()["execution"]["status"] == "SUCCEEDED"


# --- F. Weak/mismatch/unknown LinkedIn identity is blocked end-to-end,
# with no override anywhere. ---


@pytest.mark.parametrize(
    "state",
    [LinkedInIdentityState.WEAK_MATCH.value, LinkedInIdentityState.MISMATCH.value, LinkedInIdentityState.UNKNOWN.value],
)
async def test_weak_mismatch_unknown_linkedin_identity_blocked_no_override(client, session_factory, state):
    _run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    linkedin_draft = find_draft(aggregate, channel="linkedin")

    await _set_linkedin_identity_match_state(session_factory, prospect_id=northwind["id"], state=state)

    proposal = await propose(client, linkedin_draft["id"])
    assert proposal["policy_verdict"] == "BLOCKED", proposal
    assert "linkedin_identity_not_strong" in proposal["blocked_reasons"]

    # No override anywhere: a BLOCKED proposal cannot be approved.
    r = await approve(client, proposal["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "PROPOSAL_BLOCKED"
