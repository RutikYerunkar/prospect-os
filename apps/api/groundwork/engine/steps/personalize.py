"""Personalize step — the Personalization Agent (§8): the one place taste
matters. Skipped (never fabricated) when there is no contact to write to —
"Missing is UNAVAILABLE, not invented" (§3). The envelope is built only from
this prospect's own grounded signals, which is what makes the isolation
guarantee structural rather than a matter of prompt discipline: there is
nothing in scope for the provider to leak even if it wanted to.
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import ContactVerification
from groundwork.models.llm_io import PersonalizationOutput
from groundwork.models.schemas import OutreachDraft
from groundwork.providers.base import PromptEnvelope


async def personalize(ctx: ProspectContext) -> StepResult:
    assert ctx.contact is not None
    if ctx.contact.verification == ContactVerification.UNAVAILABLE:
        return StepResult(ok=True, skipped=True, detail="no verified persona — personalization skipped")

    grounded_signals = [s for s in ctx.signals if s.grounded and s.evidence_ids]
    ctx_key = ctx.step_key("personalize")
    envelope = PromptEnvelope(
        ctx_key=ctx_key,
        system="Write a short, personalized outreach email citing only the grounded signals given.",
        user=f"Draft outreach to {ctx.contact.full_name or ctx.contact.title} at {ctx.company.name}.",
        metadata={
            "company_name": ctx.company.name,
            "persona_name": ctx.contact.full_name,
            "persona_title": ctx.contact.title,
            "signals": [
                {"summary": s.summary, "evidence_id": s.evidence_ids[0]} for s in grounded_signals
            ],
        },
    )
    llm_result = await ctx.providers.llm.structured(envelope, PersonalizationOutput, ctx_key=ctx_key)
    output = PersonalizationOutput.model_validate(llm_result.data)

    draft = OutreachDraft(
        prospect_id=ctx.prospect_id,
        subject=output.subject,
        body=output.body,
        claim_map=output.claim_map,
    )
    ctx.drafts.append(draft)
    return StepResult(ok=True, detail=f"drafted with {len(output.claim_map)} grounded claim(s)")
