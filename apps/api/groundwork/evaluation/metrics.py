"""Evaluation metrics — computed on read from a run's own persisted records
(IMPLEMENTATION_PLAN.md §16). No `evaluation_metrics` table, no hardcoded
demo numbers: every field here is a real aggregate over `runs`/`prospects`/
`evidence`/`icp_scores`/`contacts`/`outreach_drafts`/`review_results`/
`agent_tasks` rows for one run. A metric that can't be computed (no data
yet) is `None`, never a fabricated placeholder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, verify_claim_evidence
from groundwork.engine.runner import Repos
from groundwork.models.enums import EvidenceOrigin, ProspectStatus, SignalType
from groundwork.models.schemas import Evidence


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * pct
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _evidence_row_to_model(row: Any) -> Evidence:
    return Evidence(
        id=row.id,
        prospect_id=row.prospect_id,
        source_url=row.source_url,
        source_ref=row.source_ref,
        source_provider=row.source_provider,
        title=row.title,
        claim=row.claim,
        snippet=row.snippet,
        signal_type=SignalType(row.signal_type) if row.signal_type else None,
        retrieved_at=row.retrieved_at,
        confidence=row.confidence,
        origin=EvidenceOrigin(row.origin),
    )


async def compute_run_evaluation(run_id: str, repos: Repos) -> dict[str, Any]:
    run_row = await repos.runs.get(run_id)
    prospects = await repos.prospects.list_for_run(run_id)
    evidence_rows = await repos.prospect_data.evidence_for_run(run_id)
    score_rows = await repos.prospect_data.scores_for_run(run_id)
    contact_rows = await repos.prospect_data.contacts_for_run(run_id)
    draft_rows = await repos.prospect_data.drafts_for_run(run_id)
    review_rows = await repos.prospect_data.reviews_for_run(run_id)
    task_rows = await repos.tasks.for_run(run_id)

    # --- volume ---
    status_counts: dict[str, int] = {}
    for p in prospects:
        status_counts[p.status] = status_counts.get(p.status, 0) + 1

    non_duplicate = [p for p in prospects if p.status != ProspectStatus.DUPLICATE.value]

    evidence_by_prospect: dict[str, list] = {}
    for row in evidence_rows:
        evidence_by_prospect.setdefault(row.prospect_id, []).append(row)

    researched = sum(1 for p in non_duplicate if evidence_by_prospect.get(p.id))

    volume = {
        "discovered": len(prospects),
        "duplicated": status_counts.get(ProspectStatus.DUPLICATE.value, 0),
        "researched": researched,
        "qualified": status_counts.get(ProspectStatus.PASS.value, 0),
        "needs_review": status_counts.get(ProspectStatus.NEEDS_REVIEW.value, 0),
        "rejected": status_counts.get(ProspectStatus.REJECTED.value, 0),
        "failed": (
            status_counts.get(ProspectStatus.FAILED.value, 0)
            + status_counts.get(ProspectStatus.TIMED_OUT.value, 0)
        ),
        "by_status": status_counts,
    }

    # --- quality ---
    with_enough_evidence = sum(1 for p in non_duplicate if len(evidence_by_prospect.get(p.id, [])) >= 3)
    evidence_coverage = with_enough_evidence / len(non_duplicate) if non_duplicate else None

    total_claims = 0
    grounded_claims = 0
    for draft in draft_rows:
        prospect_evidence = evidence_by_prospect.get(draft.prospect_id, [])
        evidence_by_id = {row.id: _evidence_row_to_model(row) for row in prospect_evidence}
        for entry in draft.claim_map:
            total_claims += 1
            evidence_ids = entry.get("evidence_ids") or []
            grounded = bool(evidence_ids) and all(
                verify_claim_evidence(
                    entry.get("sentence", ""), eid, evidence_by_id, draft.prospect_id, DEFAULT_OVERLAP_THRESHOLD
                )
                for eid in evidence_ids
            )
            if grounded:
                grounded_claims += 1

    grounded_claim_rate = grounded_claims / total_claims if total_claims else None
    unsupported_claim_count = total_claims - grounded_claims

    dim_total = 0
    dim_supported = 0
    overall_scores: list[float] = []
    confidences: list[float] = []
    for row in score_rows:
        overall_scores.append(row.overall)
        confidences.append(row.confidence)
        for dim in row.dimensions:
            dim_total += 1
            if not dim.get("unsupported", False):
                dim_supported += 1

    dimension_support_rate = dim_supported / dim_total if dim_total else None
    mean_icp_score = sum(overall_scores) / len(overall_scores) if overall_scores else None
    mean_confidence = sum(confidences) / len(confidences) if confidences else None

    contact_breakdown: dict[str, int] = {}
    for row in contact_rows:
        contact_breakdown[row.verification] = contact_breakdown.get(row.verification, 0) + 1

    provenance_mix: dict[str, int] = {}
    for row in evidence_rows:
        provenance_mix[row.origin] = provenance_mix.get(row.origin, 0) + 1

    quality = {
        "evidence_coverage": evidence_coverage,
        "grounded_claim_rate": grounded_claim_rate,
        "dimension_support_rate": dimension_support_rate,
        "unsupported_claim_count": unsupported_claim_count,
        "contact_verification_breakdown": contact_breakdown,
        "mean_icp_score": mean_icp_score,
        "mean_confidence": mean_confidence,
        "provenance_mix": provenance_mix,
    }

    # --- reliability ---
    step_status_counts: dict[str, int] = {}
    durations: list[float] = []
    error_counts: dict[str, int] = {}
    for t in task_rows:
        step_status_counts[t.status] = step_status_counts.get(t.status, 0) + 1
        if t.status == "OK":
            durations.append(t.duration_ms)
        if t.error_type:
            error_counts[t.error_type] = error_counts.get(t.error_type, 0) + 1

    wall_clock_ms: float | None = None
    if run_row is not None and run_row.started_at is not None:
        end = run_row.finished_at or datetime.utcnow()
        wall_clock_ms = (end - run_row.started_at).total_seconds() * 1000

    # Per-step success rate — a step "succeeds" if any attempt for it reached
    # OK (a step that retried then succeeded still counts as a success for
    # that prospect, same as the engine's own outcome), computed over the
    # distinct (prospect, step) pairs rather than raw attempt rows so a
    # 3-attempt retry sequence doesn't dilute the rate.
    step_pairs_total: dict[str, set[str]] = {}
    step_pairs_ok: dict[str, set[str]] = {}
    for t in task_rows:
        step_pairs_total.setdefault(t.step_name, set()).add(t.prospect_id)
        if t.status == "OK":
            step_pairs_ok.setdefault(t.step_name, set()).add(t.prospect_id)
    per_step_success_rate = {
        step: len(step_pairs_ok.get(step, set())) / len(prospect_ids)
        for step, prospect_ids in step_pairs_total.items()
    }

    reliability = {
        "step_status_counts": step_status_counts,
        "total_retries": step_status_counts.get("RETRY", 0),
        "p50_step_duration_ms": _percentile(durations, 0.5),
        "p95_step_duration_ms": _percentile(durations, 0.95),
        "run_wall_clock_ms": wall_clock_ms,
        "provider_error_counts": error_counts,
        "per_step_success_rate": per_step_success_rate,
    }

    # --- guardrails ---
    check_pass_counts: dict[str, int] = {}
    check_total_counts: dict[str, int] = {}
    check_failed_prospects: dict[str, list[str]] = {}
    for review in review_rows:
        for check in review.checks:
            cid = check["id"]
            check_total_counts[cid] = check_total_counts.get(cid, 0) + 1
            if check["passed"]:
                check_pass_counts[cid] = check_pass_counts.get(cid, 0) + 1
            else:
                check_failed_prospects.setdefault(cid, []).append(review.prospect_id)

    guardrails = [
        {
            "id": cid,
            "passed": check_pass_counts.get(cid, 0),
            "total": total,
            "pass_rate": check_pass_counts.get(cid, 0) / total,
            "failed_prospect_ids": check_failed_prospects.get(cid, []),
        }
        for cid, total in check_total_counts.items()
    ]

    return {
        "run_id": run_id,
        "volume": volume,
        "quality": quality,
        "reliability": reliability,
        "guardrails": guardrails,
    }
