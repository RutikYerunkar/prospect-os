"""V2-H/V2-I-b — action proposal + human approval + governed execution
(Demo and Live).

`draft -> explicit action proposal -> immutable hash/sender binding ->
human approval/rejection -> execution -> immutable action audit trail`.

D3: a proposal is created ONLY via an explicit POST naming a specific draft
— never automatically by the engine, never from a GET, never as an 8th
pipeline step. The seven-step pipeline and canonical board are untouched by
this router.

Real Live Gmail dispatch (V2-I-b): the structural `LiveExternalEmailSendDisabled`
refusal (D1) that made Live `EMAIL_SEND` unreachable through V2-H/V2-I-a and
part of V2-I-b was removed ONLY after the accepted plan's full verification
checklist passed in CI (SQLite, Postgres + migration drift, canonical Demo,
the complete safety-test matrix — see `docs/PROGRESS.md`'s V2-I-b entry and
PR #23) and on explicit, separate user authorization. `execute_action`'s
`LIVE_EXTERNAL` branch dispatches via `api/gmail_provider_factory.py::
build_gmail_send_provider` + `api/live_send_orchestration.py::
dispatch_live_email_send` — never `resolve_send_provider(Mode.LIVE)`, which
remains Demo-only and still unconditionally raises
`LiveExternalEmailSendDisabled` if ever called with `Mode.LIVE` (see
`providers/send_base.py`/`providers/send_registry.py`).
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from groundwork.api.deps import (
    ActionsRepoDep,
    ApprovalsRepoDep,
    GmailRepoDep,
    GoogleOAuthRuntimeDep,
    IsOperatorDep,
    LiveSendAllowanceRepoDep,
    ReposDep,
)
from groundwork.api.errors import (
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
    UnprocessableEntityError,
)
from groundwork.api.gmail_provider_factory import build_gmail_send_provider
from groundwork.api.live_gate import enforce_action_gate, enforce_live_gate, require_allowed_origin, require_operator
from groundwork.api.live_send_orchestration import dispatch_live_email_send
from groundwork.api.rate_limit import SlidingWindowRateLimiter
from groundwork.api.schemas import (
    ActionApprovalInfo,
    ActionApproveRequest,
    ActionAuditResponse,
    ActionEventInfo,
    ActionExecuteRequest,
    ActionExecutionInfo,
    ActionProposalResponse,
    ActionProposeRequest,
    ActionReconcileResponse,
    ActionRecoverResponse,
    ActionRejectRequest,
    ActionSendCallInfo,
)
from groundwork.config import settings
from groundwork.domain.action_policy import ActionPolicyResult, RecipientConflict, evaluate
from groundwork.domain.contact_identity import InvalidEmailIdentity, normalize_email_identity
from groundwork.domain.content_hash import HASH_VERSION, content_hash
from groundwork.models.enums import (
    ActionExecutionOrigin,
    ActionExecutionStatus,
    ActionPolicyVerdict,
    ActionType,
    Channel,
    EmailDiscoveryState,
    EmailVerificationState,
    LinkedInIdentityState,
    LinkedInResolutionState,
    Mode,
    ProspectStatus,
    ReviewVerdict,
)
from groundwork.models.tables import ActionExecutionRow, ActionProposalRow, ApprovalRow
from groundwork.providers.send_base import OutboundEmailMessage, ReconcileBounds, ReconcileStatus
from groundwork.providers.send_registry import resolve_send_provider
from groundwork.timeutil import ensure_aware, utcnow

router = APIRouter(prefix="/api/actions", tags=["actions"])

# Checkpoint I1 Phase 8B's shape, reused: in-process, per-client-IP — correct
# for ONE API instance, not a distributed rate limit (`api/rate_limit.py`).
# Applies to every governed-action WRITE (propose/approve/reject/execute) in
# BOTH modes — Part 9: "Demo action endpoints still require ... public-write
# abuse controls."
_write_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.action_write_rate_limit_attempts, window_s=settings.action_write_rate_limit_window_s
)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request) -> None:
    if not _write_limiter.allow(_client_key(request)):
        raise TooManyRequestsError("too many action requests — try again shortly")


def _approval_info(approval: ApprovalRow | None) -> ActionApprovalInfo | None:
    if approval is None:
        return None
    return ActionApprovalInfo(
        state=approval.decision, actor=approval.actor, reason=approval.reason, decided_at=approval.decided_at
    )


def _execution_info(execution: ActionExecutionRow | None) -> ActionExecutionInfo | None:
    if execution is None:
        return None
    return ActionExecutionInfo(
        id=execution.id,
        status=execution.status,
        origin=execution.origin,
        provider=execution.provider,
        dispatched=execution.dispatched,
        outcome_class=execution.outcome_class,
        provider_message_id=execution.provider_message_id,
        claimed_at=execution.claimed_at,
        dispatched_at=execution.dispatched_at,
        settled_at=execution.settled_at,
        reconcile_attempts=execution.reconcile_attempts,
        messages_scanned=execution.messages_scanned,
        reconciled_at=execution.reconciled_at,
        last_error_type=execution.last_error_type,
        last_error_message=execution.last_error_message,
    )


def _proposal_response(
    row: ActionProposalRow,
    *,
    approval: ApprovalRow | None,
    execution: ActionExecutionRow | None,
    created: bool,
) -> ActionProposalResponse:
    return ActionProposalResponse(
        id=row.id,
        prospect_id=row.prospect_id,
        run_id=row.run_id,
        draft_id=row.draft_id,
        action_type=row.action_type,
        channel=row.channel,
        sender_identifier=row.sender_identifier,
        recipient_identifier=row.recipient_identifier,
        content_hash=row.content_hash,
        hash_version=row.hash_version,
        policy_version=row.policy_version,
        policy_verdict=row.policy_verdict,
        blocked_reasons=row.blocked_reasons,
        origin=row.origin,
        created_at=row.created_at,
        superseded_by=row.superseded_by,
        created=created,
        approval=_approval_info(approval),
        execution=_execution_info(execution),
    )


async def _resolve_email_sender(mode: Mode, origin: ActionExecutionOrigin, gmail: GmailRepoDep) -> str | None:
    """Sender Resolution Matrix (V2-H, load-bearing): DEMO_SIMULATED's
    sender comes ONLY from `resolve_send_provider(Mode.DEMO)` — the
    `GmailConnectionRepository` is NEVER consulted, at proposal creation,
    approval, or execution, for a Demo `EMAIL_SEND`. LIVE_EXTERNAL may read
    `GmailConnectionRepository.connected_account_identifier()`; a missing
    connection yields `None` here (never an exception, never a proposal-
    construction failure) so the policy records `sender_not_connected`
    rather than the request itself throwing."""
    if origin is ActionExecutionOrigin.DEMO_SIMULATED:
        provider = resolve_send_provider(Mode.DEMO)
        return await provider.connected_account_identifier()
    raw = await gmail.connected_account_identifier()
    if not raw:
        return None
    try:
        return normalize_email_identity(raw)
    except InvalidEmailIdentity:
        return None


def _channel_states(contact_channels) -> tuple[object | None, object | None]:
    email = next((c for c in contact_channels if c.channel == Channel.EMAIL.value), None)
    linkedin = next((c for c in contact_channels if c.channel == Channel.LINKEDIN.value), None)
    return email, linkedin


async def _evaluate_policy(
    *,
    repos: ReposDep,
    actions: ActionsRepoDep,
    allowance: LiveSendAllowanceRepoDep,
    prospect_id: str,
    run_id: str,
    proposal_id: str | None,
    action_type: ActionType,
    origin: ActionExecutionOrigin,
    channel: Channel,
    draft_subject: str | None,
    draft_body: str | None,
    proposal_content_hash: str,
    recomputed_content_hash: str,
    proposal_hash_version: str,
    approval_hash_version: str | None,
    recipient_identifier: str | None,
    recipient_identity_key: str | None,
    connected_sender_identifier: str | None,
    proposal_sender_identifier: str | None,
) -> ActionPolicyResult:
    prospect = await repos.prospects.get(prospect_id)
    review = await repos.prospect_data.get_review(prospect_id)
    review_verdict = ReviewVerdict(review.verdict) if review else ReviewVerdict.FAIL
    prospect_status = ProspectStatus(prospect.status) if prospect else ProspectStatus.FAILED

    contact_channels = await repos.contact_enrichment.get_contact_channels(prospect_id)
    email_channel, linkedin_channel = _channel_states(contact_channels)

    recipient_conflict = RecipientConflict.NONE
    if action_type is ActionType.EMAIL_SEND and origin is ActionExecutionOrigin.LIVE_EXTERNAL:
        recipient_conflict = await actions.recipient_conflict(recipient_identity_key, exclude_proposal_id=proposal_id)

    # V2-I-a, clause 15 — suppressed if EITHER the current EMAIL channel
    # carries local suppression metadata OR the normalized recipient
    # identity is globally suppressed (a legal/privacy restriction observed
    # for this address by ANY prospect/run, ever). Applies to both origins —
    # never origin-gated the way clause 12 is.
    recipient_suppressed = False
    if action_type is ActionType.EMAIL_SEND:
        if email_channel is not None and email_channel.send_suppressed_at is not None:
            recipient_suppressed = True
        elif recipient_identity_key:
            global_suppression = await repos.contact_enrichment.get_email_suppression(recipient_identity_key)
            recipient_suppressed = global_suppression is not None

    demo_cap_reached = False
    if origin is ActionExecutionOrigin.DEMO_SIMULATED:
        demo_cap_reached = (await actions.count_demo_executions_for_run(run_id)) >= settings.demo_max_actions_per_run

    # V2-I-b — clause 14, now real: the rolling-24h reservation count is the
    # same DB-backed mechanism `dispatch_live_email_send` itself reserves
    # against (Phase 5) — this is the pre-check ("belt"); the reservation
    # transaction's own guarded UPDATE is the actual guarantee ("braces"),
    # exactly like clause 12's `recipient_conflict` precedent.
    live_allowance_exhausted = False
    if action_type is ActionType.EMAIL_SEND and origin is ActionExecutionOrigin.LIVE_EXTERNAL:
        reserved_count = await allowance.count_reservations_since(now=utcnow(), window_s=86400.0)
        live_allowance_exhausted = reserved_count >= settings.live_max_sends_per_day

    return evaluate(
        action_type=action_type,
        origin=origin,
        review_verdict=review_verdict,
        prospect_status=prospect_status,
        draft_channel=channel,
        draft_subject=draft_subject,
        draft_body=draft_body,
        proposal_content_hash=proposal_content_hash,
        recomputed_content_hash=recomputed_content_hash,
        proposal_hash_version=proposal_hash_version,
        approval_hash_version=approval_hash_version,
        email_discovery_state=(
            EmailDiscoveryState(email_channel.discovery_state)
            if email_channel and email_channel.discovery_state
            else None
        ),
        email_verification_state=(
            EmailVerificationState(email_channel.verification_state)
            if email_channel and email_channel.verification_state
            else None
        ),
        email_observed_at=email_channel.observed_at if email_channel else None,
        linkedin_discovery_state=(
            LinkedInResolutionState(linkedin_channel.discovery_state)
            if linkedin_channel and linkedin_channel.discovery_state
            else None
        ),
        linkedin_identity_match_state=(
            LinkedInIdentityState(linkedin_channel.identity_match_state)
            if linkedin_channel and linkedin_channel.identity_match_state
            else None
        ),
        linkedin_observed_at=linkedin_channel.observed_at if linkedin_channel else None,
        recipient_identifier=recipient_identifier,
        connected_sender_identifier=connected_sender_identifier,
        proposal_sender_identifier=proposal_sender_identifier,
        # V2-H/V2-I-b: a send MECHANISM is always nominally present for
        # EMAIL_SEND — Demo via `DemoEmailSendProvider`, Live via
        # `api/gmail_provider_factory.py::build_gmail_send_provider` (a
        # separate async seam, never `resolve_send_provider`, which stays
        # Demo-only — see that module's docstring). Clause 13 is
        # deliberately not the mechanism that blocks a disconnected Gmail
        # account either — clauses 10/11 (`sender_not_connected`/
        # `sender_changed`) already cover that, since a missing/mismatched
        # connection surfaces as `connected_sender_identifier` being falsy
        # or non-matching well before dispatch is ever reached.
        send_provider_configured=True,
        recipient_conflict=recipient_conflict,
        live_allowance_exhausted=live_allowance_exhausted,
        demo_action_cap_reached=demo_cap_reached,
        recipient_suppressed=recipient_suppressed,
    )


@router.post("/propose", response_model=ActionProposalResponse, status_code=201)
async def propose_action(
    body: ActionProposeRequest,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    gmail: GmailRepoDep,
    allowance: LiveSendAllowanceRepoDep,
    is_operator: IsOperatorDep,
) -> ActionProposalResponse:
    """D3 — the ONLY way an `ActionProposal` comes into existence: an
    explicit POST naming a specific `draft_id`. Idempotent on
    `UNIQUE(draft_id, content_hash)` (§Part 5) — proposing again for the
    same draft with unchanged sender/recipient/subject/body returns the
    existing proposal, never a duplicate row."""
    _rate_limit(request)

    draft = await actions.get_draft(body.draft_id)
    if draft is None:
        raise NotFoundError(f"no outreach draft with id {body.draft_id!r}")
    prospect = await repos.prospects.get(draft.prospect_id)
    if prospect is None:
        raise NotFoundError(f"no prospect with id {draft.prospect_id!r}")
    run = await repos.runs.get(prospect.run_id)
    if run is None:
        raise NotFoundError(f"no run with id {prospect.run_id!r}")

    enforce_action_gate(request, run.mode, is_operator)

    mode = Mode(run.mode)
    # Origin is derived server-side from run mode — NEVER accepted from
    # request JSON (`ActionProposeRequest` has no such field at all).
    origin = ActionExecutionOrigin.DEMO_SIMULATED if mode is Mode.DEMO else ActionExecutionOrigin.LIVE_EXTERNAL
    channel = Channel(draft.channel)
    action_type = ActionType.EMAIL_SEND if channel is Channel.EMAIL else ActionType.LINKEDIN_COPY_AND_OPEN

    contact_channels = await repos.contact_enrichment.get_contact_channels(prospect.id)
    email_channel, linkedin_channel = _channel_states(contact_channels)

    sender_identifier: str | None = None
    recipient_identifier: str | None = None
    recipient_identity_key: str | None = None

    if action_type is ActionType.EMAIL_SEND:
        sender_identifier = await _resolve_email_sender(mode, origin, gmail)
        recipient_identifier = email_channel.identifier if email_channel else None
        if recipient_identifier:
            try:
                recipient_identity_key = normalize_email_identity(recipient_identifier)
            except InvalidEmailIdentity:
                recipient_identity_key = None
    else:
        # LINKEDIN_COPY_AND_OPEN — sender_identifier stays None; Gmail/the
        # email send-provider resolver are NEVER consulted (D6/D2).
        recipient_identifier = linkedin_channel.identifier if linkedin_channel else None

    computed_hash = content_hash(
        channel=channel,
        sender_identifier=sender_identifier,
        recipient_identifier=recipient_identifier,
        subject=draft.subject,
        body=draft.body,
    )

    # No approval exists yet at proposal time — clause 9's supersession
    # check is meaningful only once a real approval exists (§3.9 step 2 at
    # EXECUTE time); passing the current HASH_VERSION here just means "not
    # yet superseded by anything," never a spurious block on a fresh
    # proposal.
    policy_result = await _evaluate_policy(
        repos=repos,
        actions=actions,
        allowance=allowance,
        prospect_id=prospect.id,
        run_id=run.id,
        proposal_id=None,  # no proposal exists yet at proposal-creation time
        action_type=action_type,
        origin=origin,
        channel=channel,
        draft_subject=draft.subject,
        draft_body=draft.body,
        proposal_content_hash=computed_hash,
        recomputed_content_hash=computed_hash,
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        recipient_identifier=recipient_identifier,
        recipient_identity_key=recipient_identity_key,
        connected_sender_identifier=sender_identifier,
        proposal_sender_identifier=sender_identifier,
    )

    row, created = await actions.create_proposal(
        prospect_id=prospect.id,
        run_id=run.id,
        draft_id=draft.id,
        action_type=action_type,
        channel=channel.value,
        sender_identifier=sender_identifier,
        recipient_identifier=recipient_identifier,
        recipient_identity_key=recipient_identity_key,
        content_hash=computed_hash,
        hash_version=HASH_VERSION,
        policy_version=policy_result.policy_version,
        policy_verdict=policy_result.verdict.value,
        blocked_reasons=policy_result.blocked_reasons,
        policy_snapshot=policy_result.policy_snapshot,
        origin=origin,
    )
    if created:
        await actions.set_draft_content_hash(draft.id, content_hash=row.content_hash, hash_version=row.hash_version)
        await actions.record_event(
            prospect_id=prospect.id,
            type="proposal_created",
            actor="system",
            action_proposal_id=row.id,
            payload={"policy_verdict": row.policy_verdict, "action_type": row.action_type},
        )

    return _proposal_response(row, approval=None, execution=None, created=created)


async def _load_proposal_and_run(proposal_id: str, repos: ReposDep, actions: ActionsRepoDep):
    proposal = await actions.get_proposal(proposal_id)
    if proposal is None:
        raise NotFoundError(f"no action proposal with id {proposal_id!r}")
    run = await repos.runs.get(proposal.run_id)
    if run is None:
        raise NotFoundError(f"no run with id {proposal.run_id!r}")
    return proposal, run


@router.get("/proposals/{proposal_id}", response_model=ActionProposalResponse)
async def get_action_proposal(
    proposal_id: str,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ActionProposalResponse:
    proposal, run = await _load_proposal_and_run(proposal_id, repos, actions)
    enforce_live_gate(request, run.mode, is_operator)
    approval = await approvals.latest_for_proposal(proposal.id)
    execution = await actions.latest_execution_for_proposal(proposal.id)
    return _proposal_response(proposal, approval=approval, execution=execution, created=False)


@router.get("/prospects/{prospect_id}/proposals", response_model=list[ActionProposalResponse])
async def list_action_proposals(
    prospect_id: str,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> list[ActionProposalResponse]:
    prospect = await repos.prospects.get(prospect_id)
    if prospect is None:
        raise NotFoundError(f"no prospect with id {prospect_id!r}")
    run = await repos.runs.get(prospect.run_id)
    enforce_live_gate(request, run.mode if run is not None else "demo", is_operator)

    rows = await actions.list_proposals_for_prospect(prospect_id)
    responses = []
    for row in rows:
        approval = await approvals.latest_for_proposal(row.id)
        execution = await actions.latest_execution_for_proposal(row.id)
        responses.append(_proposal_response(row, approval=approval, execution=execution, created=False))
    return responses


@router.post("/proposals/{proposal_id}/approve", response_model=ActionProposalResponse)
async def approve_action(
    proposal_id: str,
    body: ActionApproveRequest,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ActionProposalResponse:
    _rate_limit(request)
    proposal, run = await _load_proposal_and_run(proposal_id, repos, actions)
    enforce_action_gate(request, run.mode, is_operator)

    # D7 — no override anywhere. A BLOCKED proposal cannot be approved; the
    # UI is never the only gate — this is enforced server-side too.
    if proposal.policy_verdict != ActionPolicyVerdict.ELIGIBLE.value:
        raise ConflictError(
            "this proposal is BLOCKED and cannot be approved — there is no override; "
            f"blocked_reasons={proposal.blocked_reasons}",
            code="PROPOSAL_BLOCKED",
        )

    approval = await approvals.create_action_approval(
        prospect_id=proposal.prospect_id,
        action_proposal_id=proposal.id,
        content_hash=proposal.content_hash,
        hash_version=proposal.hash_version,
        decision="APPROVED",
        actor=body.actor,
    )
    await actions.record_event(
        prospect_id=proposal.prospect_id,
        type="proposal_approved",
        actor=body.actor,
        action_proposal_id=proposal.id,
    )
    execution = await actions.latest_execution_for_proposal(proposal.id)
    return _proposal_response(proposal, approval=approval, execution=execution, created=False)


@router.post("/proposals/{proposal_id}/reject", response_model=ActionProposalResponse)
async def reject_action(
    proposal_id: str,
    body: ActionRejectRequest,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ActionProposalResponse:
    _rate_limit(request)
    proposal, run = await _load_proposal_and_run(proposal_id, repos, actions)
    enforce_action_gate(request, run.mode, is_operator)

    approval = await approvals.create_action_approval(
        prospect_id=proposal.prospect_id,
        action_proposal_id=proposal.id,
        content_hash=proposal.content_hash,
        hash_version=proposal.hash_version,
        decision="REJECTED",
        actor=body.actor,
        reason=body.reason,
    )
    await actions.record_event(
        prospect_id=proposal.prospect_id,
        type="proposal_rejected",
        actor=body.actor,
        action_proposal_id=proposal.id,
        payload={"reason": body.reason},
    )
    execution = await actions.latest_execution_for_proposal(proposal.id)
    return _proposal_response(proposal, approval=approval, execution=execution, created=False)


@router.post("/proposals/{proposal_id}/execute", response_model=ActionProposalResponse)
async def execute_action(
    proposal_id: str,
    body: ActionExecuteRequest,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    gmail: GmailRepoDep,
    oauth_runtime: GoogleOAuthRuntimeDep,
    allowance: LiveSendAllowanceRepoDep,
    is_operator: IsOperatorDep,
) -> ActionProposalResponse:
    """Execute-time enforcement order (§3.9, load-bearing — do not reorder):
    1. capability gate (operator session for Live; Origin in both modes)
    2. an `ACTION`-scope `APPROVED` approval exists for this exact proposal
    3. `hash_version` equality (proposal == approval == current) -> 409
       `APPROVAL_SUPERSEDED`
    4. sender re-resolution, by `action_type` + `origin` (Sender Resolution
       Matrix)
    5. fresh `content_hash` recomputation from the live draft + the
       proposal's own recipient + the freshly re-resolved sender
    6. hash comparison (`hmac.compare_digest`) -> 409 `CONTENT_CHANGED`
    7. fresh full `action_policy.evaluate()` -> 409 with `blocked_reasons`
       (includes clause 15 recipient suppression and clause 14 the rolling
       24h allowance pre-check — both real, both evaluated fresh here)
    8. request-idempotency claim (`sha256(approval_id|content_hash)`) — a
       duplicate execute returns the EXISTING execution, never a second one
    9. dispatch: `LINKEDIN_COPY_AND_OPEN` never touches a send provider;
       Demo `EMAIL_SEND` dispatches via `resolve_send_provider(Mode.DEMO)`;
       `LIVE_EXTERNAL` `EMAIL_SEND` dispatches via
       `api/gmail_provider_factory.py::build_gmail_send_provider` (a working
       Gmail credential or an honest `409 GMAIL_NOT_CONNECTED`) then
       `api/live_send_orchestration.py::dispatch_live_email_send`, ordered
       CLAIMED -> allowance reservation (the actual guarantee, not just the
       clause 14 pre-check) -> guarded IN_FLIGHT -> the one
       Gmail HTTP call -> classify -> settle.
    """
    _rate_limit(request)
    proposal, run = await _load_proposal_and_run(proposal_id, repos, actions)
    mode = Mode(run.mode)
    origin = ActionExecutionOrigin(proposal.origin)
    action_type = ActionType(proposal.action_type)
    channel = Channel(proposal.channel)

    enforce_action_gate(request, run.mode, is_operator)

    approval = await approvals.latest_for_proposal(proposal.id)
    if approval is None or approval.decision != "APPROVED" or approval.action_proposal_id != proposal.id:
        raise ConflictError("this proposal has no APPROVED action approval", code="NOT_APPROVED")

    if (
        approval.hash_version != proposal.hash_version
        or proposal.hash_version != HASH_VERSION
        or approval.hash_version != HASH_VERSION
    ):
        raise ConflictError("the approval no longer matches the current hash version", code="APPROVAL_SUPERSEDED")

    connected_sender: str | None = None
    if action_type is ActionType.EMAIL_SEND:
        connected_sender = await _resolve_email_sender(mode, origin, gmail)
        # Gate 3 (Part 9) — checked explicitly, and BEFORE the content-hash
        # comparison below, so a sender swap is never masked as a generic
        # `CONTENT_CHANGED` (the hash also covers the sender — §3.9 — so a
        # real mismatch would trip both; this makes the two failure modes
        # independently distinguishable, exactly as the frozen "all five
        # Live gates independently reject" test matrix requires).
        if not connected_sender:
            raise ConflictError("no send identity is currently connected", code="SENDER_NOT_CONNECTED")
        if not hmac.compare_digest(connected_sender, proposal.sender_identifier or ""):
            raise ConflictError(
                "the connected sending identity no longer matches this proposal", code="SENDER_CHANGED"
            )

    draft = await actions.get_draft(proposal.draft_id)
    if draft is None:
        raise NotFoundError(f"no outreach draft with id {proposal.draft_id!r}")

    recomputed_hash = content_hash(
        channel=channel,
        sender_identifier=connected_sender if action_type is ActionType.EMAIL_SEND else None,
        # V2-H deliberate simplification (docs/PROGRESS.md): the recipient
        # is NOT re-resolved from `contact_channels` at execute time — only
        # the sender is (Sender Resolution Matrix). The immutable proposal's
        # own `recipient_identifier` is what was hashed and approved.
        recipient_identifier=proposal.recipient_identifier,
        subject=draft.subject,
        body=draft.body,
    )

    if not hmac.compare_digest(recomputed_hash, approval.content_hash):
        raise ConflictError("the draft changed after approval", code="CONTENT_CHANGED")

    policy_result = await _evaluate_policy(
        repos=repos,
        actions=actions,
        allowance=allowance,
        prospect_id=proposal.prospect_id,
        run_id=run.id,
        proposal_id=proposal.id,
        action_type=action_type,
        origin=origin,
        channel=channel,
        draft_subject=draft.subject,
        draft_body=draft.body,
        proposal_content_hash=approval.content_hash,
        recomputed_content_hash=recomputed_hash,
        proposal_hash_version=proposal.hash_version,
        approval_hash_version=approval.hash_version,
        recipient_identifier=proposal.recipient_identifier,
        recipient_identity_key=proposal.recipient_identity_key,
        connected_sender_identifier=connected_sender,
        proposal_sender_identifier=proposal.sender_identifier,
    )

    if policy_result.verdict is not ActionPolicyVerdict.ELIGIBLE:
        await actions.record_event(
            prospect_id=proposal.prospect_id,
            type="execution_blocked",
            actor=body.actor,
            action_proposal_id=proposal.id,
            payload={"blocked_reasons": policy_result.blocked_reasons},
        )
        first_reason = policy_result.blocked_reasons[0] if policy_result.blocked_reasons else "ACTION_BLOCKED"
        raise ConflictError(
            f"action blocked by policy: {', '.join(policy_result.blocked_reasons)}", code=first_reason.upper()
        )

    idempotency_key = hashlib.sha256(f"{approval.id}|{approval.content_hash}".encode()).hexdigest()
    existing = await actions.get_execution_by_idempotency_key(idempotency_key)
    if existing is not None:
        return _proposal_response(proposal, approval=approval, execution=existing, created=False)

    now = datetime.now(timezone.utc)

    if action_type is ActionType.LINKEDIN_COPY_AND_OPEN:
        row, won = await actions.insert_claimed_execution(
            execution_id=str(uuid.uuid4()),
            action_proposal_id=proposal.id,
            approval_id=approval.id,
            prospect_id=proposal.prospect_id,
            run_id=run.id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            recipient_identity_key=None,
            sender_identifier=None,
            origin=origin,
            message_id_header=None,
            claimed_at=now,
        )
        if not won:
            return _proposal_response(proposal, approval=approval, execution=row, created=False)
        settled = await actions.settle_execution_succeeded(
            row.id,
            provider=None,
            dispatched=False,
            outcome_class=None,
            provider_message_id=None,
            dispatched_at=None,
            settled_at=datetime.now(timezone.utc),
        )
        await actions.record_event(
            prospect_id=proposal.prospect_id,
            type="execution_succeeded",
            actor=body.actor,
            action_proposal_id=proposal.id,
            action_execution_id=row.id,
        )
        return _proposal_response(proposal, approval=approval, execution=settled, created=True)

    # EMAIL_SEND
    if origin is ActionExecutionOrigin.DEMO_SIMULATED:
        provider = resolve_send_provider(Mode.DEMO)
        message_id_header = f"<{uuid.uuid4()}@groundwork.invalid>"
        row, won = await actions.insert_claimed_execution(
            execution_id=str(uuid.uuid4()),
            action_proposal_id=proposal.id,
            approval_id=approval.id,
            prospect_id=proposal.prospect_id,
            run_id=run.id,
            action_type=action_type,
            idempotency_key=idempotency_key,
            recipient_identity_key=proposal.recipient_identity_key,
            sender_identifier=proposal.sender_identifier,
            origin=origin,
            message_id_header=message_id_header,
            claimed_at=now,
        )
        if not won:
            return _proposal_response(proposal, approval=approval, execution=row, created=False)

        call_started = datetime.now(timezone.utc)
        send_result = await provider.send(
            OutboundEmailMessage(
                to=proposal.recipient_identifier or "",
                subject=draft.subject or "",
                body_text=draft.body,
                message_id_header=message_id_header,
            ),
            idempotency_key=idempotency_key,
        )
        call_finished = datetime.now(timezone.utc)

        settled = await actions.settle_execution_succeeded(
            row.id,
            provider=provider.name,
            dispatched=send_result.dispatched,
            outcome_class=send_result.outcome.value,
            provider_message_id=send_result.provider_message_id,
            provider_thread_id=send_result.provider_thread_id,
            dispatched_at=now,
            settled_at=call_finished,
        )
        await actions.insert_send_call(
            action_execution_id=row.id,
            call_group_id=str(uuid.uuid4()),
            provider=provider.name,
            status="OK",
            started_at=call_started,
            finished_at=call_finished,
        )
        await actions.record_event(
            prospect_id=proposal.prospect_id,
            type="execution_succeeded",
            actor=body.actor,
            action_proposal_id=proposal.id,
            action_execution_id=row.id,
            payload={"provider_message_id": send_result.provider_message_id},
        )
        return _proposal_response(proposal, approval=approval, execution=settled, created=True)

    # LIVE_EXTERNAL — every gate above has already passed (operator session,
    # approval, hash_version, sender match, content hash, fresh policy
    # ELIGIBLE — including clause 15 recipient suppression, both local and
    # global, and clause 14's allowance pre-check). The structural
    # `LiveExternalEmailSendDisabled` refusal that gated this branch through
    # V2-H/V2-I-a and for part of V2-I-b has been deliberately removed, ONLY
    # after the full accepted-plan verification checklist actually passed in
    # CI — full SQLite, full Postgres + migration drift, canonical Demo, and
    # the complete safety-test matrix (see docs/PROGRESS.md's V2-I-b entry
    # and PR #23) — and only on the user's explicit, separate authorization
    # for this exact change. `resolve_send_provider(Mode.LIVE)` itself is
    # UNCHANGED and still unconditionally raises `LiveExternalEmailSendDisabled`
    # if anything calls it that way (see providers/send_registry.py) — this
    # branch simply no longer calls it.
    #
    # `provider is None` is a rare defensive case here (clauses 10/11 above
    # already require a matching connected sender) — a refresh token that
    # fails to decrypt (e.g. a key rotated without `_OLD` set) is the
    # realistic cause; it degrades honestly to `409 GMAIL_NOT_CONNECTED`,
    # never a fixture fallback.
    provider = await build_gmail_send_provider(gmail, oauth_runtime)
    if provider is None:
        await actions.record_event(
            prospect_id=proposal.prospect_id,
            type="live_send_unavailable",
            actor=body.actor,
            action_proposal_id=proposal.id,
            payload={"reason": "no working Gmail send provider could be constructed"},
        )
        raise ConflictError(
            "Gmail is not connected or its stored credential could not be used — reconnect Gmail in Settings",
            code="GMAIL_NOT_CONNECTED",
        )

    settled = await dispatch_live_email_send(
        actions=actions,
        allowance=allowance,
        provider=provider,
        proposal=proposal,
        approval=approval,
        draft=draft,
        idempotency_key=idempotency_key,
        live_max_sends_per_day=settings.live_max_sends_per_day,
    )
    event_type = {
        ActionExecutionStatus.SUCCEEDED.value: "execution_succeeded",
        ActionExecutionStatus.FAILED.value: "execution_failed",
        ActionExecutionStatus.UNCERTAIN.value: "execution_uncertain",
    }.get(settled.status, "execution_settled")
    await actions.record_event(
        prospect_id=proposal.prospect_id,
        type=event_type,
        actor=body.actor,
        action_proposal_id=proposal.id,
        action_execution_id=settled.id,
        payload={"outcome_class": settled.outcome_class, "provider_message_id": settled.provider_message_id},
    )
    return _proposal_response(proposal, approval=approval, execution=settled, created=True)


# ============================================================================
# V2-I-b Phase 8/9/10 — reconciliation, stale recovery, audit.
#
# None of these three endpoints make Live sending REACHABLE — they operate
# only on an execution row that already exists (an already-`UNCERTAIN` or
# already-stale `CLAIMED`/`IN_FLIGHT` row), never create a first-time send,
# and never call `resolve_send_provider(Mode.LIVE)`. They are safe to wire in
# before the refusal-removal gate.
# ============================================================================


async def _require_execution(execution_id: str, actions: ActionsRepoDep) -> ActionExecutionRow:
    execution = await actions.get_execution(execution_id)
    if execution is None:
        raise NotFoundError(f"no action execution with id {execution_id!r}")
    return execution


@router.post("/executions/{execution_id}/reconcile", response_model=ActionReconcileResponse)
async def reconcile_execution(
    execution_id: str,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    gmail: GmailRepoDep,
    oauth_runtime: GoogleOAuthRuntimeDep,
    is_operator: IsOperatorDep,
) -> ActionReconcileResponse:
    """§3.3 bounded reconciliation — operator-gated, one bounded attempt per
    eligible call, no scheduler/background worker. `NOT_FOUND_WITHIN_BOUNDS`,
    `AMBIGUOUS`, `HISTORY_EXPIRED`, and `LOOKUP_FAILED` NEVER convert to
    `FAILED` — the execution stays `UNCERTAIN`. Zero-egress triage happens
    BEFORE any Gmail call: attempts exhausted with the window still open
    stays `UNCERTAIN` with zero calls; a window that has expired settles to
    `ABANDONED` with zero calls.

    V2-I-b correction (post-smoke): matching is anchored on the persisted
    `pre_dispatch_history_id` plus approved Subject/To/From/Date
    (`domain/reconciliation_match.py`), NOT on `message_id_header` — the
    real smoke send proved Gmail can omit our generated header from both
    `Message-ID` and `X-Google-Original-Message-ID`. See
    `docs/PROGRESS.md`'s V2-I-b entry for the full account."""
    require_operator(is_operator)
    require_allowed_origin(request)

    execution = await _require_execution(execution_id, actions)
    if execution.action_type != ActionType.EMAIL_SEND.value or execution.origin != ActionExecutionOrigin.LIVE_EXTERNAL.value:
        raise UnprocessableEntityError("reconciliation only applies to LIVE_EXTERNAL EMAIL_SEND executions")
    if execution.status != ActionExecutionStatus.UNCERTAIN.value:
        raise ConflictError(
            f"execution status is {execution.status!r}, not UNCERTAIN — nothing to reconcile", code="NOT_UNCERTAIN"
        )
    if not execution.pre_dispatch_history_id or not execution.dispatched_at or not execution.settled_at:
        raise ConflictError(
            "execution has no pre_dispatch_history_id/dispatched_at/settled_at — cannot reconcile",
            code="NOT_RECONCILABLE",
        )

    proposal = await actions.get_proposal(execution.action_proposal_id)
    if proposal is None:
        raise UnprocessableEntityError("the proposal for this execution no longer exists")
    draft = await actions.get_draft(proposal.draft_id)
    if draft is None:
        raise UnprocessableEntityError("the draft for this execution's proposal no longer exists")

    now = datetime.now(timezone.utc)
    # SQLite drops tzinfo on read (`groundwork/timeutil.py`) — every
    # persisted timestamp compared against a fresh `datetime.now(timezone.
    # utc)` must be re-attached via `ensure_aware` first, or the comparison
    # raises `TypeError: can't compare offset-naive and offset-aware
    # datetimes` instead of ever reaching the intended zero-egress logic.
    dispatched_at = ensure_aware(execution.dispatched_at)
    settled_at = ensure_aware(execution.settled_at)
    assert dispatched_at is not None and settled_at is not None
    window_deadline = dispatched_at + timedelta(seconds=settings.reconcile_window_s)

    # --- zero-egress triage — BEFORE any Gmail call ----------------------
    if now >= window_deadline:
        settled = await actions.mark_execution_abandoned(execution.id, settled_at=now)
        if settled is not None:
            await actions.record_event(
                prospect_id=execution.prospect_id,
                type="execution_abandoned",
                actor="operator",
                action_execution_id=execution.id,
                payload={"reason": "reconciliation window expired"},
            )
            execution = settled
        return ActionReconcileResponse(
            execution=_execution_info(execution), reconcile_status="ABANDONED", attempts_remaining=0
        )

    if execution.reconcile_attempts >= settings.reconcile_max_attempts:
        return ActionReconcileResponse(
            execution=_execution_info(execution),
            reconcile_status="ATTEMPTS_EXHAUSTED_WINDOW_OPEN",
            attempts_remaining=0,
            next_terminalization_at=window_deadline,
        )

    provider = await build_gmail_send_provider(gmail, oauth_runtime)
    if provider is None:
        raise UnprocessableEntityError("Gmail is not connected/configured — cannot reconcile")

    bounds = ReconcileBounds(
        page_size=settings.reconcile_page_size,
        max_pages=settings.reconcile_max_pages,
        max_messages=settings.reconcile_max_messages,
        clock_skew_s=settings.reconcile_clock_skew_s,
    )
    clock_skew = timedelta(seconds=settings.reconcile_clock_skew_s)
    result = await provider.find_sent_message(
        pre_dispatch_history_id=execution.pre_dispatch_history_id,
        expected_subject=draft.subject or "",
        expected_recipient_identifier=proposal.recipient_identifier or "",
        expected_sender_identifier=proposal.sender_identifier or "",
        window_start=dispatched_at - clock_skew,
        window_end=settled_at + clock_skew,
        bounds=bounds,
    )
    await actions.insert_send_calls_from_telemetry(execution.id, result.telemetry)

    if result.status is ReconcileStatus.FOUND:
        settled = await actions.settle_execution_found_via_reconciliation(
            execution.id, provider_message_id=result.provider_message_id or "", reconciled_at=now
        )
        if settled is not None:
            await actions.record_event(
                prospect_id=execution.prospect_id,
                type="execution_reconciled",
                actor="operator",
                action_execution_id=execution.id,
                payload={"result": "FOUND", "messages_scanned": result.messages_scanned},
            )
            execution = settled
        return ActionReconcileResponse(
            execution=_execution_info(execution), reconcile_status=ReconcileStatus.FOUND.value, attempts_remaining=max(
                0, settings.reconcile_max_attempts - execution.reconcile_attempts
            )
        )

    # NOT_FOUND_WITHIN_BOUNDS, AMBIGUOUS, HISTORY_EXPIRED, or LOOKUP_FAILED —
    # never FAILED; bookkeeping only, distinctly labeled via result.status.
    updated = await actions.record_reconcile_attempt(
        execution.id, messages_scanned_delta=result.messages_scanned, reconciled_at=now
    )
    if updated is not None:
        execution = updated
    await actions.record_event(
        prospect_id=execution.prospect_id,
        type="execution_reconcile_attempt",
        actor="operator",
        action_execution_id=execution.id,
        payload={"result": result.status.value, "messages_scanned": result.messages_scanned},
    )
    attempts_remaining = max(0, settings.reconcile_max_attempts - execution.reconcile_attempts)
    return ActionReconcileResponse(
        execution=_execution_info(execution),
        reconcile_status=result.status.value,
        attempts_remaining=attempts_remaining,
        next_terminalization_at=window_deadline,
    )


