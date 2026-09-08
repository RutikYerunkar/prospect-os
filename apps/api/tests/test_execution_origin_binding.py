"""V2-H — origin is derived server-side from run mode, and is never
accepted from request JSON, at any of the three action endpoints that
accept a body (`propose`/`approve`/`reject`; `execute` too). `extra="forbid"`
on `ActionProposeRequest` makes smuggling `origin` a 422, not a silently
ignored field — the strongest available proof short of the field not
existing on the model at all (which is also true here)."""

from __future__ import annotations

from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion


async def test_propose_request_schema_has_no_origin_field():
    from groundwork.api.schemas import ActionProposeRequest

    assert "origin" not in ActionProposeRequest.model_fields


async def test_propose_rejects_unknown_origin_field_with_422(client):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    resp = await client.post(
        "/api/actions/propose", json={"draft_id": draft["id"], "origin": "LIVE_EXTERNAL"}
    )
    assert resp.status_code == 422, resp.text


async def test_demo_run_proposal_origin_is_always_demo_simulated_regardless_of_body(client):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    assert proposal["origin"] == "DEMO_SIMULATED"


async def test_execution_origin_matches_proposal_origin_not_request_body(client):
    """`ActionExecuteRequest` carries no origin field either — the executed
    row's `origin` is always copied from the immutable proposal, which was
    itself bound at propose time from the run's own mode."""
    from groundwork.api.schemas import ActionExecuteRequest

    assert "origin" not in ActionExecuteRequest.model_fields

    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])
    resp = await execute(client, proposal["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == "DEMO_SIMULATED"
    assert body["execution"]["origin"] == "DEMO_SIMULATED"
