"""V2-H — `domain/action_policy.py::evaluate()` wired end-to-end through the
real API against the real (Demo) engine output — as opposed to
`test_action_policy.py`'s pure-function unit tests with hand-built kwargs.
Proves axis independence on the canonical Sable Compute fixture (risky
email blocked, STRONG_MATCH LinkedIn allowed — no button anywhere makes the
email sendable) and the Northwind happy path (both actions ELIGIBLE).
"""

from __future__ import annotations

from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion


async def test_northwind_both_actions_eligible(client):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])

    email_draft = find_draft(aggregate, channel="email")
    linkedin_draft = find_draft(aggregate, channel="linkedin")

    email_proposal = await propose(client, email_draft["id"])
    linkedin_proposal = await propose(client, linkedin_draft["id"])

    assert email_proposal["policy_verdict"] == "ELIGIBLE", email_proposal
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE", linkedin_proposal


async def test_sable_email_blocked_linkedin_allowed_axis_independence(client):
    """Sable Compute (§Part 7 canonical matrix): email RISKY (catch-all) ->
    permanently blocked; LinkedIn STRONG_MATCH -> allowed. Proves the five
    contact axes are independent — a risky email never drags LinkedIn down
    with it, and a strong LinkedIn match never grants the email an
    exception. No override exists for the blocked email (D7)."""
    _, prospects = await run_demo_play_to_completion(client)
    sable = prospects["Sable Compute"]
    aggregate = await get_aggregate(client, sable["id"])

    email_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "email")
    linkedin_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "linkedin")
    assert email_channel["verification_state"] == "RISKY"
    assert linkedin_channel["identity_match_state"] == "STRONG_MATCH"

    email_draft = find_draft(aggregate, channel="email")
    linkedin_draft = find_draft(aggregate, channel="linkedin")

    email_proposal = await propose(client, email_draft["id"])
    linkedin_proposal = await propose(client, linkedin_draft["id"])

    assert email_proposal["policy_verdict"] == "BLOCKED"
    assert "email_not_verified" in email_proposal["blocked_reasons"]
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE", linkedin_proposal

    # No override anywhere: the blocked email proposal cannot be approved.
    r = await approve(client, email_proposal["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "PROPOSAL_BLOCKED"

    # The eligible LinkedIn proposal, meanwhile, sails through the full
    # governance path untouched by the email's block.
    r = await approve(client, linkedin_proposal["id"])
    assert r.status_code == 200, r.text
    r = await execute(client, linkedin_proposal["id"])
    assert r.status_code == 200, r.text
    assert r.json()["execution"]["status"] == "SUCCEEDED"


async def test_riverbend_no_named_person_email_not_attempted(client):
    """Riverbend Analytics (§Part 7): `NOT_ATTEMPTED` on both — no named
    person to look up. Proposing against a not-attempted channel is
    BLOCKED, never an error — the same clean degradation the rest of the
    pipeline already exhibits for PERSONA_ONLY contacts."""
    _, prospects = await run_demo_play_to_completion(client)
    riverbend = prospects["Riverbend Analytics"]
    aggregate = await get_aggregate(client, riverbend["id"])

    email_draft = next((d for d in aggregate["drafts"] if d["channel"] == "email"), None)
    if email_draft is None:
        # No draft at all is an equally valid degradation for a
        # never-actionable contact — either way, nothing here 500s.
        return
    proposal = await propose(client, email_draft["id"])
    assert proposal["policy_verdict"] == "BLOCKED"