@router.post("/executions/{execution_id}/recover", response_model=ActionRecoverResponse)
async def recover_execution(
    execution_id: str, request: Request, actions: ActionsRepoDep, is_operator: IsOperatorDep
) -> ActionRecoverResponse:
    """Stale recovery (Phase 9) — operator-gated, per execution. Never
    dispatches, never releases the allowance reservation, never resends.
    `CLAIMED` with no `dispatched_at` -> `PROVEN_NOT_DISPATCHED` -> `FAILED`.
    `IN_FLIGHT` with `dispatched_at` set -> `ACCEPTANCE_UNKNOWN` ->
    `UNCERTAIN`. An impossible/ambiguous persisted shape (a contradiction
    between status and `dispatched_at`) also settles to `UNCERTAIN`, with a
    dedicated anomaly audit event. A row younger than the stale lease is
    refused outright."""
    require_operator(is_operator)
    require_allowed_origin(request)

    execution = await _require_execution(execution_id, actions)
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.execution_stale_lease_s)

    status = execution.status
    claimed = execution.status == ActionExecutionStatus.CLAIMED.value
    in_flight = execution.status == ActionExecutionStatus.IN_FLIGHT.value
    if not claimed and not in_flight:
        return ActionRecoverResponse(
            execution=_execution_info(execution),
            recovered=False,
            reason=f"status is {status!r} — not eligible for recovery",
        )

    anomaly = (claimed and execution.dispatched_at is not None) or (in_flight and execution.dispatched_at is None)
    staleness_ref = ensure_aware(
        execution.claimed_at if (claimed or execution.dispatched_at is None) else execution.dispatched_at
    )
    if staleness_ref is None or staleness_ref >= stale_before:
        return ActionRecoverResponse(
            execution=_execution_info(execution), recovered=False, reason="younger than the stale lease — refused"
        )

    if anomaly:
        expected_status = ActionExecutionStatus.CLAIMED if claimed else ActionExecutionStatus.IN_FLIGHT
        settled = await actions.force_settle_stale(
            execution.id,
            expected_status=expected_status,
            new_status=ActionExecutionStatus.UNCERTAIN,
            outcome_class="ACCEPTANCE_UNKNOWN",
            settled_at=now,
        )
        if settled is not None:
            await actions.record_event(
                prospect_id=execution.prospect_id,
                type="execution_recovery_anomaly",
                actor="operator",
                action_execution_id=execution.id,
                payload={"status": status, "dispatched_at_set": execution.dispatched_at is not None},
            )
            return ActionRecoverResponse(execution=_execution_info(settled), recovered=True, reason="anomaly -> UNCERTAIN")
        return ActionRecoverResponse(execution=_execution_info(execution), recovered=False, reason="already changed")

    if claimed:
        settled = await actions.force_settle_stale(
            execution.id,
            expected_status=ActionExecutionStatus.CLAIMED,
            new_status=ActionExecutionStatus.FAILED,
            outcome_class="PROVEN_NOT_DISPATCHED",
            settled_at=now,
        )
        event_type = "execution_recovered_failed"
    else:
        settled = await actions.force_settle_stale(
            execution.id,
            expected_status=ActionExecutionStatus.IN_FLIGHT,
            new_status=ActionExecutionStatus.UNCERTAIN,
            outcome_class="ACCEPTANCE_UNKNOWN",
            settled_at=now,
        )
        event_type = "execution_recovered_uncertain"

    if settled is None:
        return ActionRecoverResponse(execution=_execution_info(execution), recovered=False, reason="already changed")

    await actions.record_event(
        prospect_id=execution.prospect_id, type=event_type, actor="operator", action_execution_id=execution.id
    )
    return ActionRecoverResponse(execution=_execution_info(settled), recovered=True, reason=None)


