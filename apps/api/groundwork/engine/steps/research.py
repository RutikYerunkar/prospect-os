"""Research step — the Research Agent (§8): source documents -> `ResearchFacts`
+ `Evidence`, with fixture-derived structured output in Demo Mode. The only
step with `max_retries > 0` in the base fixtures — this is where Northwind's
scripted retry and Quarry's scripted, unrecoverable failure happen.

H1 Phase 1/9/10/11 — commit-once architecture. Two bugs this closes:

- **Retry duplication (Bug A)**: the pre-H1 version appended `Evidence`
  before the LLM call, so a step-level retry (this whole function called
  again) appended the same sources' evidence a second time. Retrieval state
  (`ctx.sources`, fetched at most once per prospect) is now strictly
  separate from accepted Evidence state (`ctx.evidence`, only ever written
  by ONE assignment, only on a *successful* extraction). A failed
  extraction never partially mutates `ctx.evidence`, and a retry never
  calls the search provider again.
- **Deterministic, idempotent Evidence ids**: `domain.source_identity.
  evidence_id_for()` (uuid5 over prospect + source identity) means the same
  winning source always derives the same Evidence id — a retried commit can
  never produce a duplicate row for the same source even if this function
  somehow ran to a second successful completion.
"""

from __future__ import annotations

from groundwork.domain.source_identity import evidence_id_for, select_winners
from groundwork.engine.context import ProspectContext
from groundwork.engine.llm import call_structured
from groundwork.engine.search import call_search
from groundwork.engine.step import StepResult
from groundwork.models.enums import EvidenceOrigin, SignalType
from groundwork.models.llm_io import ResearchExtractionOutput
from groundwork.models.schemas import Evidence
from groundwork.prompts import research_extraction as prompt
from groundwork.providers.base import LLMOperation


async def research(ctx: ProspectContext) -> StepResult:
    ctx_key = ctx.step_key("research")

    # Retrieval state: fetched at most once per prospect. A step-level
    # retry (this whole function invoked again after an LLM failure) reuses
    # the cached winners instead of calling the search provider a second
    # time — no duplicate `source_documents` rows, no duplicate provider
    # call.
    if not ctx.sources:
        occurrences = await call_search(ctx)
        ctx.sources = select_winners(occurrences)
    winners = ctx.sources

    # Candidate Evidence, built LOCALLY — never appended to `ctx.evidence`
    # until extraction actually succeeds (see module docstring).
    #
    # Origin/URL/retrieved_at are read from the winning `SourceDocument`
    # itself, never hardcoded — H2 fix: this used to hardcode
    # `origin=DEMO_FIXTURE, source_url=None` unconditionally, which would
    # have silently mislabeled real `TavilySearchProvider` evidence as
    # synthetic fixture data (and dropped its real, clickable URL) the
    # moment Live search existed. `Evidence`'s own §12 model validator
    # still enforces the invariant structurally: only a genuinely
    # LIVE_FETCH-origin document may carry a `source_url` at all.
    candidate_evidence = [
        Evidence(
            id=evidence_id_for(ctx.prospect_id, doc),
            prospect_id=ctx.prospect_id,
            source_url=doc.url if doc.origin == EvidenceOrigin.LIVE_FETCH else None,
            source_ref=doc.ref,
            source_provider=doc.source_provider,
            title=doc.title,
            claim=doc.claim,
            snippet=doc.text,
            signal_type=SignalType(doc.signal_type) if doc.signal_type else None,
            retrieved_at=doc.retrieved_at,
            confidence=doc.confidence,
            origin=doc.origin,
        )
        for doc in winners
    ]

    prompt_input = prompt.ResearchExtractionInput.from_context(
        company=ctx.company, reference_date=ctx.reference_date, docs=winners, play_spec=ctx.play_spec
    )
    envelope = prompt.build_envelope(ctx_key, prompt_input)
    llm_result = await call_structured(
        ctx, envelope, ResearchExtractionOutput,
        operation=LLMOperation.RESEARCH_EXTRACTION, step_name="research", prompt_version=prompt.PROMPT_VERSION,
    )
    output = llm_result.parsed

    # Naive structural link only — source_ref -> this (candidate) evidence
    # id. Grounding (does the claim's text actually occur in that
    # evidence's snippet?) is verified deterministically in the signals
    # step, not here. Profile facts (industry/employee_count) are linked
    # the same way and independently — see module docstring: neither ever
    # inherits the other's evidence_ids just because both cite the same
    # source_ref.
    evidence_id_by_ref = {e.source_ref: e.id for e in candidate_evidence if e.source_ref}
    facts = output.facts
    for item in (*facts.funding_events, *facts.hiring_roles, *facts.tech_mentions, *facts.leadership):
        if item.source_ref and item.source_ref in evidence_id_by_ref:
            item.evidence_ids = [evidence_id_by_ref[item.source_ref]]
    if facts.profile.industry.source_ref and facts.profile.industry.source_ref in evidence_id_by_ref:
        facts.profile.industry.evidence_ids = [evidence_id_by_ref[facts.profile.industry.source_ref]]
    if facts.profile.employee_count.source_ref and facts.profile.employee_count.source_ref in evidence_id_by_ref:
        facts.profile.employee_count.evidence_ids = [
            evidence_id_by_ref[facts.profile.employee_count.source_ref]
        ]

    # Commit once, only on a successful extraction. This is a plain
    # assignment (not `.extend(...)`), so even in the hypothetical case of
    # this function somehow completing successfully twice for the same
    # prospect, `ctx.evidence` never accumulates duplicates — and because
    # Evidence ids are deterministic, re-committing the same winners is a
    # no-op in content, not a growth.
    ctx.evidence = candidate_evidence
    ctx.facts = facts
    fact_count = len(facts.funding_events) + len(facts.hiring_roles) + len(facts.tech_mentions) + len(facts.leadership)
    return StepResult(ok=True, detail=f"{len(candidate_evidence)} evidence rows, {fact_count} facts")
