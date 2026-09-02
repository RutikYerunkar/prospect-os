"""Evaluation metrics — computed on read from a run's own persisted records
(IMPLEMENTATION_PLAN.md §16). No `evaluation_metrics` table, no hardcoded
demo numbers: every field here is a real aggregate over `runs`/`prospects`/
`evidence`/`icp_scores`/`contacts`/`outreach_drafts`/`review_results`/
`agent_tasks` rows for one run. A metric that can't be computed (no data
yet) is `None`, never a fabricated placeholder.
"""

from __future__ import annotations

from typing import Any

from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, verify_claim_evidence
from groundwork.domain.scoring import exclusion_status_from_persisted
from groundwork.engine.runner import Repos
from groundwork.models.enums import EvidenceOrigin, ExclusionEvaluation, ProspectStatus, SignalType
from groundwork.models.schemas import Evidence
from groundwork.timeutil import elapsed_seconds


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
        wall_clock_ms = elapsed_seconds(run_row.started_at, run_row.finished_at) * 1000

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

    llm_usage = await _compute_llm_usage(run_id, repos)
    search_quality = await _compute_search_quality(run_id, repos, score_rows=score_rows)

    return {
        "run_id": run_id,
        "volume": volume,
        "quality": quality,
        "reliability": reliability,
        "guardrails": guardrails,
        "llm_usage": llm_usage,
        "search_quality": search_quality,
    }


async def _compute_search_quality(run_id: str, repos: Any, *, score_rows: list) -> dict[str, Any]:
    """H1 Phase 16 — unambiguous source/quality definitions, computed on
    read from `source_documents`/`search_calls`/`icp_scores`. Several of
    these are legitimately zero/null in H1 (no live search has ever run
    against a real provider), but the metric *definitions* and computation
    exist now so H2 doesn't invent them under time pressure:

        result_occurrences        = every source_documents row for this run
        sources_retrieved_unique  = distinct is_winner=True rows
        sources_used_as_evidence  = winners whose evidence_id resolved to a
                                     real, persisted Evidence row
        source_utilization_rate   = sources_used_as_evidence /
                                     sources_retrieved_unique
        duplicate_retrieval_rate  = 1 - sources_retrieved_unique /
                                     result_occurrences
    """
    docs = await repos.search.source_documents_for_run(run_id)
    calls = await repos.search.search_calls_for_run(run_id)

    result_occurrences = len(docs)
    winners = [d for d in docs if d.is_winner]
    sources_retrieved_unique = len(winners)

    evidence_ids_persisted: set[str] = set()
    for e in await repos.prospect_data.evidence_for_run(run_id):
        evidence_ids_persisted.add(e.id)
    sources_used_as_evidence = sum(1 for w in winners if w.evidence_id and w.evidence_id in evidence_ids_persisted)

    source_utilization_rate = (
        sources_used_as_evidence / sources_retrieved_unique if sources_retrieved_unique else None
    )
    duplicate_retrieval_rate = (
        1 - (sources_retrieved_unique / result_occurrences) if result_occurrences else None
    )

    # Grounded profile coverage — read straight off the already-persisted
    # `dimensions` JSON (each entry carries `support`; H1 Phase 7). Counted
    # over non-duplicate prospects that reached a score at all.
    industry_grounded = 0
    size_grounded = 0
    for row in score_rows:
        for dim in row.dimensions:
            if dim.get("name") == "industry_fit" and dim.get("support") == "SUPPORTED":
                industry_grounded += 1
            if dim.get("name") == "size_fit" and dim.get("support") == "SUPPORTED":
                size_grounded += 1
    scored_count = len(score_rows)
    industry_grounded_coverage = industry_grounded / scored_count if scored_count else None
    employee_count_grounded_coverage = size_grounded / scored_count if scored_count else None
    # Reconstructed purely from persisted `ICPScoreRow` fields (H1
    # deviation-closure pass) — `exclusion_status_from_persisted` never
    # touches an in-memory `ICPScore`/`ProspectContext`, so this is exactly
    # what a fresh process reading the DB after a restart would compute.
    unevaluable_exclusion_count = sum(
        1
        for row in score_rows
        if exclusion_status_from_persisted(disqualified=row.disqualified, modifiers=row.modifiers)
        == ExclusionEvaluation.UNKNOWN
    )

    search_error_counts: dict[str, int] = {}
    latencies: list[float] = []
    retries = 0
    for c in calls:
        if c.error_type:
            search_error_counts[c.error_type] = search_error_counts.get(c.error_type, 0) + 1
        latencies.append(c.latency_ms)
        if c.attempt_kind == "transport_retry":
            retries += 1

    # H2 Phase 16: `search_cost_usd` sums only once EVERY contributing call
    # has a non-null cost — the same completeness rule `_compute_llm_usage`
    # applies, never a partial sum presented as complete. `search_credits_
    # used` is the provider-native usage figure, summed independently
    # (defaulting missing values to 0, like token counts) so real usage is
    # visible even when no trustworthy USD rate is configured.
    costs = [c.cost_usd for c in calls]
    search_cost_usd = sum(costs) if costs and all(cost is not None for cost in costs) else None
    credits = [c.credits_used for c in calls if c.credits_used is not None]
    search_credits_used = sum(credits) if credits else None

    extraction_calls = [c for c in calls if c.operation == "extract"]
    partial_extractions = sum(1 for c in extraction_calls if c.status == "PARTIAL_EXTRACTION")
    failed_source_count = sum(1 for d in docs if d.status in ("failed", "partial"))

    # H2 Phase 18/19 — discovery-only counters, sourced from the same
    # `run_events` SSE log `engine/discovery.py` already writes narrative
    # entries to (never a second telemetry table for something this
    # lightweight; the events are real progress items a viewer could also
    # see in the Activity Stream).
    discovery_rejection_reasons: dict[str, int] = {}
    domain_resolution_method_counts: dict[str, int] = {}
    events = await repos.events.after(run_id, 0)
    for event in events:
        if event.type == "discovery.candidate_rejected":
            reason = (event.payload or {}).get("reason", "unknown")
            discovery_rejection_reasons[reason] = discovery_rejection_reasons.get(reason, 0) + 1
        elif event.type == "discovery.domain_resolved":
            method = (event.payload or {}).get("method", "unknown")
            domain_resolution_method_counts[method] = domain_resolution_method_counts.get(method, 0) + 1

    return {
        "result_occurrences": result_occurrences,
        "sources_retrieved_unique": sources_retrieved_unique,
        "sources_used_as_evidence": sources_used_as_evidence,
        "source_utilization_rate": source_utilization_rate,
        "duplicate_retrieval_rate": duplicate_retrieval_rate,
        "industry_grounded_coverage": industry_grounded_coverage,
        "employee_count_grounded_coverage": employee_count_grounded_coverage,
        "unevaluable_exclusion_count": unevaluable_exclusion_count,
        "search_calls": len(calls),
        "search_retries": retries,
        "search_error_counts": search_error_counts,
        "p50_search_latency_ms": _percentile(latencies, 0.5),
        "p95_search_latency_ms": _percentile(latencies, 0.95),
        "search_cost_usd": search_cost_usd,
        "search_credits_used": search_credits_used,
        "extraction_calls": len(extraction_calls),
        "partial_extractions": partial_extractions,
        "failed_or_partial_sources": failed_source_count,
        "discovery_rejection_reasons": discovery_rejection_reasons,
        "domain_resolution_method_counts": domain_resolution_method_counts,
    }