@router.get("/proposals/{proposal_id}/audit", response_model=ActionAuditResponse)
async def get_action_audit(
    proposal_id: str,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ActionAuditResponse:
    """Phase 10 — the full, immutable audit trail for one proposal: the
    proposal itself, its approval/rejection, its execution (including
    reconciliation bookkeeping), every `action_events` row, and every
    `action_send_calls` telemetry row. Never a raw Gmail provider payload,
    never an OAuth token, never raw MIME — only already-derived, safe
    fields."""
    proposal, run = await _load_proposal_and_run(proposal_id, repos, actions)
    enforce_live_gate(request, run.mode, is_operator)

    approval = await approvals.latest_for_proposal(proposal.id)
    execution = await actions.latest_execution_for_proposal(proposal.id)
    events = await actions.list_events_for_proposal(proposal.id)
    send_calls = await actions.list_send_calls_for_execution(execution.id) if execution is not None else []

    return ActionAuditResponse(
        proposal=_proposal_response(proposal, approval=approval, execution=execution, created=False),
        events=[
            ActionEventInfo(id=e.id, type=e.type, actor=e.actor, payload=e.payload, ts=e.ts) for e in events
        ],
        send_calls=[
            ActionSendCallInfo(
                id=c.id,
                operation=c.operation,
                provider=c.provider,
                status=c.status,
                started_at=c.started_at,
                finished_at=c.finished_at,
                latency_ms=c.latency_ms,
                http_status=c.http_status,
                error_type=c.error_type,
            )
            for c in send_calls
        ],
    )
