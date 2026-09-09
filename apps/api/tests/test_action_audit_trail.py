"""V2-I-b Phase 10 — `GET /api/actions/proposals/{id}/audit`: the immutable
audit trail. Redaction and no-raw-payload/token/MIME assertions."""

from __future__ import annotations

import httpx

from groundwork.main import app
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion
from tests.api_helpers import login_as_operator


async def test_audit_shows_proposal_approval_execution_and_events(client):
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])
    exec_resp = await execute(client, proposal["id"])
    assert exec_resp.status_code == 200

    resp = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["id"] == proposal["id"]
    assert body["proposal"]["approval"]["state"] == "APPROVED"
    assert body["proposal"]["execution"]["status"] == "SUCCEEDED"
    assert len(body["events"]) > 0
    event_types = {e["type"] for e in body["events"]}
    assert "proposal_created" in event_types
    assert "proposal_approved" in event_types
    assert "execution_succeeded" in event_types
    assert len(body["send_calls"]) > 0


async def test_audit_events_are_immutable_append_only(client):
    """Two audits of the same proposal (before/after an execution) never
    lose an earlier event — proving nothing is ever updated in place."""
    run_id, prospects = await run_demo_play_to_completion(client)
    sable = prospects["Sable Compute"]
    aggregate = await get_aggregate(client, sable["id"])
    draft = find_draft(aggregate, channel="linkedin")
    proposal = await propose(client, draft["id"])

    resp1 = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    n_before = len(resp1.json()["events"])

    await approve(client, proposal["id"])
    resp2 = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    events2 = resp2.json()["events"]
    assert len(events2) > n_before
    # All events from the first read are still present in the second.
    ids1 = {e["id"] for e in resp1.json()["events"]}
    ids2 = {e["id"] for e in events2}
    assert ids1 <= ids2


async def test_audit_never_exposes_raw_gmail_payload_token_or_mime(client):
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])
    await execute(client, proposal["id"])

    resp = await client.get(f"/api/actions/proposals/{proposal['id']}/audit")
    raw_text = resp.text
    forbidden_substrings = ["refresh_token", "access_token", "raw\":", "Content-Transfer-Encoding", "Bearer "]
    for token in forbidden_substrings:
        assert token not in raw_text, f"audit response leaked {token!r}"


async def test_audit_requires_operator_for_live_proposal(client, session_factory, monkeypatch):
    from sqlalchemy import update

    from groundwork.models.tables import RunRow
    from groundwork.repositories.gmail_connection import GmailConnectionRepository
    from groundwork.timeutil import utcnow

    await login_as_operator(client, monkeypatch)
    run_id, prospects = await run_demo_play_to_completion(client)
    async with session_factory() as session:
        await session.execute(update(RunRow).where(RunRow.id == run_id).values(mode="live"))
        await session.commit()
    repo = GmailConnectionRepository(session_factory)
    now = utcnow()
    await repo.upsert_connection(
        google_account_email="operator@example.com",
        encrypted_refresh_token="placeholder",
        key_version=1,
        scopes=["gmail.send"],
        connected_at=now,
        connected_by_actor="test",
        last_refreshed_at=now,
    )
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
    ) as anon_client:
        resp = await anon_client.get(f"/api/actions/proposals/{proposal['id']}/audit")
        assert resp.status_code == 401


async def test_audit_no_resend_endpoint_exists():
    """Structural proof: no resend route exists anywhere in the actions
    router (D7/D10) — never merely a missing button in the UI."""
    from groundwork.api.routers import actions as actions_router

    paths = {route.path for route in actions_router.router.routes}
    for path in paths:
        assert "resend" not in path.lower()
