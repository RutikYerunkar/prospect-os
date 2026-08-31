"""Research step — the Research Agent (§8): source documents -> `ResearchFacts`
+ `Evidence`, with fixture-derived structured output in Demo Mode. The only
step with `max_retries > 0` in the base fixtures — this is where Northwind's
scripted retry and Quarry's scripted, unrecoverable failure happen."""

from __future__ import annotations

import uuid

from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import EvidenceOrigin, SignalType
from groundwork.models.llm_io import ResearchExtractionOutput
from groundwork.models.schemas import Evidence
from groundwork.providers.base import PromptEnvelope


async def research(ctx: ProspectContext) -> StepResult:
    ctx_key = ctx.step_key("research")
    docs = await ctx.providers.search.fetch_sources(ctx.company, ctx_key=ctx_key)

    evidence = [
        Evidence(
            id=str(uuid.uuid4()),
            prospect_id=ctx.prospect_id,
            source_url=None,
            source_ref=doc.ref,
            source_provider=doc.source_provider,
            title=doc.title,
            claim=doc.claim,
            snippet=doc.text,
            signal_type=SignalType(doc.signal_type) if doc.signal_type else None,
            retrieved_at=None,
            confidence=doc.confidence,
            origin=EvidenceOrigin.DEMO_FIXTURE,
        )
        for doc in docs
    ]
    ctx.evidence.extend(evidence)

    envelope = PromptEnvelope(
        ctx_key=ctx_key,
        system="Extract structured research facts (funding, hiring, tech, leadership) from the provided sources.",
        user=f"Extract facts for {ctx.company.name} from {len(docs)} source document(s).",
        metadata={"company_slug": ctx.company.slug, "reference_date": ctx.reference_date.isoformat()},
    )
    llm_result = await ctx.providers.llm.structured(envelope, ResearchExtractionOutput, ctx_key=ctx_key)
    output = ResearchExtractionOutput.model_validate(llm_result.data)

    # Naive structural link only — source_ref -> this prospect's own evidence
    # id. Grounding (does the claim's text actually occur in that evidence's
    # snippet?) is verified deterministically in the signals step, not here.
    evidence_id_by_ref = {e.source_ref: e.id for e in ctx.evidence if e.source_ref}
    facts = output.facts
    for item in (*facts.funding_events, *facts.hiring_roles, *facts.tech_mentions, *facts.leadership):
        if item.source_ref and item.source_ref in evidence_id_by_ref:
            item.evidence_ids = [evidence_id_by_ref[item.source_ref]]

    ctx.facts = facts
    fact_count = len(facts.funding_events) + len(facts.hiring_roles) + len(facts.tech_mentions) + len(facts.leadership)
    return StepResult(ok=True, detail=f"{len(evidence)} evidence rows, {fact_count} facts")
