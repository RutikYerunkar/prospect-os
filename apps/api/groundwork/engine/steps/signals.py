"""Signals step — the deterministic half of the Hybrid signal detection (§9).

Research already produced each fact's claim text and a naive source_ref link.
This step is the "deterministic verifier [that] confirms the span actually
occurs in the cited source": any fact whose claim doesn't sufficiently
overlap its evidence's snippet is demoted — its `evidence_ids` are cleared so
it can never contribute to scoring, and a `Signal` is still recorded so the
demotion is visible in the trace, never silently dropped.
"""

from __future__ import annotations

import uuid

from groundwork.domain.grounding import is_grounded, numeric_claim_supported
from groundwork.domain.industry import allowed_categories, validate_category
from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import SignalType
from groundwork.models.schemas import Signal


def _occurred_at(item: object):
    return getattr(item, "announced_at", None) or getattr(item, "posted_at", None)


async def signals(ctx: ProspectContext) -> StepResult:
    assert ctx.facts is not None
    verified = 0
    demoted = 0

    for item, signal_type in (
        *((f, SignalType.FUNDING) for f in ctx.facts.funding_events),
        *((h, SignalType.HIRING) for h in ctx.facts.hiring_roles),
        *((t, SignalType.TECH) for t in ctx.facts.tech_mentions),
        *((leader, SignalType.LEADERSHIP) for leader in ctx.facts.leadership),
    ):
        if not item.evidence_ids:
            continue
        evidence = ctx.evidence_by_id(item.evidence_ids[0])
        grounded = evidence is not None and is_grounded(item.claim, evidence)
        if not grounded:
            item.evidence_ids = []
            demoted += 1
        else:
            verified += 1

        ctx.signals.append(
            Signal(
                id=str(uuid.uuid4()),
                prospect_id=ctx.prospect_id,
                type=signal_type,
                summary=item.claim,
                occurred_at=_occurred_at(item),
                confidence=evidence.confidence if evidence else 0.0,
                evidence_ids=item.evidence_ids,
                grounded=grounded,
            )
        )

    # --- H1 Phase 4/5/6: independent, deterministic grounding for the two
    # profile facts. Each is verified on its OWN claim against its OWN
    # cited evidence — neither ever inherits the other's evidence_ids, even
    # when both happen to cite the same source_ref (the "industry and
    # employee_count never share one provenance record" invariant).

    industry = ctx.facts.profile.industry
    if industry.evidence_ids:
        allowed = allowed_categories(ctx.play_spec)
        category = validate_category(industry.category, allowed)
        evidence = ctx.evidence_by_id(industry.evidence_ids[0])
        category_grounded = (
            category is not None and evidence is not None and is_grounded(industry.claim, evidence)
        )
        if category_grounded:
            industry.category = category
            verified += 1
        else:
            industry.category = None
            industry.evidence_ids = []
            demoted += 1
    else:
        industry.category = None

    employee_count = ctx.facts.profile.employee_count
    if employee_count.evidence_ids and employee_count.employee_count is not None:
        evidence = ctx.evidence_by_id(employee_count.evidence_ids[0])
        count_grounded = evidence is not None and numeric_claim_supported(
            evidence.snippet, employee_count.employee_count
        )
        if count_grounded:
            verified += 1
        else:
            employee_count.employee_count = None
            employee_count.evidence_ids = []
            demoted += 1
    else:
        employee_count.evidence_ids = []

    return StepResult(ok=True, detail=f"{verified} grounded, {demoted} demoted to unsupported")
