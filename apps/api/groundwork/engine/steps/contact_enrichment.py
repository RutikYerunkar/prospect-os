"""Contact-enrichment step (v2 §Part 4/§F) — deliberately NOT named `enrich`
(C4): `engine/steps/enrich.py` already means the v1 deterministic
field-precedence merge. This step calls a real `EnrichmentProvider` through
`engine/enrichment.py::call_enrichment` and lets the pure
`domain/contact_identity.py` derivations turn its observation into
`contact_channels` state.

Optional (`Step(optional=True)` in `engine/pipeline.py`): a contact-
enrichment provider failure degrades this one prospect's enrichment (visible
in the trace) rather than crashing the whole prospect. `ctx.contact` itself
is NEVER written here — `Contact.verification` is the v1 person-identity
axis (§C3); rewriting it would move every ICP score in the canonical demo.

NOT_ATTEMPTED, zero provider calls, for any of:
- no named person to look up (`ctx.contact.full_name is None`) — Riverbend's
  PERSONA_ONLY case, and Ferrous's UNAVAILABLE case;
- the prospect is already hard-disqualified (`ctx.score.disqualified`) —
  Cobalt's excluded-industry case (§Part 7's Demo matrix: "not attempted;
  never actionable" — there is no value in enriching contact info for a
  company already rejected by hard policy);
- no enrichment provider is wired for this run (`call_enrichment` returns
  `None` — Live Mode before V2-D, or enrichment disabled).
"""

from __future__ import annotations

from groundwork.engine.context import ProspectContext
from groundwork.engine.enrichment import call_enrichment
from groundwork.engine.step import StepResult
from groundwork.providers.contact_base import PersonEnrichmentQuery


async def contact_enrichment(ctx: ProspectContext) -> StepResult:
    assert ctx.contact is not None

    if ctx.contact.full_name is None:
        return StepResult(ok=True, skipped=True, detail="no named person — contact_enrichment not attempted")

    if ctx.score is not None and ctx.score.disqualified:
        return StepResult(ok=True, skipped=True, detail="prospect disqualified — contact_enrichment not attempted")

    query = PersonEnrichmentQuery(
        full_name=ctx.contact.full_name,
        title=ctx.contact.title,
        company_name=ctx.company.name,
        company_domain=ctx.company.domain,
    )
    result = await call_enrichment(ctx, query)
    if result is None:
        return StepResult(ok=True, skipped=True, detail="no enrichment provider configured — not attempted")

    return StepResult(ok=True, detail=f"contact_enrichment matched={result.matched}")
