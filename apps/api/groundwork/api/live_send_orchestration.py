"""Live `EMAIL_SEND` dispatch orchestration (V2-I-b, Phase 7).

Deliberately NOT called from `api/routers/actions.py::execute_action` until
the refusal-removal gate (see `docs/PROGRESS.md`) — `resolve_send_provider
(Mode.LIVE)` still unconditionally raises `LiveExternalEmailSendDisabled`
before this function is ever reached. Built and independently testable now
so the removal step itself is a small, reviewable diff (swap the raise for
one call to `dispatch_live_email_send`), not a rewrite.

Dispatch ordering (load-bearing for crash recovery — do not reorder):

1. `CLAIMED` committed (write-ahead, carrying our own generated Message-ID).
2. Allowance reservation committed (the rolling-24h guard, Phase 5).
3. Guarded `IN_FLIGHT` update, `dispatched_at` set, committed.
4. ONLY THEN the external Gmail HTTP call.
5. Classify (§3.4) and settle.

If step 3's guarded update doesn't affect exactly one row, dispatch is
skipped entirely — a process that crashed between CLAIMED and here is
recovered by the stale-claim sweep (Phase 9), never re-dispatched here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from groundwork.domain.message_id import generate_message_id_header
from groundwork.domain.send_classifier import execution_status_for_outcome
from groundwork.models.enums import ActionExecutionOrigin, ActionExecutionStatus, ActionType, SendOutcome
from groundwork.models.tables import ActionExecutionRow, ActionProposalRow, ApprovalRow, OutreachDraftRow
from groundwork.providers.send_base import EmailSendProvider, OutboundEmailMessage
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.live_send_allowance import AllowanceReservationOutcome, LiveSendAllowanceRepository


async def dispatch_live_email_send(
    *,
    actions: ActionRepository,
    allowance: LiveSendAllowanceRepository,
    provider: EmailSendProvider,
    proposal: ActionProposalRow,
    approval: ApprovalRow,
    draft: OutreachDraftRow,
    idempotency_key: str,
    live_max_sends_per_day: int,
    allowance_window_s: float = 86400.0,
) -> ActionExecutionRow:
    now = datetime.now(timezone.utc)
    execution_id = str(uuid.uuid4())

    assert proposal.sender_identifier is not None  # gate 3 (§Part 9) already re-verified this upstream
    message_id_header = generate_message_id_header(
        execution_id=execution_id,
        idempotency_key=idempotency_key,
        sender_identifier=proposal.sender_identifier,
    )

    row, won = await actions.insert_claimed_execution(
        execution_id=execution_id,
        action_proposal_id=proposal.id,
        approval_id=approval.id,
        prospect_id=proposal.prospect_id,
        run_id=proposal.run_id,
        action_type=ActionType.EMAIL_SEND,
        idempotency_key=idempotency_key,
        recipient_identity_key=proposal.recipient_identity_key,
        sender_identifier=proposal.sender_identifier,
        origin=ActionExecutionOrigin.LIVE_EXTERNAL,
        message_id_header=message_id_header,
        claimed_at=now,
    )
    if not won:
        # A duplicate execute request (request idempotency, §3.5A) — the
        # EXISTING row, whatever its current state, never a second attempt.
        return row

    reservation_outcome = await allowance.try_reserve(
        row.id, now=now, window_s=allowance_window_s, limit=live_max_sends_per_day
    )
    if reservation_outcome is not AllowanceReservationOutcome.GRANTED:
        return await actions.settle_execution_failed(
            row.id,
            provider=None,
            dispatched=False,
            outcome_class=SendOutcome.PROVEN_NOT_DISPATCHED.value,
            settled_at=datetime.now(timezone.utc),
            last_error_type="ALLOWANCE_DENIED",
            last_error_message=f"live send allowance reservation denied: {reservation_outcome.value}",
        )

    in_flight = await actions.transition_to_in_flight(row.id, dispatched_at=datetime.now(timezone.utc))
    if in_flight is None:
        # Unreachable in practice (this execution_id is freshly minted and
        # uniquely ours) — fail closed rather than dispatch without a
        # confirmed IN_FLIGHT transition. The reservation is NOT released
        # (Phase 5 — reservations are never released for any outcome).
        current = await actions.get_execution(row.id)
        assert current is not None
        return current

    send_result = await provider.send(
        OutboundEmailMessage(
            to=proposal.recipient_identifier or "",
            subject=draft.subject or "",
            body_text=draft.body,
            message_id_header=message_id_header,
        ),
        idempotency_key=idempotency_key,
    )
    await actions.insert_send_calls_from_telemetry(row.id, send_result.telemetry)

    execution_status = execution_status_for_outcome(send_result.outcome)
    settled_at = datetime.now(timezone.utc)

    if execution_status is ActionExecutionStatus.SUCCEEDED:
        return await actions.settle_execution_succeeded(
            row.id,
            provider=provider.name,
            dispatched=send_result.dispatched,
            outcome_class=send_result.outcome.value,
            provider_message_id=send_result.provider_message_id,
            provider_thread_id=send_result.provider_thread_id,
            dispatched_at=in_flight.dispatched_at,
            settled_at=settled_at,
        )
    if execution_status is ActionExecutionStatus.FAILED:
        return await actions.settle_execution_failed(
            row.id,
            provider=provider.name,
            dispatched=send_result.dispatched,
            outcome_class=send_result.outcome.value,
            provider_message_id=send_result.provider_message_id,
            settled_at=settled_at,
        )
    # ACCEPTANCE_UNKNOWN -> UNCERTAIN. Never resent automatically.
    return await actions.settle_execution_uncertain(
        row.id,
        provider=provider.name,
        dispatched=send_result.dispatched,
        outcome_class=send_result.outcome.value,
        provider_message_id=send_result.provider_message_id,
        provider_thread_id=send_result.provider_thread_id,
        settled_at=settled_at,
    )
