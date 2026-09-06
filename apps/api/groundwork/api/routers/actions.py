"""V2-H — action proposal + human approval (Demo executor only).

`draft -> explicit action proposal -> immutable hash/sender binding ->
human approval/rejection -> Demo execution -> immutable action audit trail`.

D3: a proposal is created ONLY via an explicit POST naming a specific draft
— never automatically by the engine, never from a GET, never as an 8th
pipeline step. The seven-step pipeline and canonical board are untouched by
this router. Real Gmail sending remains entirely V2-I: Live `EMAIL_SEND`
structurally terminates at `LiveExternalEmailSendDisabled` (D1/D4) before any
send-provider implementation or network dispatch is ever reachable — see
`providers/send_base.py`/`providers/send_registry.py`.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from groundwork.api.deps import ActionsRepoDep, ApprovalsRepoDep, GmailRepoDep, IsOperatorDep, ReposDep
from groundwork.api.errors import ActionDisabledError, ConflictError, NotFoundError, TooManyRequestsError
from groundwork.api.live_gate import enforce_action_gate, enforce_live_gate
from groundwork.api.rate_limit import SlidingWindowRateLimiter
from groundwork.api.schemas import (
    ActionApprovalInfo,
    ActionApproveRequest,
    ActionExecuteRequest,
    ActionExecutionInfo,
    ActionProposalResponse,
    ActionProposeRequest,
    ActionRejectRequest,
)
from groundwork.config import settings
from groundwork.domain.action_policy import ActionPolicyResult, RecipientConflict, evaluate
from groundwork.domain.contact_identity import InvalidEmailIdentity, normalize_email_identity
from groundwork.domain.content_hash import HASH_VERSION, content_hash
from groundwork.models.enums import (
    ActionExecutionOrigin,
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
from groundwork.providers.send_base import LiveExternalEmailSendDisabled, OutboundEmailMessage
from groundwork.providers.send_registry import resolve_send_provider

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
    prospect_id: str,
    run_id: str,
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
        recipient_conflict = await actions.recipient_conflict(recipient_identity_key)

    demo_cap_reached = False
    if origin is ActionExecutionOrigin.DEMO_SIMULATED:
        demo_cap_reached = (await actions.count_demo_executions_for_run(run_id)) >= settings.demo_max_actions_per_run

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
        # V2-H deliberate simplification (recorded in docs/PROGRESS.md): a
        # send MECHANISM is always nominally present for EMAIL_SEND in this
        # checkpoint — Demo via `DemoEmailSendProvider`, Live via the same
        # `resolve_send_provider` seam. The frozen brief explicitly warns
        # against relying on clause 13 (`send_provider_unavailable`) as the
        # proof that Live sending is blocked (D4) — that proof is the
        # dedicated `LiveExternalEmailSendDisabled` structural refusal,
        # reached only AFTER a fresh policy ELIGIBLE verdict, at dispatch.
        send_provider_configured=True,
        recipient_conflict=recipient_conflict,
        live_allowance_exhausted=False,  # V2-I scope — no real Live send exists to exhaust an allowance
        demo_action_cap_reached=demo_cap_reached,
    )


@router.post("/propose", response_model=ActionProposalResponse, status_code=201)
async def propose_action(
    body: ActionProposeRequest,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    gmail: GmailRepoDep,
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
        prospect_id=prospect.id,
        run_id=run.id,
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
    8. request-idempotency claim (`sha256(approval_id|content_hash)`) — a
       duplicate execute returns the EXISTING execution, never a second one
    9. dispatch: `LINKEDIN_COPY_AND_OPEN` never touches a send provider;
       `EMAIL_SEND` dispatches via `resolve_send_provider(mode)` — which,
       for `LIVE_EXTERNAL`, unconditionally raises
       `LiveExternalEmailSendDisabled` here, AFTER every gate above has
       already passed, proving the refusal is structural, not merely a
       policy verdict (D4).
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
        prospect_id=proposal.prospect_id,
        run_id=run.id,
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
    # ELIGIBLE). This is the deliberate D4 structural boundary: reached only
    # here, never earlier, and never bypassable by configuring anything.
    try:
        resolve_send_provider(Mode.LIVE)
    except LiveExternalEmailSendDisabled as exc:
        await actions.record_event(
            prospect_id=proposal.prospect_id,
            type="live_send_disabled",
            actor=body.actor,
            action_proposal_id=proposal.id,
            payload={"reason": str(exc)},
        )
        raise ActionDisabledError(str(exc), code=LiveExternalEmailSendDisabled.code) from exc
    # Unreachable in V2-H — `resolve_send_provider(Mode.LIVE)` always raises.
    raise AssertionError("unreachable: resolve_send_provider(Mode.LIVE) must always raise in V2-H")
