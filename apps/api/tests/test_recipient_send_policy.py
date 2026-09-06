"""V2-H — mechanism B: the LIVE-only recipient-level send policy (§3.5B).
Two different proposals cannot both send an initial real email to the same
human. Demo/Demo never blocks; a Demo execution never blocks (or is blocked
by) a later Live send to the same address; `FAILED` frees the recipient
identity; `UNCERTAIN`/`ABANDONED` continue to block it.

Exercised primarily at `ActionRepository.recipient_conflict()` — the pure
read `_evaluate_policy` calls — since V2-H never produces a real second
`LIVE_EXTERNAL` execution (Live sending is structurally disabled); the DB
partial-unique-index race itself is `models/tables.py`'s existing,
untouched V2-B mechanism, re-verified here at the repository layer against
real rows.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from groundwork.domain.action_policy import RecipientConflict
from groundwork.models.enums import ActionExecutionOrigin, ActionExecutionStatus, ActionType
from groundwork.models.tables import ActionExecutionRow
from tests.action_helpers import find_draft, get_aggregate, propose, run_demo_play_to_completion

RECIPIENT_KEY = "priya.natarajan@northwindlabs.com"


async def _insert_execution_row(
    session_factory,
    *,
    prospect_id: str,
    run_id: str,
    action_proposal_id: str,
    origin: ActionExecutionOrigin,
    status: ActionExecutionStatus,
    recipient_identity_key: str = RECIPIENT_KEY,
) -> None:
    async with session_factory() as session:
        session.add(
            ActionExecutionRow(
                id=str(uuid.uuid4()),
                action_proposal_id=action_proposal_id,
                approval_id=None,
                prospect_id=prospect_id,
                run_id=run_id,
                action_type=ActionType.EMAIL_SEND.value,
                provider="test",
                status=status.value,
                idempotency_key=str(uuid.uuid4()),
                recipient_identity_key=recipient_identity_key,
                sender_identifier="operator@example.com",
                origin=origin.value,
                dispatched=status is not ActionExecutionStatus.CLAIMED,
            )
        )
        await session.commit()


async def _real_proposal_ids(client):
    _, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, draft["id"])
    return proposal, northwind


class TestRecipientConflictRepositoryMethod:
    async def test_no_rows_is_none(self, client, session_factory):
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict("nobody@example.com") is RecipientConflict.NONE

    async def test_demo_row_never_produces_a_conflict(self, client, session_factory):
        """rev 4 — a `DEMO_SIMULATED` row is not a message to a human; it
        neither consumes nor reserves a live recipient identity, in either
        direction. `recipient_conflict()` only ever queries
        `origin=LIVE_EXTERNAL` rows (§3.5B) — a Demo row must never surface
        here regardless of its own status."""
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.DEMO_SIMULATED,
            status=ActionExecutionStatus.SUCCEEDED,
        )
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict(RECIPIENT_KEY) is RecipientConflict.NONE

    async def test_live_succeeded_row_blocks_with_already_sent(self, client, session_factory):
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.SUCCEEDED,
        )
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict(RECIPIENT_KEY) is RecipientConflict.SUCCEEDED

    async def test_live_uncertain_row_blocks(self, client, session_factory):
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.UNCERTAIN,
        )
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict(RECIPIENT_KEY) is RecipientConflict.UNCERTAIN

    async def test_live_abandoned_row_still_blocks(self, client, session_factory):
        """Marking an UNCERTAIN execution ABANDONED records an operator's
        judgement for the audit trail; it does not license a second
        attempt at that human."""
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.ABANDONED,
        )
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict(RECIPIENT_KEY) is RecipientConflict.ABANDONED

    async def test_live_failed_row_frees_the_identity(self, client, session_factory):
        """§3.4/§3.5B — FAILED is the ONLY state that frees a recipient
        identity, because it is defined as PROVABLY non-delivered."""
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.FAILED,
        )
        from groundwork.repositories.actions import ActionRepository

        repo = ActionRepository(session_factory)
        assert await repo.recipient_conflict(RECIPIENT_KEY) is RecipientConflict.NONE

    async def test_claimed_and_in_flight_both_block_as_send_in_flight(self, client, session_factory):
        from groundwork.repositories.actions import ActionRepository

        for status in (ActionExecutionStatus.CLAIMED, ActionExecutionStatus.IN_FLIGHT):
            proposal, northwind = await _real_proposal_ids(client)
            await _insert_execution_row(
                session_factory,
                prospect_id=northwind["id"],
                run_id=proposal["run_id"],
                action_proposal_id=proposal["id"],
                origin=ActionExecutionOrigin.LIVE_EXTERNAL,
                status=status,
                recipient_identity_key=f"{status.value.lower()}@example.com",
            )
            repo = ActionRepository(session_factory)
            conflict = await repo.recipient_conflict(f"{status.value.lower()}@example.com")
            assert conflict in (RecipientConflict.CLAIMED, RecipientConflict.IN_FLIGHT)


class TestPartialUniqueIndexDemoNeverBlocksDemo:
    async def test_two_demo_rows_same_recipient_both_insert_successfully(self, client, session_factory):
        """The partial unique index is predicated on
        `origin='LIVE_EXTERNAL'` — two `DEMO_SIMULATED` rows for the SAME
        recipient must coexist without any constraint violation (a second
        portfolio visitor simulating the same canonical Northwind send must
        never be blocked)."""
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.DEMO_SIMULATED,
            status=ActionExecutionStatus.SUCCEEDED,
        )
        # Second row, same recipient, same origin, same action_type,
        # blocking-equivalent status — must NOT raise IntegrityError.
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.DEMO_SIMULATED,
            status=ActionExecutionStatus.SUCCEEDED,
        )

    async def test_a_demo_execution_never_blocks_a_later_live_send_to_the_same_address(self, client, session_factory):
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.DEMO_SIMULATED,
            status=ActionExecutionStatus.SUCCEEDED,
        )
        # A LIVE row for the SAME address, inserted after the Demo one,
        # must succeed cleanly — the Demo row never reserved the identity.
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.SUCCEEDED,
        )

    async def test_two_live_rows_same_recipient_second_insert_conflicts(self, client, session_factory):
        """The database itself is the guarantee (§3.5B) — a second LIVE row
        for the SAME recipient identity, in a blocking status, is a real
        `IntegrityError` against the partial unique index, independent of
        whether application-level policy would have caught it first."""
        proposal, northwind = await _real_proposal_ids(client)
        await _insert_execution_row(
            session_factory,
            prospect_id=northwind["id"],
            run_id=proposal["run_id"],
            action_proposal_id=proposal["id"],
            origin=ActionExecutionOrigin.LIVE_EXTERNAL,
            status=ActionExecutionStatus.SUCCEEDED,
        )
        try:
            await _insert_execution_row(
                session_factory,
                prospect_id=northwind["id"],
                run_id=proposal["run_id"],
                action_proposal_id=proposal["id"],
                origin=ActionExecutionOrigin.LIVE_EXTERNAL,
                status=ActionExecutionStatus.SUCCEEDED,
            )
            raised = False
        except IntegrityError:
            raised = True
        assert raised, "the partial unique index must reject a second blocking LIVE_EXTERNAL row for one recipient"
