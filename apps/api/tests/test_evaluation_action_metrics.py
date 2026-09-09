"""V2-J §2/§3 — `evaluation.actions` governance metrics: proposal-time vs
execution-time blocked reasons kept separate, the five pre-policy
`execution_blocked` instrumentation paths (with the original 409 error
contract unchanged), executions_by_status/origin, approval-to-execution
latency, reconciliation outcomes, the empty state, and the full cross-run
recipient status matrix (§3) driven off `_BLOCKING_LIVE_STATUSES` via
`ActionRepository.cross_run_blocking_runs` — never a second, hardcoded
blocking-status list.
"""

from __future__ import annotations

import uuid

import pytest

from groundwork.engine.runner import Repos
from groundwork.models.enums import ActionExecutionOrigin, ActionExecutionStatus, ActionType
from groundwork.models.tables import ActionExecutionRow, ActionProposalRow
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.repositories.plays import PlayRepository
from groundwork.evaluation.metrics import compute_run_evaluation
from tests.action_helpers import approve, execute, find_draft, get_aggregate, propose, run_demo_play_to_completion

RECIPIENT_KEY = "priya.natarajan@northwindlabs.com"


async def _evaluate(session_factory, run_id: str) -> dict:
    repos = Repos.build(session_factory)
    return await compute_run_evaluation(
        run_id, repos, actions=ActionRepository(session_factory), approvals=ApprovalRepository(session_factory)
    )


async def test_action_empty_state(session_factory):
    """No governed action has been proposed for this run yet — every
    counter is zero/empty/None, never an error."""
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="empty", objective_text="t", icp_spec={}, mode="demo")
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["proposals_by_verdict"] == {}
    assert a["blocked_reasons"] == {}
    assert a["execution_blocked_reasons"] == {}
    assert a["execution_blocked_attempts"] == 0
    assert a["execution_blocked_proposals"] == 0
    assert a["content_hash_mismatch_count"] == 0
    assert a["approval_to_execution_latency_p50_ms"] is None
    assert a["approval_to_execution_latency_p95_ms"] is None
    assert a["executions_by_status"] == {}
    assert a["executions_by_origin"] == {}
    assert a["uncertain_count"] == 0
    assert a["reconciliation_outcomes"] == {}
    assert a["mean_messages_scanned_per_reconcile"] is None
    assert a["cross_run_recipient_blocks"] == 0
    assert a["cross_run_recipient_blocked_proposals"] == 0


async def test_proposals_by_verdict_and_blocked_reasons(client, session_factory):
    run_id, prospects = await run_demo_play_to_completion(client)
    sable = prospects["Sable Compute"]
    aggregate = await get_aggregate(client, sable["id"])
    email_draft = find_draft(aggregate, channel="email")
    linkedin_draft = find_draft(aggregate, channel="linkedin")

    email_proposal = await propose(client, email_draft["id"])
    linkedin_proposal = await propose(client, linkedin_draft["id"])
    assert email_proposal["policy_verdict"] == "BLOCKED"
    assert linkedin_proposal["policy_verdict"] == "ELIGIBLE"

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["proposals_by_verdict"].get("BLOCKED") == 1
    assert a["proposals_by_verdict"].get("ELIGIBLE") == 1
    assert a["blocked_reasons"].get("email_not_verified") == 1
    # Proposal-time blocks must never leak into the execution-time counter.
    assert a["execution_blocked_reasons"] == {}


async def test_execute_without_approval_is_execution_blocked_not_approved(client, session_factory):
    """One of the five pre-policy 409 paths — the original error contract
    (status 409, code NOT_APPROVED) is unchanged, and it now ALSO shows up
    in the execution-time metric."""
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="linkedin")
    proposal = await propose(client, draft["id"])
    assert proposal["policy_verdict"] == "ELIGIBLE"

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "NOT_APPROVED"

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["execution_blocked_reasons"].get("not_approved") == 1
    assert a["execution_blocked_attempts"] == 1
    assert a["execution_blocked_proposals"] == 1
    # Never counted as a proposal-time block — the proposal itself was ELIGIBLE.
    assert a["blocked_reasons"] == {}


