"""Score step — the deterministic ICP rubric (§13). `domain/scoring.py`
computes every number; the LLM call here only writes the prose explanation
*from* the finished score and cannot alter it."""

from __future__ import annotations

from groundwork.domain.scoring import ScoringInputs, compute_score
from groundwork.engine.context import ProspectContext
from groundwork.engine.llm import call_structured
from groundwork.engine.step import StepResult
from groundwork.models.llm_io import ScoreExplanationOutput
from groundwork.prompts import score_explanation as prompt
from groundwork.providers.base import LLMOperation


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
        industry_fact=ctx.facts.profile.industry,
        employee_count_fact=ctx.facts.profile.employee_count,
    )
    computed = compute_score(ctx.prospect_id, inputs)

    ctx_key = ctx.step_key("score")
    top_dimensions = sorted(computed.dimensions, key=lambda d: d.contribution, reverse=True)[:3]
    disqualifier = next((m for m in computed.modifiers if m.name == "hard_disqualifier"), None)
    prompt_input = prompt.ScoreExplanationInput(
        company_name=ctx.company.name,
        overall=computed.overall,
        disqualified=computed.disqualified,
        disqualifier_reason=disqualifier.detail if disqualifier else None,
        top_dimensions=[
            prompt.TopDimensionInput(name=d.name, raw=d.raw, weight=d.weight, contribution=d.contribution)
            for d in top_dimensions
        ],
    )
    envelope = prompt.build_envelope(ctx_key, prompt_input)
    llm_result = await call_structured(
        ctx, envelope, ScoreExplanationOutput,
        operation=LLMOperation.SCORE_EXPLANATION, step_name="score", prompt_version=prompt.PROMPT_VERSION,
    )
    explanation = llm_result.parsed

    computed.explanation = explanation.explanation
    ctx.score = computed
    await ctx.events.emit("prospect.scored", prospect_id=ctx.prospect_id, overall=computed.overall, disqualified=computed.disqualified)
    return StepResult(ok=True, detail=f"overall={computed.overall} confidence={computed.confidence:.2f}")
