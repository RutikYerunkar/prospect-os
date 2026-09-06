"""Shared helpers for the V2-H action-flow test suite — mirrors
`api_helpers.py`'s style (plain async functions, not fixtures)."""

from __future__ import annotations

from typing import Any

from tests.api_helpers import create_play, start_run, wait_for_terminal


async def run_demo_play_to_completion(client, *, target_count: int = 7) -> tuple[str, dict[str, dict[str, Any]]]:
    """Runs the canonical Demo play to completion and returns
    `(run_id, prospects_by_company_name)` — the latter keyed by
    `company_name` exactly as `ProspectSummary` reports it (e.g.
    `"Northwind Labs"`, `"Sable Compute"`)."""
    play = await create_play(client, target_count=target_count)
    run = await start_run(client, play["id"])
    await wait_for_terminal(client, run["run_id"])
    r = await client.get(f"/api/runs/{run['run_id']}/prospects")
    assert r.status_code == 200, r.text
    # The canonical fixture pack includes a deliberate DUPLICATE row sharing
    # "Northwind Labs" as its company_name (§23) — never let it shadow the
    # real PASS row in this lookup.
    prospects: dict[str, dict[str, Any]] = {}
    for p in r.json():
        if p["status"] == "DUPLICATE":
            continue
        prospects[p["company_name"]] = p
    return run["run_id"], prospects


async def get_aggregate(client, prospect_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/prospects/{prospect_id}")
    assert r.status_code == 200, r.text
    return r.json()


def find_draft(aggregate: dict[str, Any], *, channel: str) -> dict[str, Any]:
    for draft in aggregate["drafts"]:
        if draft["channel"] == channel:
            return draft
    raise AssertionError(f"no {channel!r} draft found for prospect {aggregate['id']!r}")


async def propose(client, draft_id: str) -> dict[str, Any]:
    r = await client.post("/api/actions/propose", json={"draft_id": draft_id})
    assert r.status_code == 201, r.text
    return r.json()


async def approve(client, proposal_id: str, *, actor: str = "demo_user"):
    return await client.post(f"/api/actions/proposals/{proposal_id}/approve", json={"actor": actor})


async def reject(client, proposal_id: str, *, reason: str = "not a fit", actor: str = "demo_user"):
    return await client.post(f"/api/actions/proposals/{proposal_id}/reject", json={"reason": reason, "actor": actor})


async def execute(client, proposal_id: str, *, actor: str = "demo_user"):
    return await client.post(f"/api/actions/proposals/{proposal_id}/execute", json={"actor": actor})


async def propose_approve(client, draft_id: str) -> dict[str, Any]:
    """Propose then approve in one call — the common setup for execute
    tests. Asserts the proposal is ELIGIBLE (fails loudly, with the
    blocked_reasons, if the fixture/prospect this draft belongs to isn't
    actually eligible — a clearer failure than a downstream 409)."""
    proposal = await propose(client, draft_id)
    assert proposal["policy_verdict"] == "ELIGIBLE", proposal
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    return proposal
