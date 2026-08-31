"""Score step — the deterministic ICP rubric (§13). `domain/scoring.py`
computes every number; the LLM call here only writes the prose explanation
*from* the finished score and cannot alter it."""

from __future__ import annotations

from groundwork.domain.scoring import ScoringInputs, compute_score
from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.llm_io import ScoreExplanationOutput
from groundwork.providers.base import PromptEnvelope


async def score(ctx: ProspectContext) -> StepResult:
    assert ctx.facts is not None
    inputs = ScoringInputs(
        company=ctx.company,
        play_spec=ctx.play_spec,
        funding_events=ctx.facts.funding_events,
        hiring_roles=ctx.facts.hiring_roles,
        tech_mentions=ctx.facts.tech_mentions,
        contact=ctx.contact,
        evidence=ctx.evidence,
        reference_date=ctx.reference_date,
    )
    computed = compute_score(ctx.prospect_id, inputs)

    ctx_key = ctx.step_key("score")
    top_dimensions = sorted(computed.dimensions, key=lambda d: d.contribution, reverse=True)[:3]
    envelope = PromptEnvelope(
        ctx_key=ctx_key,
        system="Write one sentence explaining this ICP score from the numbers given. Do not invent numbers.",
        user=f"{ctx.company.name} scored {computed.overall}/100.",
        metadata={
            "overall": computed.overall,
            "disqualified": computed.disqualified,
            "top_dimensions": [
                {"name": d.name, "contribution": d.contribution} for d in top_dimensions
            ],
        },
    )
    llm_result = await ctx.providers.llm.structured(envelope, ScoreExplanationOutput, ctx_key=ctx_key)
    explanation = ScoreExplanationOutput.model_validate(llm_result.data)

    computed.explanation = explanation.explanation
    ctx.score = computed
    await ctx.events.emit("prospect.scored", prospect_id=ctx.prospect_id, overall=computed.overall, disqualified=computed.disqualified)
    return StepResult(ok=True, detail=f"overall={computed.overall} confidence={computed.confidence:.2f}")
