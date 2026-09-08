"""`action_proposals` / `action_executions` / `action_send_calls` /
`action_events` persistence (V2-H, §3.2/§3.5/§Part 5).

Mirrors the rest of this package's idioms: no ORM `relationship()` anywhere,
`PRAGMA foreign_keys=ON` in both `db.py` and `conftest.py`, so a row that
references an id created earlier in the SAME transaction needs the
`add -> flush() -> add -> commit` pattern `create_play_with_attempts`
established. `ActionProposalRow` is immutable once created (§3.2) — nothing
here ever mutates a proposal's own hash/sender/recipient/subject/body-derived
fields after insert; only `superseded_by` is set on an OLDER proposal when a
NEWER one is created for the same draft.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from groundwork.domain.action_policy import RecipientConflict
from groundwork.models.enums import ActionExecutionOrigin, ActionExecutionStatus, ActionType
from groundwork.models.schemas import ActionExecution, ActionProposal
from groundwork.models.tables import (
    ActionEventRow,
    ActionExecutionRow,
    ActionProposalRow,
    ActionSendCallRow,
    OutreachDraftRow,
)
from groundwork.observability.redact import redact

# §3.5B — exactly the statuses the partial unique index (and clause 12)
# treat as currently blocking a LIVE_EXTERNAL recipient identity. FAILED is
# deliberately excluded — it is the only state that frees the identity.
_BLOCKING_LIVE_STATUSES = (
    ActionExecutionStatus.CLAIMED.value,
    ActionExecutionStatus.IN_FLIGHT.value,
    ActionExecutionStatus.SUCCEEDED.value,
    ActionExecutionStatus.UNCERTAIN.value,
    ActionExecutionStatus.ABANDONED.value,
)

# Highest-severity-first, so a caller who (in theory) found more than one
# blocking row for the same recipient still gets a single, deterministic
# conflict classification. In practice the partial unique index makes more
# than one such row impossible; this ordering is a defensive tie-break only.
_STATUS_PRIORITY: list[tuple[str, RecipientConflict]] = [
    (ActionExecutionStatus.SUCCEEDED.value, RecipientConflict.SUCCEEDED),
    (ActionExecutionStatus.ABANDONED.value, RecipientConflict.ABANDONED),
    (ActionExecutionStatus.UNCERTAIN.value, RecipientConflict.UNCERTAIN),
    (ActionExecutionStatus.IN_FLIGHT.value, RecipientConflict.IN_FLIGHT),
    (ActionExecutionStatus.CLAIMED.value, RecipientConflict.CLAIMED),
]


class ActionRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    # --- drafts (read/update the two v2 columns only — never anything else) ---

    async def get_draft(self, draft_id: str) -> OutreachDraftRow | None:
        async with self._session_factory() as session:
            result = await session.execute(select(OutreachDraftRow).where(OutreachDraftRow.id == draft_id))
            return result.scalar_one_or_none()

    async def set_draft_content_hash(self, draft_id: str, *, content_hash: str, hash_version: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(OutreachDraftRow).where(OutreachDraftRow.id == draft_id))
            row = result.scalar_one_or_none()
            if row is None:
                return
            row.content_hash = content_hash
            row.hash_version = hash_version
            await session.commit()

    # --- proposals (immutable; idempotent on UNIQUE(draft_id, content_hash)) ---

    async def create_proposal(
        self,
        *,
        prospect_id: str,
        run_id: str,
        draft_id: str,
        action_type: ActionType,
        channel: str,
        sender_identifier: str | None,
        recipient_identifier: str | None,
        recipient_identity_key: str | None,
        content_hash: str,
        hash_version: str,
        policy_version: str,
        policy_verdict: str,
        blocked_reasons: list[str],
        policy_snapshot: dict[str, Any],
        origin: ActionExecutionOrigin,
    ) -> tuple[ActionProposalRow, bool]:
        """Returns `(row, created)` — `created=False` means an identical
        proposal (same `draft_id` + `content_hash`) already existed and was
        returned unchanged (D3's idempotent-on-demand creation), never a
        second row and never a mutation of the first."""
        async with self._session_factory() as session:
            existing = await session.execute(
                select(ActionProposalRow).where(
                    ActionProposalRow.draft_id == draft_id, ActionProposalRow.content_hash == content_hash
                )
            )
            found = existing.scalar_one_or_none()
            if found is not None:
                return found, False

            # Supersede any PRIOR proposal(s) for this same draft that carry
            # a DIFFERENT content hash and aren't already superseded — pure
            # bookkeeping for the audit trail (§3.2's APPROVED->SUPERSEDED
            # arc); never mutates their hash/sender/recipient fields, and
            # never affects an already-issued approval's own hash check.
            prior = await session.execute(
                select(ActionProposalRow).where(
                    ActionProposalRow.draft_id == draft_id,
                    ActionProposalRow.content_hash != content_hash,
                    ActionProposalRow.superseded_by.is_(None),
                )
            )
            prior_rows = list(prior.scalars())

            # Re-validated through the Pydantic schema — its model validator
            # enforces D9's `DEMO_SIMULATED` sender convention a SECOND time,
            # independent of the router's own construction of
            # `sender_identifier` (the "secrets are scrubbed twice, not
            # once" discipline `ContactEnrichmentRepository.record_success`
            # already established for the LinkedIn identifier grammar).
            proposal_model = ActionProposal(
                prospect_id=prospect_id,
                run_id=run_id,
                draft_id=draft_id,
                action_type=action_type.value,
                channel=channel,
                sender_identifier=sender_identifier,
                recipient_identifier=recipient_identifier,
                recipient_identity_key=recipient_identity_key,
                content_hash=content_hash,
                hash_version=hash_version,
                policy_version=policy_version,
                policy_verdict=policy_verdict,
                blocked_reasons=blocked_reasons,
                policy_snapshot=policy_snapshot,
                origin=origin,
                created_at=datetime.now(timezone.utc),
            )

            row = ActionProposalRow(
                id=str(uuid.uuid4()),
                prospect_id=proposal_model.prospect_id,
                run_id=proposal_model.run_id,
                draft_id=proposal_model.draft_id,
                action_type=proposal_model.action_type,
                channel=proposal_model.channel,
                sender_identifier=proposal_model.sender_identifier,
                recipient_identifier=proposal_model.recipient_identifier,
                recipient_identity_key=proposal_model.recipient_identity_key,
                content_hash=proposal_model.content_hash,
                hash_version=proposal_model.hash_version,
                policy_version=proposal_model.policy_version,
                policy_verdict=proposal_model.policy_verdict,
                blocked_reasons=proposal_model.blocked_reasons,
                policy_snapshot=proposal_model.policy_snapshot,
                origin=proposal_model.origin.value,
                created_at=proposal_model.created_at,
            )
            session.add(row)
            await session.flush()

            for p in prior_rows:
                p.superseded_by = row.id

            await session.commit()
            await session.refresh(row)
            return row, True

    async def get_proposal(self, proposal_id: str) -> ActionProposalRow | None:
        async with self._session_factory() as session:
            result = await session.execute(select(ActionProposalRow).where(ActionProposalRow.id == proposal_id))
            return result.scalar_one_or_none()

    async def list_proposals_for_prospect(self, prospect_id: str) -> list[ActionProposalRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ActionProposalRow)
                .where(ActionProposalRow.prospect_id == prospect_id)
                .order_by(ActionProposalRow.created_at.asc())
            )
            return list(result.scalars())

    # --- executions ---

    async def get_execution_by_idempotency_key(self, idempotency_key: str) -> ActionExecutionRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow).where(ActionExecutionRow.idempotency_key == idempotency_key)
            )
            return result.scalar_one_or_none()

    async def latest_execution_for_proposal(self, action_proposal_id: str) -> ActionExecutionRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow)
                .where(ActionExecutionRow.action_proposal_id == action_proposal_id)
                .order_by(ActionExecutionRow.claimed_at.desc())
            )
            return result.scalars().first()

    async def count_demo_executions_for_run(self, run_id: str) -> int:
        """Policy clause 14's `demo_action_cap_reached` — every
        `DEMO_SIMULATED` execution this run has ever recorded, regardless of
        action type or its own status (a cap on activity, not just on
        successful sends)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count())
                .select_from(ActionExecutionRow)
                .where(
                    ActionExecutionRow.run_id == run_id,
                    ActionExecutionRow.origin == ActionExecutionOrigin.DEMO_SIMULATED.value,
                )
            )
            return int(result.scalar_one())

    async def recipient_conflict(self, recipient_identity_key: str | None) -> RecipientConflict:
        """§3.5B / policy clause 12 — LIVE_EXTERNAL EMAIL_SEND rows ONLY.
        Never inspects a `DEMO_SIMULATED` row (a demo execution neither
        consumes nor is blocked by this rule — rev 4)."""
        if not recipient_identity_key:
            return RecipientConflict.NONE
        async with self._session_factory() as session:
            result = await session.execute(
                select(ActionExecutionRow.status).where(
                    ActionExecutionRow.action_type == ActionType.EMAIL_SEND.value,
                    ActionExecutionRow.origin == ActionExecutionOrigin.LIVE_EXTERNAL.value,
                    ActionExecutionRow.recipient_identity_key == recipient_identity_key,
                    ActionExecutionRow.status.in_(_BLOCKING_LIVE_STATUSES),
                )
            )
            statuses = {row[0] for row in result.all()}
        for status_value, conflict in _STATUS_PRIORITY:
            if status_value in statuses:
                return conflict
        return RecipientConflict.NONE

    async def insert_claimed_execution(
        self,
        *,
        execution_id: str,
        action_proposal_id: str,
        approval_id: str,
        prospect_id: str,
        run_id: str,
        action_type: ActionType,
        idempotency_key: str,
        recipient_identity_key: str | None,
        sender_identifier: str | None,
        origin: ActionExecutionOrigin,
        message_id_header: str | None,
        claimed_at: datetime,
    ) -> tuple[ActionExecutionRow, bool]:
        """Write-ahead durability (§3.5A): the row commits as `CLAIMED`,
        carrying our own generated Message-ID, BEFORE any provider call.
        Returns `(row, won_race)` — `won_race=False` means another request
        already holds this `idempotency_key` (an `IntegrityError` on the
        UNIQUE constraint, caught here) and the EXISTING row is returned;
        the caller must not attempt to send/settle again in that case."""
        # Re-validated through the Pydantic schema (same discipline as
        # `create_proposal` above) — trivially satisfied at CLAIM time since
        # `provider_message_id` isn't known yet; the meaningful check runs
        # again in `settle_execution_succeeded` once it is.
        execution_model = ActionExecution(
            action_proposal_id=action_proposal_id,
            approval_id=approval_id,
            prospect_id=prospect_id,
            run_id=run_id,
            action_type=action_type.value,
            provider=None,
            status=ActionExecutionStatus.CLAIMED.value,
            idempotency_key=idempotency_key,
            recipient_identity_key=recipient_identity_key,
            sender_identifier=sender_identifier,
            origin=origin,
            message_id_header=message_id_header,
            dispatched=False,
            claimed_at=claimed_at,
        )
        row = ActionExecutionRow(
            id=execution_id,
            action_proposal_id=execution_model.action_proposal_id,
            approval_id=execution_model.approval_id,
            prospect_id=execution_model.prospect_id,
            run_id=execution_model.run_id,
            action_type=execution_model.action_type,
            provider=execution_model.provider,
            status=execution_model.status,
            idempotency_key=execution_model.idempotency_key,
            recipient_identity_key=execution_model.recipient_identity_key,
            sender_identifier=execution_model.sender_identifier,
            origin=execution_model.origin.value,
            message_id_header=execution_model.message_id_header,
            dispatched=execution_model.dispatched,
            claimed_at=execution_model.claimed_at,
        )
        async with self._session_factory() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self.get_execution_by_idempotency_key(idempotency_key)
                assert existing is not None
                return existing, False
            await session.refresh(row)
            return row, True

    async def settle_execution_succeeded(
        self,
        execution_id: str,
        *,
        provider: str | None,
        dispatched: bool,
        outcome_class: str | None,
        provider_message_id: str | None,
        provider_thread_id: str | None = None,
        dispatched_at: datetime | None,
        settled_at: datetime,
    ) -> ActionExecutionRow:
        async with self._session_factory() as session:
            result = await session.execute(select(ActionExecutionRow).where(ActionExecutionRow.id == execution_id))
            row = result.scalar_one()

            # Re-validated through the Pydantic schema — THIS is the
            # meaningful moment for D9's `provider_message_id` convention:
            # a `DEMO_SIMULATED` execution's final message id must start
            # with `demo://`. Independent of `DemoEmailSendProvider.send()`
            # already constructing it that way (the "scrubbed twice"
            # discipline) — a bug in the provider would be caught here
            # rather than silently persisted.
            ActionExecution(
                action_proposal_id=row.action_proposal_id,
                approval_id=row.approval_id,
                prospect_id=row.prospect_id,
                run_id=row.run_id,
                action_type=row.action_type,
                provider=provider,
                status=ActionExecutionStatus.SUCCEEDED.value,
                idempotency_key=row.idempotency_key,
                recipient_identity_key=row.recipient_identity_key,
                sender_identifier=row.sender_identifier,
                origin=ActionExecutionOrigin(row.origin),
                message_id_header=row.message_id_header,
                provider_message_id=provider_message_id,
                provider_thread_id=provider_thread_id,
                dispatched=dispatched,
                outcome_class=outcome_class,
                claimed_at=row.claimed_at,
                dispatched_at=dispatched_at,
                settled_at=settled_at,
            )

            row.status = ActionExecutionStatus.SUCCEEDED.value
            row.provider = provider
            row.dispatched = dispatched
            row.outcome_class = outcome_class
            row.provider_message_id = provider_message_id
            row.provider_thread_id = provider_thread_id
            row.attempt_count = row.attempt_count + 1
            row.dispatched_at = dispatched_at
            row.settled_at = settled_at
            await session.commit()
            await session.refresh(row)
            return row

    async def insert_send_call(
        self,
        *,
        action_execution_id: str,
        call_group_id: str,
        provider: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        latency_ms: float = 0.0,
        operation: str = "send",
        attempt: int = 1,
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                ActionSendCallRow(
                    id=str(uuid.uuid4()),
                    action_execution_id=action_execution_id,
                    call_group_id=call_group_id,
                    attempt=attempt,
                    attempt_kind="initial",
                    operation=operation,
                    provider=provider,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                )
            )
            await session.commit()

    async def record_event(
        self,
        *,
        prospect_id: str,
        type: str,
        actor: str,
        action_proposal_id: str | None = None,
        action_execution_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append-only (§Part 5) — never updated. `payload` is redacted key-
        by-key here (never a raw provider/error blob) so this stays the same
        "redact before persistence" discipline as every other telemetry
        table in this codebase."""
        safe_payload: dict[str, Any] = {}
        for key, value in (payload or {}).items():
            safe_payload[key] = redact(value) if isinstance(value, str) else value
        async with self._session_factory() as session:
            session.add(
                ActionEventRow(
                    id=str(uuid.uuid4()),
                    action_execution_id=action_execution_id,
                    action_proposal_id=action_proposal_id,
                    prospect_id=prospect_id,
                    type=type,
                    actor=actor,
                    payload=safe_payload,
                    ts=datetime.now(timezone.utc),
                )
            )
            await session.commit()
