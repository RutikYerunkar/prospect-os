"""GET /prospects/{id} (the full aggregate) and approve/reject.

Approve/reject are state transitions only (§21, "Human approval boundary"):
they insert one `approvals` row and return the refreshed aggregate. Nothing
here sends email, hits LinkedIn, or calls a webhook — there is no such
provider wired into this module at all, so there is nothing to accidentally
trigger.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request

from groundwork.api.deps import ApprovalsRepoDep, IsOperatorDep, ReposDep
from groundwork.api.errors import ConflictError, NotFoundError
from groundwork.api.live_gate import enforce_live_gate
from groundwork.api.schemas import ApprovalInfo, ApproveRequest, ProspectAggregate, RejectRequest
from groundwork.domain.action_policy import (
    ENRICHMENT_STALE_AFTER_DAYS_DEFAULT,
    derive_preserved_enrichment_state,
    is_enrichment_stale,
)
from groundwork.models.enums import Channel, ProspectStatus
from groundwork.timeutil import utcnow

router = APIRouter(prefix="/api/prospects", tags=["prospects"])

# A prospect can only be decided once the pipeline has produced a verdict for
# a human to weigh in on. DUPLICATE/FAILED/TIMED_OUT never reached a review
# verdict; PENDING/RUNNING haven't finished yet.
_DECIDABLE_STATUSES = {ProspectStatus.PASS.value, ProspectStatus.NEEDS_REVIEW.value, ProspectStatus.REJECTED.value}


def _evidence_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_url": row.source_url,
        "source_ref": row.source_ref,
        "source_provider": row.source_provider,
        "title": row.title,
        "claim": row.claim,
        "snippet": row.snippet,
        "signal_type": row.signal_type,
        "retrieved_at": row.retrieved_at.isoformat() if row.retrieved_at else None,
        "confidence": row.confidence,
        "origin": row.origin,
    }


def _signal_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "type": row.type,
        "summary": row.summary,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "confidence": row.confidence,
        "evidence_ids": row.evidence_ids,
        "grounded": row.grounded,
    }


def _score_dict(row) -> dict[str, Any]:
    return {
        "overall": row.overall,
        "dimensions": row.dimensions,
        "modifiers": row.modifiers,
        "disqualified": row.disqualified,
        "explanation": row.explanation,
        "confidence": row.confidence,
        "rubric_version": row.rubric_version,
        "computed_at": row.computed_at.isoformat(),
    }


def _contact_dict(row) -> dict[str, Any]:
    return {
        "full_name": row.full_name,
        "title": row.title,
        "persona": row.persona,
        "linkedin_url": row.linkedin_url,
        "email": row.email,
        "verification": row.verification,
        "evidence_ids": row.evidence_ids,
    }


def _draft_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel": row.channel,
        "step_index": row.step_index,
        "subject": row.subject,
        "body": row.body,
        "claim_map": row.claim_map,
        "version": row.version,
        "status": row.status,
    }


def _contact_channel_dict(
    row,
    *,
    now: datetime,
    latest_enrichment_observed_at: datetime | None,
    enrichment_by_id: dict[str, Any],
) -> dict[str, Any]:
    """V2-E, §5/§6: additive-only fields layered on top of the v2-C shape —
    every field already returned above this comment is unchanged. The new
    fields are the approved provider-neutral/read-only set: `origin`/
    `provider` (from the `contact_enrichments` row this state derives from,
    never the provider's raw payload), `stale`/`stale_after_days` (existing
    `domain/action_policy.py` staleness semantics, reused not re-derived),
    `preserved_state` (§8, `domain/action_policy.py::
    derive_preserved_enrichment_state`), and `provider_confidence`/
    `is_catch_all` for the email channel only — observations, never part of
    any state's explanation text (that discipline lives in the frontend).
    Deliberately NOT exposed: `email_provider_status`, any raw payload/
    digest, `provider_person_id`, or raw provider error text."""
    enrichment = enrichment_by_id.get(row.derived_from_enrichment_id) if row.derived_from_enrichment_id else None
    is_email = row.channel == Channel.EMAIL.value
    preserved_state = derive_preserved_enrichment_state(
        discovery_state=row.discovery_state,
        identifier=row.identifier,
        observed_at=row.observed_at,
        last_attempt_status=row.last_attempt_status,
        latest_enrichment_observed_at=latest_enrichment_observed_at,
    )
    return {
        "channel": row.channel,
        "identifier": row.identifier,
        "discovery_state": row.discovery_state,
        "verification_state": row.verification_state,
        "identity_match_state": row.identity_match_state,
        "derivation_version": row.derivation_version,
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
        "last_attempt_at": row.last_attempt_at.isoformat() if row.last_attempt_at else None,
        "last_attempt_status": row.last_attempt_status,
        "last_attempt_error_type": row.last_attempt_error_type,
        "origin": enrichment.origin if enrichment else None,
        "provider": enrichment.provider if enrichment else None,
        "stale": is_enrichment_stale(row.observed_at, now, ENRICHMENT_STALE_AFTER_DAYS_DEFAULT),
        "stale_after_days": ENRICHMENT_STALE_AFTER_DAYS_DEFAULT,
        "preserved_state": preserved_state.value if preserved_state else None,
        "provider_confidence": enrichment.email_provider_confidence if enrichment and is_email else None,
        "is_catch_all": enrichment.email_is_catch_all if enrichment and is_email else None,
    }


def _review_dict(row) -> dict[str, Any]:
    return {
        "verdict": row.verdict,
        "checks": row.checks,
        "reasons": row.reasons,
        "reviewed_at": row.reviewed_at.isoformat(),
    }


def _task_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "step_name": row.step_name,
        "attempt": row.attempt,
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "duration_ms": row.duration_ms,
        "model": row.model,
        "provider": row.provider,
        "tokens_in": row.tokens_in,
        "tokens_out": row.tokens_out,
        "error_type": row.error_type,
        "error_message": row.error_message,
        "evidence_count": row.evidence_count,
    }


async def _get_prospect_and_enforce_gate(prospect_id: str, request: Request, repos: ReposDep, is_operator: bool):
    """Checkpoint I1 Phase 8: a prospect has no `mode` field of its own —
    it inherits its run's. 404s before the gate check (existence isn't a
    secret; the run's live-ness is what's protected) so a nonexistent
    prospect always reads as "not found," never as "unauthorized," for
    both operators and anonymous callers alike."""
    prospect = await repos.prospects.get(prospect_id)
    if prospect is None:
        raise NotFoundError(f"no prospect with id {prospect_id!r}")
    run_row = await repos.runs.get(prospect.run_id)
    enforce_live_gate(request, run_row.mode if run_row is not None else "demo", is_operator)
    return prospect


async def _load_aggregate(prospect_id: str, repos: ReposDep, approvals: ApprovalsRepoDep) -> ProspectAggregate:
    prospect = await repos.prospects.get(prospect_id)
    if prospect is None:
        raise NotFoundError(f"no prospect with id {prospect_id!r}")

    company = await repos.companies.get(prospect.company_id)
    evidence = await repos.prospect_data.get_evidence(prospect_id)
    signals = await repos.prospect_data.get_signals(prospect_id)
    score = await repos.prospect_data.get_score(prospect_id)
    contact = await repos.prospect_data.get_contact(prospect_id)
    drafts = await repos.prospect_data.get_drafts(prospect_id)
    review = await repos.prospect_data.get_review(prospect_id)
    trace = await repos.tasks.for_prospect(prospect_id)
    approval = await approvals.latest_for_prospect(prospect_id)
    contact_channels = await repos.contact_enrichment.get_contact_channels(prospect_id)
    enrichments = await repos.contact_enrichment.get_contact_enrichments(prospect_id)
    enrichment_by_id = {e.id: e for e in enrichments}
    latest_enrichment_observed_at = max((e.observed_at for e in enrichments), default=None)
    now = utcnow()

    company_dict: dict[str, Any] = {}
    if company is not None:
        company_dict = {
            "id": company.id,
            "canonical_domain": company.canonical_domain,
            "display_name": company.display_name,
            **company.profile,
        }

    return ProspectAggregate(
        id=prospect.id,
        run_id=prospect.run_id,
        company=company_dict,
        dedupe_key=prospect.dedupe_key,
        duplicate_of=prospect.duplicate_of,
        stage=prospect.current_stage,
        status=prospect.status,
        error=prospect.error,
        evidence=[_evidence_dict(e) for e in evidence],
        signals=[_signal_dict(s) for s in signals],
        score=_score_dict(score) if score else None,
        contact=_contact_dict(contact) if contact else None,
        drafts=[_draft_dict(d) for d in drafts],
        review=_review_dict(review) if review else None,
        trace=[_task_dict(t) for t in trace],
        contact_channels=[
            _contact_channel_dict(
                c, now=now, latest_enrichment_observed_at=latest_enrichment_observed_at,
                enrichment_by_id=enrichment_by_id,
            )
            for c in contact_channels
        ],
        approval=ApprovalInfo(
            state=approval.decision if approval else "PENDING",
            actor=approval.actor if approval else None,
            reason=approval.reason if approval else None,
            decided_at=approval.decided_at if approval else None,
        ),
    )


@router.get("/{prospect_id}", response_model=ProspectAggregate)
async def get_prospect(
    prospect_id: str, request: Request, repos: ReposDep, approvals: ApprovalsRepoDep, is_operator: IsOperatorDep
) -> ProspectAggregate:
    await _get_prospect_and_enforce_gate(prospect_id, request, repos, is_operator)
    return await _load_aggregate(prospect_id, repos, approvals)


@router.post("/{prospect_id}/approve", response_model=ProspectAggregate)
async def approve_prospect(
    prospect_id: str,
    body: ApproveRequest,
    request: Request,
    repos: ReposDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ProspectAggregate:
    prospect = await _get_prospect_and_enforce_gate(prospect_id, request, repos, is_operator)
    if prospect.status not in _DECIDABLE_STATUSES:
        raise ConflictError(
            f"prospect {prospect_id!r} is {prospect.status}; "
            "only a PASS/NEEDS_REVIEW/REJECTED prospect can be approved"
        )
    await approvals.create(prospect_id=prospect_id, decision="APPROVED", actor=body.actor)
    return await _load_aggregate(prospect_id, repos, approvals)


@router.post("/{prospect_id}/reject", response_model=ProspectAggregate)
async def reject_prospect(
    prospect_id: str,
    body: RejectRequest,
    request: Request,
    repos: ReposDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> ProspectAggregate:
    prospect = await _get_prospect_and_enforce_gate(prospect_id, request, repos, is_operator)
    if prospect.status not in _DECIDABLE_STATUSES:
        raise ConflictError(
            f"prospect {prospect_id!r} is {prospect.status}; "
            "only a PASS/NEEDS_REVIEW/REJECTED prospect can be rejected"
        )
    await approvals.create(prospect_id=prospect_id, decision="REJECTED", actor=body.actor, reason=body.reason)
    return await _load_aggregate(prospect_id, repos, approvals)
