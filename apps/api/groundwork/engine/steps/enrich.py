"""Enrich step — deterministic merge (§9): field precedence, no interpolation.

Also resolves a preliminary `Contact` from the (grounded) leadership facts.
Persona resolution has to happen before Score (persona_availability is one of
its eight dimensions) even though the dedicated Contact step comes later in
the pipeline — the Contact step simply confirms/finalizes what is computed
here rather than recomputing it, so the two can never disagree.
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import ContactVerification
from groundwork.models.schemas import Contact, ResearchFacts


def resolve_contact(prospect_id: str, facts: ResearchFacts, persona_titles: list[str]) -> Contact:
    grounded = [leader for leader in facts.leadership if leader.evidence_ids]
    persona_matches = [
        leader for leader in grounded if leader.is_persona_match or leader.title in persona_titles
    ]
    if not persona_matches:
        return Contact(prospect_id=prospect_id, verification=ContactVerification.UNAVAILABLE, evidence_ids=[])

    leader = persona_matches[0]
    if leader.full_name:
        return Contact(
            prospect_id=prospect_id,
            full_name=leader.full_name,
            title=leader.title,
            persona_match=True,
            verification=ContactVerification.VERIFIED,
            evidence_ids=leader.evidence_ids,
        )
    return Contact(
        prospect_id=prospect_id,
        full_name=None,
        title=leader.title,
        persona_match=True,
        verification=ContactVerification.PERSONA_ONLY,
        evidence_ids=leader.evidence_ids,
    )


async def enrich(ctx: ProspectContext) -> StepResult:
    assert ctx.facts is not None
    # Field precedence: verified fixture facts already win by construction
    # (only grounded items keep evidence_ids after the signals step); nothing
    # here needs to arbitrate a conflict for these six fixtures. Absent
    # values stay absent — never interpolated.
    ctx.contact = resolve_contact(ctx.prospect_id, ctx.facts, ctx.play_spec.persona_titles)
    return StepResult(ok=True, detail=f"contact resolved: {ctx.contact.verification.value}")
