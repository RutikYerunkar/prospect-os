"""GET /prospects/{id} (the full aggregate) and approve/reject.

Approve/reject are state transitions only (§21, "Human approval boundary"):
they insert one `approvals` row and return the refreshed aggregate. Nothing
here sends email, hits LinkedIn, or calls a webhook — there is no such
provider wired into this module at all, so there is nothing to accidentally
trigger.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from groundwork.api.deps import ApprovalsRepoDep, IsOperatorDep, ReposDep
from groundwork.api.errors import ConflictError, NotFoundError
from groundwork.api.live_gate import enforce_live_gate
from groundwork.api.schemas import ApprovalInfo, ApproveRequest, ProspectAggregate, RejectRequest
from groundwork.models.enums import ProspectStatus

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
