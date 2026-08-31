"""Contact step — deterministic, never generative (§9): "the most dangerous
hallucination in GTM." The persona candidate was already resolved in Enrich
(needed there for the Score step); this step finalizes it. Optional: if
resolution is somehow missing, the prospect degrades to UNAVAILABLE rather
than failing outright."""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import ContactVerification
from groundwork.models.schemas import Contact


async def contact(ctx: ProspectContext) -> StepResult:
    if ctx.contact is None:
        ctx.contact = Contact(prospect_id=ctx.prospect_id, verification=ContactVerification.UNAVAILABLE)
    return StepResult(ok=True, detail=f"verification={ctx.contact.verification.value}")