async def test_repeated_blocked_attempts_on_one_proposal_count_per_attempt(client, session_factory):
    """Repeated-attempt grain (pinned): execution_blocked_attempts counts
    every blocked attempt; execution_blocked_proposals counts distinct
    proposals — a caller retrying the same still-unapproved proposal
    inflates the former, never the latter."""
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="linkedin")
    proposal = await propose(client, draft["id"])
    assert proposal["policy_verdict"] == "ELIGIBLE"

    for _ in range(3):
        resp = await execute(client, proposal["id"])
        assert resp.status_code == 409
        assert resp.json()["code"] == "NOT_APPROVED"

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["execution_blocked_attempts"] == 3
    assert a["execution_blocked_proposals"] == 1


async def test_content_changed_after_approval_is_execution_blocked_and_hash_mismatch(client, session_factory):
    from sqlalchemy import select

    from groundwork.models.tables import OutreachDraftRow

    run_id, prospects = await run_demo_play_to_completion(client)
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
        row.body = row.body + "\n\nedited after approval"
        await session.commit()

    resp = await execute(client, proposal["id"])
    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == "CONTENT_CHANGED"

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["execution_blocked_reasons"].get("content_changed") == 1
    assert a["content_hash_mismatch_count"] == 1
    assert a["execution_blocked_attempts"] == 1


async def test_successful_demo_execution_metrics(client, session_factory):
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="linkedin")
    proposal = await propose(client, draft["id"])
    assert proposal["policy_verdict"] == "ELIGIBLE"
    r = await approve(client, proposal["id"])
    assert r.status_code == 200, r.text
    r = await execute(client, proposal["id"])
    assert r.status_code == 200, r.text
    assert r.json()["execution"]["status"] == "SUCCEEDED"

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["executions_by_status"] == {"SUCCEEDED": 1}
    assert a["executions_by_origin"] == {"DEMO_SIMULATED": 1}
    assert a["uncertain_count"] == 0
    # Approval -> execution latency is a real, non-negative duration.
    assert a["approval_to_execution_latency_p50_ms"] is not None
    assert a["approval_to_execution_latency_p50_ms"] >= 0.0


async def _insert_execution_row(
    session_factory, *, prospect_id: str, run_id: str, action_proposal_id: str,
    origin: ActionExecutionOrigin, status: ActionExecutionStatus, recipient_identity_key: str = RECIPIENT_KEY,
) -> None:
    async with session_factory() as session:
        session.add(
            ActionExecutionRow(
                id=str(uuid.uuid4()), action_proposal_id=action_proposal_id, approval_id=None,
                prospect_id=prospect_id, run_id=run_id, action_type=ActionType.EMAIL_SEND.value,
                provider="test", status=status.value, idempotency_key=str(uuid.uuid4()),
                recipient_identity_key=recipient_identity_key, sender_identifier="operator@example.com",
                origin=origin.value, dispatched=status is not ActionExecutionStatus.CLAIMED,
            )
        )
        await session.commit()


async def _insert_blocked_proposal(
    session_factory, *, prospect_id: str, run_id: str, draft_id: str, recipient_identity_key: str,
    blocked_reason: str,
) -> str:
    proposal_id = str(uuid.uuid4())
    async with session_factory() as session:
        session.add(
            ActionProposalRow(
                id=proposal_id, prospect_id=prospect_id, run_id=run_id, draft_id=draft_id,
                action_type=ActionType.EMAIL_SEND.value, channel="email",
                sender_identifier="operator@example.com", recipient_identifier=recipient_identity_key,
                recipient_identity_key=recipient_identity_key, content_hash=str(uuid.uuid4()), hash_version="v1",
                policy_version="v1", policy_verdict="BLOCKED", blocked_reasons=[blocked_reason],
                policy_snapshot={}, origin=ActionExecutionOrigin.LIVE_EXTERNAL.value,
            )
        )
        await session.commit()
    return proposal_id


