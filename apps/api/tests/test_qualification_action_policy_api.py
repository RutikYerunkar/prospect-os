"""v2.0.3 — a NOT_QUALIFIED prospect is blocked from the action pipeline
end-to-end through the real API: propose is created but BLOCKED
(`prospect_not_actionable`), approve is refused (`PROPOSAL_BLOCKED`, no
override), and execute is never reachable at all — so zero send-provider
calls happen anywhere along the path. Mirrors the proof style
`test_linkedin_action_path.py` already established for a different
non-actionable path.
"""

from __future__ import annotations

from groundwork.api.routers import actions as actions_module
from tests.action_helpers import approve, find_draft, get_aggregate, propose
from tests.api_helpers import DEMO_ICP_OVERRIDES, create_play, start_run, wait_for_terminal


def _forbidden_resolve_send_provider(mode):
    raise AssertionError("resolve_send_provider must never be called for a BLOCKED, never-approved proposal")


async def _forbidden_build_gmail_send_provider(gmail, oauth_runtime):
    raise AssertionError("build_gmail_send_provider must never be called for a BLOCKED, never-approved proposal")


async def _run_with_raised_min_score(client, *, min_score: int) -> tuple[str, dict]:
    overrides = {**DEMO_ICP_OVERRIDES, "min_score": min_score}
    play = await create_play(client, icp_overrides=overrides)
    run = await start_run(client, play["id"])
    await wait_for_terminal(client, run["run_id"])
    r = await client.get(f"/api/runs/{run['run_id']}/prospects")
    assert r.status_code == 200, r.text
    northwind = next(p for p in r.json() if p["company_name"] == "Northwind Labs" and p["status"] != "DUPLICATE")
    return run["run_id"], northwind


async def test_not_qualified_prospect_blocks_propose_approve_and_never_reaches_send(client):
    _, northwind = await _run_with_raised_min_score(client, min_score=95)  # Northwind scores 92
    assert northwind["status"] == "NOT_QUALIFIED"

    aggregate = await get_aggregate(client, northwind["id"])
    email_draft = find_draft(aggregate, channel="email")

    # propose still creates the row (audit trail), but BLOCKED — never
    # ELIGIBLE, with the same `prospect_not_actionable` reason every other
    # non-actionable status produces (§action safety, no manual override).
    proposal = await propose(client, email_draft["id"])
    assert proposal["policy_verdict"] == "BLOCKED", proposal
    assert "prospect_not_actionable" in proposal["blocked_reasons"]

    originals = (actions_module.resolve_send_provider, actions_module.build_gmail_send_provider)
    actions_module.resolve_send_provider = _forbidden_resolve_send_provider
    actions_module.build_gmail_send_provider = _forbidden_build_gmail_send_provider
    try:
        # D7 — no override: approve is refused server-side, not just hidden
        # in the UI. Never reaches send-provider resolution.
        r = await approve(client, proposal["id"])
        assert r.status_code == 409, r.text
        assert r.json()["code"] == "PROPOSAL_BLOCKED"

        # execute requires an APPROVED approval first, which is now
        # structurally unreachable — proving zero send-provider calls
        # across the whole propose -> approve -> execute path.
        r2 = await client.post(f"/api/actions/proposals/{proposal['id']}/execute", json={"actor": "demo_user"})
        assert r2.status_code >= 400
    finally:
        actions_module.resolve_send_provider, actions_module.build_gmail_send_provider = originals


async def test_not_qualified_review_verdict_is_still_pass(client):
    """The review verdict itself must be untouched — a NOT_QUALIFIED
    prospect's own review panel still shows PASS, all seven checks; only
    the final status reflects the score floor."""
    _, northwind = await _run_with_raised_min_score(client, min_score=95)
    aggregate = await get_aggregate(client, northwind["id"])
    assert aggregate["status"] == "NOT_QUALIFIED"
    assert aggregate["review"]["verdict"] == "PASS"
    assert len(aggregate["review"]["checks"]) == 7