async def _compute_llm_usage(run_id: str, repos: Any) -> dict[str, Any]:
    """Computed-on-read from `llm_calls` (Checkpoint G Phase 8) — one row
    per provider *attempt*; a "logical call" is a distinct `call_group_id`.
    `estimated_cost_usd` is `None` whenever ANY contributing attempt lacks a
    computed cost (i.e. pricing wasn't configured for at least part of the
    run) — never a partially-summed number presented as complete."""
    calls = await repos.llm_calls.for_run(run_id)
    if not calls:
        return {
            "logical_calls": 0, "provider_attempts": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "reasoning_tokens": None, "estimated_cost_usd": None, "by_operation": {}, "by_status": {},
            "transport_retries": 0, "schema_repairs": 0, "budget_tripped": False,
        }

    call_groups = {c.call_group_id for c in calls}
    tokens_in = sum(c.tokens_in for c in calls)
    tokens_out = sum(c.tokens_out for c in calls)
    reasoning = [c.reasoning_tokens for c in calls if c.reasoning_tokens is not None]
    costs = [c.cost_usd for c in calls]
    estimated_cost = sum(costs) if costs and all(c is not None for c in costs) else None

    by_operation: dict[str, dict[str, int]] = {}
    for c in calls:
        bucket = by_operation.setdefault(c.operation, {"attempts": 0, "tokens_in": 0, "tokens_out": 0})
        bucket["attempts"] += 1
        bucket["tokens_in"] += c.tokens_in
        bucket["tokens_out"] += c.tokens_out

    by_status: dict[str, int] = {}
    for c in calls:
        by_status[c.status] = by_status.get(c.status, 0) + 1

    return {
        "logical_calls": len(call_groups),
        "provider_attempts": len(calls),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_total": tokens_in + tokens_out,
        "reasoning_tokens": sum(reasoning) if reasoning else None,
        "estimated_cost_usd": estimated_cost,
        "by_operation": by_operation,
        "by_status": by_status,
        "transport_retries": sum(1 for c in calls if c.attempt_kind == "transport_retry"),
        "schema_repairs": sum(1 for c in calls if c.attempt_kind == "schema_repair"),
        "budget_tripped": any(c.status == "NOT_ATTEMPTED_BUDGET" for c in calls),
    }
