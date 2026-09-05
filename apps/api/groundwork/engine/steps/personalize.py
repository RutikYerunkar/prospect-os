"""Personalize step — the Personalization Agent (§8): the one place taste
matters. Skipped (never fabricated) when there is no contact to write to —
"Missing is UNAVAILABLE, not invented" (§3). The envelope is built only from
this prospect's own grounded signals, which is what makes the isolation
guarantee structural rather than a matter of prompt discipline: there is
nothing in scope for the provider to leak even if it wanted to.

v2 §V2-F: this step now drafts one email (unchanged, byte-identical to v1 —
the block below is untouched) plus, when eligible, one ADDITIONAL LinkedIn
draft via a SEPARATE LLM call (`prompts/linkedin_personalization.py`, its own
`LLMOperation.LINKEDIN_PERSONALIZATION`, its own ctx_key
`personalize:linkedin`). Eligibility is `contact_channels[LINKEDIN].
discovery_state == RESOLVED` ONLY — a `MISMATCH` identity is NOT checked
here (identity policy is not duplicated in personalization); a MISMATCH
profile that is otherwise RESOLVED still gets a draft, and
`domain/review.py::_no_fabricated_contact` deterministically blocks it
afterward. No new step, no new timeout/retry budget — both calls share the
existing `personalize` step's budget."""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.llm import call_structured
from groundwork.engine.step import StepResult
from groundwork.models.enums import Channel, ContactVerification, LinkedInResolutionState
from groundwork.models.llm_io import LinkedInOutreachOutput, PersonalizationOutput
from groundwork.models.schemas import OutreachDraft
from groundwork.prompts import linkedin_personalization as linkedin_prompt
from groundwork.prompts import personalization as prompt
from groundwork.providers.base import LLMOperation


async def personalize(ctx: ProspectContext) -> StepResult:
    assert ctx.contact is not None
    if ctx.contact.verification == ContactVerification.UNAVAILABLE:
        return StepResult(ok=True, skipped=True, detail="no verified persona — personalization skipped")

    grounded_signals = [s for s in ctx.signals if s.grounded and s.evidence_ids]

    # --- Email (v1, byte-identical — untouched) ---
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
        channel=Channel.EMAIL,
        step_index=0,
        subject=output.subject,
        body=output.body,
        claim_map=output.claim_map,
    )
    ctx.drafts.append(draft)
    detail = f"drafted with {len(output.claim_map)} grounded claim(s)"

    # --- LinkedIn (v2 §V2-F, additive) ---
    linkedin_channel = next((c for c in ctx.contact_channels if c.channel is Channel.LINKEDIN), None)
    linkedin_eligible = (
        linkedin_channel is not None
        and linkedin_channel.discovery_state == LinkedInResolutionState.RESOLVED.value
    )
    if linkedin_eligible:
        linkedin_ctx_key = ctx.step_key("personalize:linkedin")
        linkedin_input = linkedin_prompt.LinkedInPersonalizationInput(
            company_name=ctx.company.name,
            persona_name=ctx.contact.full_name,
            persona_title=ctx.contact.title,
            signals=[
                linkedin_prompt.GroundedSignalInput(summary=s.summary, evidence_id=s.evidence_ids[0])
                for s in grounded_signals
            ],
        )
        linkedin_envelope = linkedin_prompt.build_envelope(linkedin_ctx_key, linkedin_input)
        linkedin_result = await call_structured(
            ctx, linkedin_envelope, LinkedInOutreachOutput,
            operation=LLMOperation.LINKEDIN_PERSONALIZATION, step_name="personalize",
            prompt_version=linkedin_prompt.PROMPT_VERSION,
        )
        linkedin_output = linkedin_result.parsed
        linkedin_draft = OutreachDraft(
            prospect_id=ctx.prospect_id,
            channel=Channel.LINKEDIN,
            step_index=1,
            subject=None,
            body=linkedin_output.body,
            claim_map=linkedin_output.claim_map,
        )
        ctx.drafts.append(linkedin_draft)
        detail += f"; linkedin drafted with {len(linkedin_output.claim_map)} grounded claim(s)"

    return StepResult(ok=True, detail=detail)
