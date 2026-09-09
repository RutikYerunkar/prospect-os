"""Evaluation metrics — computed on read from a run's own persisted records
(IMPLEMENTATION_PLAN.md §16). No `evaluation_metrics` table, no hardcoded
demo numbers: every field here is a real aggregate over `runs`/`prospects`/
`evidence`/`icp_scores`/`contacts`/`outreach_drafts`/`review_results`/
`agent_tasks` rows for one run. A metric that can't be computed (no data
yet) is `None`, never a fabricated placeholder.
"""

from __future__ import annotations

from typing import Any

from groundwork.domain.action_policy import derive_preserved_enrichment_state, is_enrichment_stale
from groundwork.domain.contact_identity import IdentifierVerdict, validate_linkedin_identifier
from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, verify_claim_evidence
from groundwork.domain.scoring import exclusion_status_from_persisted
from groundwork.engine.runner import Repos
from groundwork.models.enums import (
    Channel,
    EmailDiscoveryState,
    EmailVerificationState,
    EnrichmentOrigin,
    EvidenceOrigin,
    ExclusionEvaluation,
    LinkedInResolutionState,
    ProspectStatus,
    SignalType,
)
from groundwork.models.schemas import Evidence
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.timeutil import elapsed_seconds, ensure_aware, utcnow

# V2-J §3 — the recipient-conflict blocked-reason vocabulary (§6.1 clause 12,
# `domain/action_policy.py::_RECIPIENT_CONFLICT_REASONS`'s value set),
# reused here (never re-typed as a second literal list) so the cross-run
# recipient metric recognizes exactly the same reasons the real policy emits.
_RECIPIENT_CONFLICT_REASON_STRINGS = frozenset(
    {"already_sent_to_recipient", "send_in_flight", "prior_send_uncertain"}
)


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


