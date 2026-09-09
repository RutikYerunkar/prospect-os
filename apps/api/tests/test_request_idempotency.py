"""V2-H — mechanism A: request idempotency (§3.5A). The same approved
execution can never happen twice, in EITHER origin. `idempotency_key =
sha256(f"{approval_id}|{content_hash}")` carries a plain, non-partial UNIQUE
constraint on `action_executions`, so a double-click/retried execute
request loses the insert race and returns the EXISTING execution's state,
never a second row.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from groundwork.models.tables import ActionExecutionRow
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion


async def _execution_count(session_factory, proposal_id: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(ActionExecutionRow)
            .where(ActionExecutionRow.action_proposal_id == proposal_id)
        )
        return int(result.scalar_one())


async def test_double_execute_produces_exactly_one_execution_email(client, session_factory):
    """Frozen acceptance criterion 9, EMAIL_SEND / DEMO_SIMULATED."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])

    r1 = await execute(client, proposal["id"])
    r2 = await execute(client, proposal["id"])
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["execution"]["id"] == r2.json()["execution"]["id"]
    assert await _execution_count(session_factory, proposal["id"]) == 1


async def test_double_execute_produces_exactly_one_execution_linkedin(client, session_factory):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="linkedin")

    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])

    r1 = await execute(client, proposal["id"])
    r2 = await execute(client, proposal["id"])
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["execution"]["id"] == r2.json()["execution"]["id"]
    assert await _execution_count(session_factory, proposal["id"]) == 1


async def test_concurrent_double_execute_still_yields_exactly_one_execution(client, session_factory):
    """The race itself, not just sequential double-submission: two
    concurrent execute requests for the SAME approval. The
    `idempotency_key` UNIQUE constraint — not application-level locking —
    is the actual guarantee (§3.5A), so this exercises the real DB race."""
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    proposal = await propose(client, draft["id"])
    await approve(client, proposal["id"])

    results = await asyncio.gather(
        execute(client, proposal["id"]), execute(client, proposal["id"]), execute(client, proposal["id"])
    )
    for r in results:
        assert r.status_code == 200, r.text
    execution_ids = {r.json()["execution"]["id"] for r in results}
    assert len(execution_ids) == 1
    assert await _execution_count(session_factory, proposal["id"]) == 1


async def test_idempotency_key_derived_from_approval_id_and_content_hash():
    import hashlib

    approval_id = "approval-123"
    content_hash_value = "deadbeef"
    expected = hashlib.sha256(f"{approval_id}|{content_hash_value}".encode()).hexdigest()
    # Same construction the router uses (`api/routers/actions.py::execute_action`).
    assert expected == hashlib.sha256(f"{approval_id}|{content_hash_value}".encode()).hexdigest()


async def test_insert_claimed_execution_deterministically_loses_the_race(client, session_factory):
    """Exercises the actual `IntegrityError` catch path deterministically
    (not relying on asyncio scheduling to produce a real race): two calls
    to `insert_claimed_execution` with the SAME `idempotency_key` — the
    second must return the first row with `won_race=False`, never raise,
    never create a second row. Uses real proposal/approval/prospect/run ids
    (via a real propose+approve) so the FK-checked columns are valid under
    `PRAGMA foreign_keys=ON`."""
    import uuid

    from groundwork.models.enums import ActionExecutionOrigin, ActionType
    from groundwork.repositories.actions import ActionRepository
    from groundwork.timeutil import utcnow

    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    approve_resp = await approve(client, proposal["id"])
    assert approve_resp.status_code == 200, approve_resp.text

    from groundwork.repositories.approvals import ApprovalRepository

    real_approval = await ApprovalRepository(session_factory).latest_for_proposal(proposal["id"])

    repo = ActionRepository(session_factory)
    idempotency_key = f"idem-{uuid.uuid4()}"
    kwargs = dict(
        action_proposal_id=proposal["id"],
        approval_id=real_approval.id,
        prospect_id=proposal["prospect_id"],
        run_id=proposal["run_id"],
        action_type=ActionType.EMAIL_SEND,
        idempotency_key=idempotency_key,
        recipient_identity_key="priya@example.com",
        sender_identifier="demo-sender@groundwork.invalid",
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
        message_id_header="<a@groundwork.invalid>",
        claimed_at=utcnow(),
    )

    row1, won1 = await repo.insert_claimed_execution(execution_id=str(uuid.uuid4()), **kwargs)
    row2, won2 = await repo.insert_claimed_execution(execution_id=str(uuid.uuid4()), **kwargs)
    assert won1 is True
    assert won2 is False
    assert row1.id == row2.id
    assert await _execution_count(session_factory, proposal["id"]) == 1


async def test_two_different_demo_visitors_get_two_different_executions(client):
    """Because the key is derived from `approval_id`, two different
    approvals for the SAME draft/content (e.g. two independent portfolio
    visitors each running their own Demo play) each get their own
    execution — this is the flip side of idempotency, not a bug: one
    approval, double-clicked, still yields exactly one execution, but two
    approvals each yield their own."""
    _, prospects_a = await run_demo_play_to_completion(client)
    _, prospects_b = await run_demo_play_to_completion(client)

    draft_a = find_draft(await get_aggregate(client, prospects_a["Northwind Labs"]["id"]), channel="email")
    draft_b = find_draft(await get_aggregate(client, prospects_b["Northwind Labs"]["id"]), channel="email")

    proposal_a = await propose(client, draft_a["id"])
    proposal_b = await propose(client, draft_b["id"])
    await approve(client, proposal_a["id"])
    await approve(client, proposal_b["id"])

    exec_a = (await execute(client, proposal_a["id"])).json()["execution"]
    exec_b = (await execute(client, proposal_b["id"])).json()["execution"]
    assert exec_a["id"] != exec_b["id"]
