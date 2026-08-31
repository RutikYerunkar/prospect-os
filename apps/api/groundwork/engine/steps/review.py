"""Review step — the seven deterministic checks (§14). No LLM in this path."""

from __future__ import annotations

from groundwork.domain.review import run_checks
from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult


async def review(ctx: ProspectContext) -> StepResult:
    assert ctx.score is not None
    result = run_checks(
        prospect_id=ctx.prospect_id,
        evidence=ctx.evidence,
        drafts=ctx.drafts,
        contact=ctx.contact,
        score=ctx.score,
        dedupe_key=ctx.dedupe_key,
        other_dedupe_keys=set(ctx.other_dedupe_keys),
        other_company_identifiers=set(ctx.other_company_identifiers),
        min_confidence=ctx.play_spec.min_confidence,
    )
    ctx.review = result
    await ctx.events.emit("prospect.reviewed", prospect_id=ctx.prospect_id, verdict=result.verdict.value)
    return StepResult(ok=True, detail=f"verdict={result.verdict.value}")