async def compute_run_evaluation(
    run_id: str, repos: Repos, *, actions: ActionRepository, approvals: ApprovalRepository
) -> dict[str, Any]:
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
    prospect_ids = [p.id for p in prospects]
    enrichment = await _compute_enrichment_metrics(run_id, repos, prospect_ids=prospect_ids)
    actions_metrics = await _compute_action_metrics(run_id, actions=actions, approvals=approvals)

    return {
        "run_id": run_id,
        "volume": volume,
        "quality": quality,
        "reliability": reliability,
        "guardrails": guardrails,
        "llm_usage": llm_usage,
        "search_quality": search_quality,
        "enrichment": enrichment,
        "actions": actions_metrics,
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


async def _compute_enrichment_metrics(run_id: str, repos: Any, *, prospect_ids: list[str]) -> dict[str, Any]:
    """V2-J §1 — computed on read from `enrichment_calls` (run-scoped) and
    `contact_channels`/`contact_enrichments` (prospect-scoped, gathered here
    for this run's own prospects only). No new table, no persistence.

    Grain: `attempted`/`matched`/`match_rate` are call-level — one call
    GROUP (`enrichment_calls.call_group_id`) is one enrichment attempt,
    mirroring `_compute_llm_usage`'s `logical_calls`. A group whose only
    telemetry row is `NOT_ATTEMPTED_BUDGET` (the run's `EnrichmentCallBudget`
    was already exhausted — the provider was never actually called) is
    counted in `not_attempted_budget_count`, never in `attempted` — this is
    the load-bearing "CONTRIB excludes NOT_ATTEMPTED_BUDGET" rule extended to
    the funnel counters, not just credits/cost below.

    The FOUND/VERIFIED/RESOLVED/identity-match rates are read from
    `contact_channels` — the already-derived, provider-agnostic CURRENT
    state (§3.6 last-known-good) — never re-derived from a raw provider
    status word here (that would require importing a provider's own
    `email_status_map`, which `domain`/`evaluation` must never do, D2).
    `catch_all_rate` and `identifier_grammar_rejections` are the two
    exceptions: `email_is_catch_all` is already a plain, provider-neutral
    boolean on `contact_enrichments`, and LinkedIn grammar validation is a
    pure `domain/contact_identity.py` re-check of the raw observed URL —
    both computed straight from the raw observation rows, not the channel
    snapshot.
    """
    channels = await repos.contact_enrichment.contact_channels_for_prospects(prospect_ids)
    enrichments = await repos.contact_enrichment.contact_enrichments_for_prospects(prospect_ids)
    calls = await repos.contact_enrichment.enrichment_calls_for_run(run_id)

    groups: dict[str, list] = {}
    for c in calls:
        groups.setdefault(c.call_group_id, []).append(c)

    not_attempted_budget_count = 0
    attempted = 0
    for group_calls in groups.values():
        last = max(group_calls, key=lambda c: c.attempt)
        if last.status == "NOT_ATTEMPTED_BUDGET":
            not_attempted_budget_count += 1
        else:
            attempted += 1

    matched = sum(1 for e in enrichments if e.matched)
    match_rate = matched / attempted if attempted else None

    enrichment_attempts_by_status: dict[str, int] = {}
    for c in calls:
        enrichment_attempts_by_status[c.status] = enrichment_attempts_by_status.get(c.status, 0) + 1

    # CONTRIB — every attempt row EXCEPT NOT_ATTEMPTED_BUDGET (never a real
    # provider call); latency/credits/cost/provider-error-rate below are all
    # computed over this same set, so "contributing" means one thing
    # throughout this function.
    contributing_calls = [c for c in calls if c.status != "NOT_ATTEMPTED_BUDGET"]
    provider_error_rate = (
        sum(1 for c in contributing_calls if c.status == "PROVIDER_ERROR") / len(contributing_calls)
        if contributing_calls
        else None
    )

    email_channels = [c for c in channels if c.channel == Channel.EMAIL.value]
    linkedin_channels = [c for c in channels if c.channel == Channel.LINKEDIN.value]

    email_found = sum(1 for c in email_channels if c.discovery_state == EmailDiscoveryState.FOUND.value)
    email_found_rate = email_found / attempted if attempted else None

    email_verified = sum(
        1
        for c in email_channels
        if c.discovery_state == EmailDiscoveryState.FOUND.value
        and c.verification_state == EmailVerificationState.VERIFIED.value
    )
    # Pinned semantics (V2-J test matrix): the denominator is FOUND, not
    # `attempted` — "what fraction of the emails we actually found turned
    # out to be verified," not diluted by prospects with no email at all.
    email_verified_rate = email_verified / email_found if email_found else None

    linkedin_resolved = sum(
        1 for c in linkedin_channels if c.discovery_state == LinkedInResolutionState.RESOLVED.value
    )
    linkedin_resolved_rate = linkedin_resolved / attempted if attempted else None

    identity_match_distribution: dict[str, int] = {}
    for c in linkedin_channels:
        if c.identity_match_state:
            identity_match_distribution[c.identity_match_state] = (
                identity_match_distribution.get(c.identity_match_state, 0) + 1
            )

    # catch_all_rate — NULL (`email_is_catch_all is None`, e.g. the
    # provider's own status word wasn't mapped to a catch-all signal at all)
    # is excluded from BOTH numerator and denominator, never treated as
    # "not catch-all."
    catch_all_known = [
        bool(e.email_is_catch_all) for e in enrichments if e.email_address and e.email_is_catch_all is not None
    ]
    catch_all_rate = (sum(catch_all_known) / len(catch_all_known)) if catch_all_known else None

    identifier_grammar_rejections = 0
    for e in enrichments:
        if e.linkedin_url:
            verdict = validate_linkedin_identifier(e.linkedin_url, origin=EnrichmentOrigin(e.origin))
            if verdict is IdentifierVerdict.REJECTED:
                identifier_grammar_rejections += 1

    now = utcnow()
    latest_observed_by_prospect: dict[str, Any] = {}
    for e in enrichments:
        observed = ensure_aware(e.observed_at)
        if observed is None:
            continue
        prior = latest_observed_by_prospect.get(e.prospect_id)
        if prior is None or observed > prior:
            latest_observed_by_prospect[e.prospect_id] = observed

    # stale vs never-observed (pinned): only a channel that WAS observed at
    # least once and has since aged past the threshold counts as stale — a
    # channel that was never successfully observed (`observed_at is None`)
    # is simply not-yet-attempted, not stale.
    stale_channel_count = 0
    preserved_last_known_good_count = 0
    preserved_last_known_good_breakdown: dict[str, int] = {}
    for c in channels:
        observed_at = ensure_aware(c.observed_at)
        if observed_at is not None and is_enrichment_stale(observed_at, now):
            stale_channel_count += 1
        preserved = derive_preserved_enrichment_state(
            discovery_state=c.discovery_state,
            identifier=c.identifier,
            observed_at=observed_at,
            last_attempt_status=c.last_attempt_status,
            latest_enrichment_observed_at=latest_observed_by_prospect.get(c.prospect_id),
        )
        if preserved is not None:
            preserved_last_known_good_count += 1
            preserved_last_known_good_breakdown[preserved.value] = (
                preserved_last_known_good_breakdown.get(preserved.value, 0) + 1
            )

    latencies = [c.latency_ms for c in contributing_calls]

    # Credits/cost completeness (pinned) — CONTRIB excludes
    # NOT_ATTEMPTED_BUDGET (already true of `contributing_calls`); beyond
    # that: no contributing calls -> None; any contributing cost/credits
    # unknown -> None; multiple distinct provider credit UNITS -> None
    # (Apollo credits and Hunter credits are not comparable/summable) —
    # never a partial numeric total presented as complete.
    costs = [c.cost_usd for c in contributing_calls]
    enrichment_cost_usd = sum(costs) if contributing_calls and all(v is not None for v in costs) else None

    providers_seen = {c.provider for c in contributing_calls}
    credits = [c.credits_used for c in contributing_calls]
    if not contributing_calls or len(providers_seen) != 1 or any(v is None for v in credits):
        enrichment_credits_used = None
    else:
        enrichment_credits_used = sum(credits)

    return {
        "attempted": attempted,
        "matched": matched,
        "match_rate": match_rate,
        "email_found_rate": email_found_rate,
        "email_verified_rate": email_verified_rate,
        "catch_all_rate": catch_all_rate,
        "linkedin_resolved_rate": linkedin_resolved_rate,
        "identity_match_distribution": identity_match_distribution,
        "identifier_grammar_rejections": identifier_grammar_rejections,
        "provider_error_rate": provider_error_rate,
        "not_attempted_budget_count": not_attempted_budget_count,
        "enrichment_attempts_by_status": enrichment_attempts_by_status,
        "stale_channel_count": stale_channel_count,
        "preserved_last_known_good_count": preserved_last_known_good_count,
        "preserved_last_known_good_breakdown": preserved_last_known_good_breakdown,
        "p50_enrichment_latency_ms": _percentile(latencies, 0.5),
        "p95_enrichment_latency_ms": _percentile(latencies, 0.95),
        "enrichment_credits_used": enrichment_credits_used,
        "enrichment_cost_usd": enrichment_cost_usd,
        "enrichment_calls": len(contributing_calls),
    }


async def _compute_action_metrics(run_id: str, *, actions: ActionRepository, approvals: ApprovalRepository) -> dict[str, Any]:
    """V2-J §2/§3 — governed-action observability, computed on read from
    `action_proposals`/`action_executions`/`action_events`/`approvals`.
    Proposal-time blocked reasons (`blocked_reasons`, from each proposal's
    own persisted `policy_verdict`/`blocked_reasons`) are kept strictly
    separate from execution-time blocked reasons (`execution_blocked_reasons`,
    from `action_events` rows of type `"execution_blocked"` — both the five
    pre-policy 409 paths and the policy-block path emit exactly this event
    type, so this metric sees all six with no special-casing).
    """
    proposals = await actions.proposals_for_run(run_id)
    executions = await actions.executions_for_run(run_id)
    proposal_ids = [p.id for p in proposals]
    events = await actions.events_for_proposals(proposal_ids)

    proposals_by_verdict: dict[str, int] = {}
    blocked_reasons: dict[str, int] = {}
    for p in proposals:
        proposals_by_verdict[p.policy_verdict] = proposals_by_verdict.get(p.policy_verdict, 0) + 1
        for reason in p.blocked_reasons:
            blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1

    execution_blocked_events = [e for e in events if e.type == "execution_blocked"]
    execution_blocked_reasons: dict[str, int] = {}
    for e in execution_blocked_events:
        for reason in (e.payload or {}).get("blocked_reasons", []):
            execution_blocked_reasons[reason] = execution_blocked_reasons.get(reason, 0) + 1
    execution_blocked_attempts = len(execution_blocked_events)
    execution_blocked_proposals = len(
        {e.action_proposal_id for e in execution_blocked_events if e.action_proposal_id}
    )
    content_hash_mismatch_count = sum(
        1
        for e in execution_blocked_events
        if "content_changed" in (e.payload or {}).get("blocked_reasons", [])
    )

    executions_by_status: dict[str, int] = {}
    executions_by_origin: dict[str, int] = {}
    for x in executions:
        executions_by_status[x.status] = executions_by_status.get(x.status, 0) + 1
        executions_by_origin[x.origin] = executions_by_origin.get(x.origin, 0) + 1
    uncertain_count = executions_by_status.get("UNCERTAIN", 0)

    approvals_by_id = await approvals.get_many([x.approval_id for x in executions if x.approval_id])
    latencies_ms: list[float] = []
    for x in executions:
        approval = approvals_by_id.get(x.approval_id) if x.approval_id else None
        if approval is None or approval.decided_at is None or x.claimed_at is None:
            continue
        latencies_ms.append(max(elapsed_seconds(approval.decided_at, x.claimed_at) * 1000, 0.0))

    reconciliation_outcomes: dict[str, int] = {}
    messages_scanned_values: list[int] = []
    for e in events:
        payload = e.payload or {}
        if e.type == "execution_reconciled":
            outcome = payload.get("result", "FOUND")
        elif e.type == "execution_reconcile_attempt":
            outcome = payload.get("result")
        elif e.type == "execution_abandoned" and payload.get("reason") == "reconciliation window expired":
            outcome = "ABANDONED"
        else:
            outcome = None
        if outcome:
            reconciliation_outcomes[outcome] = reconciliation_outcomes.get(outcome, 0) + 1
        if e.type in ("execution_reconciled", "execution_reconcile_attempt") and "messages_scanned" in payload:
            messages_scanned_values.append(payload["messages_scanned"])

    mean_messages_scanned_per_reconcile = (
        sum(messages_scanned_values) / len(messages_scanned_values) if messages_scanned_values else None
    )

    # --- cross-run recipient blocking (§3 — do not hardcode; delegates the
    # actual blocking-status lookup to ActionRepository.cross_run_blocking_
    # runs, which reuses `_BLOCKING_LIVE_STATUSES` in-module) ---
    proposal_by_id = {p.id: p for p in proposals}
    per_proposal_attempts: dict[str, int] = {}
    for p in proposals:
        if p.recipient_identity_key and any(r in _RECIPIENT_CONFLICT_REASON_STRINGS for r in p.blocked_reasons):
            per_proposal_attempts[p.id] = per_proposal_attempts.get(p.id, 0) + 1
    for e in execution_blocked_events:
        reasons = (e.payload or {}).get("blocked_reasons", [])
        if not e.action_proposal_id or not any(r in _RECIPIENT_CONFLICT_REASON_STRINGS for r in reasons):
            continue
        p = proposal_by_id.get(e.action_proposal_id)
        if p is not None and p.recipient_identity_key:
            per_proposal_attempts[p.id] = per_proposal_attempts.get(p.id, 0) + 1

    recipient_keys = {proposal_by_id[pid].recipient_identity_key for pid in per_proposal_attempts}
    cross_run_map = await actions.cross_run_blocking_runs(recipient_keys, exclude_run_id=run_id)

    cross_run_recipient_blocks = 0
    cross_run_blocked_proposal_ids: set[str] = set()
    for pid, attempt_count in per_proposal_attempts.items():
        key = proposal_by_id[pid].recipient_identity_key
        if key in cross_run_map:
            cross_run_recipient_blocks += attempt_count
            cross_run_blocked_proposal_ids.add(pid)

    return {
        "proposals_by_verdict": proposals_by_verdict,
        "blocked_reasons": blocked_reasons,
        "execution_blocked_reasons": execution_blocked_reasons,
        "execution_blocked_attempts": execution_blocked_attempts,
        "execution_blocked_proposals": execution_blocked_proposals,
        "content_hash_mismatch_count": content_hash_mismatch_count,
        "approval_to_execution_latency_p50_ms": _percentile(latencies_ms, 0.5),
        "approval_to_execution_latency_p95_ms": _percentile(latencies_ms, 0.95),
        "executions_by_status": executions_by_status,
        "executions_by_origin": executions_by_origin,
        "uncertain_count": uncertain_count,
        "reconciliation_outcomes": reconciliation_outcomes,
        "mean_messages_scanned_per_reconcile": mean_messages_scanned_per_reconcile,
        "cross_run_recipient_blocks": cross_run_recipient_blocks,
        "cross_run_recipient_blocked_proposals": len(cross_run_blocked_proposal_ids),
    }