@pytest.mark.parametrize(
    "status,blocks",
    [
        (ActionExecutionStatus.SUCCEEDED, True),
        (ActionExecutionStatus.ABANDONED, True),
        (ActionExecutionStatus.UNCERTAIN, True),
        (ActionExecutionStatus.IN_FLIGHT, True),
        (ActionExecutionStatus.CLAIMED, True),
        (ActionExecutionStatus.FAILED, False),
    ],
)
async def test_cross_run_recipient_status_matrix(client, session_factory, status, blocks):
    """§3 — a BLOCKING status on a DIFFERENT run's LIVE_EXTERNAL EMAIL_SEND
    execution for the same recipient identity is a cross-run block; FAILED
    is the one status that frees the identity and must never count."""
    run_id_a, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    # A second, independent run holds the (possibly) blocking execution.
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    other_play_id = await plays.create(name="other", objective_text="t", icp_spec={}, mode="live")
    run_id_b = await repos.runs.create(play_id=other_play_id, mode="live", seed=2)

    blocking_proposal_id = await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id_b, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason="already_sent_to_recipient",
    )
    await _insert_execution_row(
        session_factory, prospect_id=northwind["id"], run_id=run_id_b,
        action_proposal_id=blocking_proposal_id, origin=ActionExecutionOrigin.LIVE_EXTERNAL, status=status,
    )

    # This run's own proposal — BLOCKED by the recipient-conflict reason,
    # as the real policy would compute it.
    reason = "already_sent_to_recipient" if status in (
        ActionExecutionStatus.SUCCEEDED, ActionExecutionStatus.ABANDONED
    ) else "send_in_flight" if status in (
        ActionExecutionStatus.IN_FLIGHT, ActionExecutionStatus.CLAIMED
    ) else "prior_send_uncertain"
    await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id_a, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason=reason,
    )

    evaluation = await _evaluate(session_factory, run_id_a)
    a = evaluation["actions"]
    if blocks:
        assert a["cross_run_recipient_blocks"] == 1
        assert a["cross_run_recipient_blocked_proposals"] == 1
    else:
        assert a["cross_run_recipient_blocks"] == 0
        assert a["cross_run_recipient_blocked_proposals"] == 0


async def test_demo_simulated_never_counts_cross_run(client, session_factory):
    run_id_a, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    other_play_id = await plays.create(name="other", objective_text="t", icp_spec={}, mode="demo")
    run_id_b = await repos.runs.create(play_id=other_play_id, mode="demo", seed=2)

    blocking_proposal_id = await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id_b, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason="already_sent_to_recipient",
    )
    await _insert_execution_row(
        session_factory, prospect_id=northwind["id"], run_id=run_id_b,
        action_proposal_id=blocking_proposal_id, origin=ActionExecutionOrigin.DEMO_SIMULATED,
        status=ActionExecutionStatus.SUCCEEDED,
    )
    await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id_a, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason="already_sent_to_recipient",
    )

    evaluation = await _evaluate(session_factory, run_id_a)
    a = evaluation["actions"]
    assert a["cross_run_recipient_blocks"] == 0
    assert a["cross_run_recipient_blocked_proposals"] == 0


async def test_same_run_blocking_execution_is_not_cross_run(client, session_factory):
    run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")

    blocking_proposal_id = await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason="already_sent_to_recipient",
    )
    await _insert_execution_row(
        session_factory, prospect_id=northwind["id"], run_id=run_id,
        action_proposal_id=blocking_proposal_id, origin=ActionExecutionOrigin.LIVE_EXTERNAL,
        status=ActionExecutionStatus.SUCCEEDED,
    )
    await _insert_blocked_proposal(
        session_factory, prospect_id=northwind["id"], run_id=run_id, draft_id=draft["id"],
        recipient_identity_key=RECIPIENT_KEY, blocked_reason="already_sent_to_recipient",
    )

    evaluation = await _evaluate(session_factory, run_id)
    a = evaluation["actions"]
    assert a["cross_run_recipient_blocks"] == 0
    assert a["cross_run_recipient_blocked_proposals"] == 0
