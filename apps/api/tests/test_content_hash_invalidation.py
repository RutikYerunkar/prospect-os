"""V2-H — content-hash invalidation at the API/integration level (distinct
from `test_content_hash.py`'s pure-function unit tests): editing a draft
after approval voids it (`409 CONTENT_CHANGED`), and a proposal is
idempotent on `UNIQUE(draft_id, content_hash)` — re-proposing an unchanged
draft returns the SAME proposal, never a duplicate.
"""

from __future__ import annotations

from sqlalchemy import select

from groundwork.models.tables import OutreachDraftRow
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion


async def test_editing_draft_after_approval_yields_409_content_changed(client, session_factory):
    """Frozen acceptance criterion 2, verbatim."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    assert proposal["policy_verdict"] == "ELIGIBLE"
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text

    async with session_factory() as session:
        result = await session.execute(select(OutreachDraftRow).where(OutreachDraftRow.id == draft["id"]))
        row = result.scalar_one()
        row.body = row.body + "\n\nP.S. this line was added after approval."
        await session.commit()

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "CONTENT_CHANGED"


async def test_reproposing_unchanged_draft_is_idempotent_same_proposal(client):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    first = await propose(client, draft["id"])
    second = await propose(client, draft["id"])
    assert first["id"] == second["id"]
    assert first["content_hash"] == second["content_hash"]
    assert second["created"] is False
    assert first["created"] is True


async def test_reproposing_after_edit_creates_a_new_proposal_and_supersedes_the_old(client, session_factory):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    first = await propose(client, draft["id"])

    async with session_factory() as session:
        result = await session.execute(select(OutreachDraftRow).where(OutreachDraftRow.id == draft["id"]))
        row = result.scalar_one()
        row.body = row.body + "\n\nA materially different edit."
        await session.commit()

    second = await propose(client, draft["id"])
    assert second["id"] != first["id"]
    assert second["content_hash"] != first["content_hash"]

    r = await client.get(f"/api/actions/proposals/{first['id']}")
    assert r.status_code == 200
    assert r.json()["superseded_by"] == second["id"]


async def test_draft_content_hash_and_hash_version_populated_on_proposal_creation(client):
    """Part 5/D3 — `outreach_drafts.content_hash`/`hash_version` are NULL
    until a proposal is created, then populated as frozen for V2-H."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate_before = await get_aggregate(client, northwind["id"])
    draft_before = find_draft(aggregate_before, channel="email")
    assert draft_before["content_hash"] is None

    proposal = await propose(client, draft_before["id"])

    aggregate_after = await get_aggregate(client, northwind["id"])
    draft_after = find_draft(aggregate_after, channel="email")
    assert draft_after["content_hash"] == proposal["content_hash"]
    assert draft_after["hash_version"] == proposal["hash_version"]


class TestMismatchLinkedInBlockedNoOverride:
    """Frozen acceptance criterion 3: a MISMATCH LinkedIn profile is
    blocked with a visible reason and NO override affordance anywhere.
    Built via direct DB manipulation of `contact_channels` (a dedicated
    test fixture, never `demo_pack.yaml` itself — the canonical demo board
    stays byte-identical)."""

    async def test_mismatch_linkedin_proposal_is_blocked_with_visible_reason(self, client, session_factory):
        from sqlalchemy import update

        from groundwork.models.tables import ContactChannelRow

        _, prospects = await run_demo_play_to_completion(client)
        northwind = prospects["Northwind Labs"]

        async with session_factory() as session:
            await session.execute(
                update(ContactChannelRow)
                .where(ContactChannelRow.prospect_id == northwind["id"], ContactChannelRow.channel == "linkedin")
                .values(identity_match_state="MISMATCH")
            )
            await session.commit()

        aggregate = await get_aggregate(client, northwind["id"])
        draft = find_draft(aggregate, channel="linkedin")
        proposal = await propose(client, draft["id"])

        assert proposal["policy_verdict"] == "BLOCKED"
        assert "linkedin_identity_not_strong" in proposal["blocked_reasons"]

    async def test_mismatch_linkedin_cannot_be_approved_no_override_endpoint_exists(self, client, session_factory):
        from sqlalchemy import update

        from groundwork.models.tables import ContactChannelRow

        _, prospects = await run_demo_play_to_completion(client)
        northwind = prospects["Northwind Labs"]

        async with session_factory() as session:
            await session.execute(
                update(ContactChannelRow)
                .where(ContactChannelRow.prospect_id == northwind["id"], ContactChannelRow.channel == "linkedin")
                .values(identity_match_state="MISMATCH")
            )
            await session.commit()

        aggregate = await get_aggregate(client, northwind["id"])
        draft = find_draft(aggregate, channel="linkedin")
        proposal = await propose(client, draft["id"])

        # D7 — no override anywhere: approving a BLOCKED proposal is
        # rejected server-side, and there is no query parameter, header, or
        # alternate endpoint that grants one. The ONLY write endpoints this
        # router exposes are propose/approve/reject/execute (see
        # `api/routers/actions.py`'s route table) — none of them accepts a
        # force/override argument at all (`ActionApproveRequest`/
        # `ActionExecuteRequest` both have no such field, and extra fields
        # are not silently accepted anywhere else in this schema family).
        r = await approve(client, proposal["id"])
        assert r.status_code == 409
        assert r.json()["code"] == "PROPOSAL_BLOCKED"

        # Attempting to force it through a forged extra field is a no-op —
        # FastAPI/Pydantic ignore unknown fields on this model rather than
        # erroring, but the value is never read anywhere in the handler.
        r2 = await client.post(
            f"/api/actions/proposals/{proposal['id']}/approve",
            json={"actor": "demo_user", "override": True, "force": True},
        )
        assert r2.status_code == 409
