"""Personalize step — the Personalization Agent (§8): the one place taste
matters. Skipped (never fabricated) when there is no contact to write to —
"Missing is UNAVAILABLE, not invented" (§3). The envelope is built only from
this prospect's own grounded signals, which is what makes the isolation
guarantee structural rather than a matter of prompt discipline: there is
nothing in scope for the provider to leak even if it wanted to.
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.llm import call_structured
from groundwork.engine.step import StepResult
from groundwork.models.enums import ContactVerification
from groundwork.models.llm_io import PersonalizationOutput
from groundwork.models.schemas import OutreachDraft
from groundwork.prompts import personalization as prompt
from groundwork.providers.base import LLMOperation


async def personalize(ctx: ProspectContext) -> StepResult:
    assert ctx.contact is not None
    if ctx.contact.verification == ContactVerification.UNAVAILABLE:
        return StepResult(ok=True, skipped=True, detail="no verified persona — personalization skipped")

    grounded_signals = [s for s in ctx.signals if s.grounded and s.evidence_ids]
    ctx_key = ctx.step_key("personalize")
    prompt_input = prompt.PersonalizationInput(
        company_name=ctx.company.name,
        persona_name=ctx.contact.full_name,
        persona_title=ctx.contact.title,
        signals=[
            prompt.GroundedSignalInput(summary=s.summary, evidence_id=s.evidence_ids[0]) for s in grounded_signals
        ],
    )
    envelope = prompt.build_envelope(ctx_key, prompt_input)
    llm_result = await call_structured(
        ctx, envelope, PersonalizationOutput,
        operation=LLMOperation.PERSONALIZATION, step_name="personalize", prompt_version=prompt.PROMPT_VERSION,
    )
    output = llm_result.parsed

    draft = OutreachDraft(
        prospect_id=ctx.prospect_id,
        subject=output.subject,
        body=output.body,
        claim_map=output.claim_map,
    )
    ctx.drafts.append(draft)
    return StepResult(ok=True, detail=f"drafted with {len(output.claim_map)} grounded claim(s)")
