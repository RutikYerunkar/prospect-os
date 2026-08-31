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

from groundwork.domain.grounding import is_grounded
from groundwork.engine.context import ProspectContext
from groundwork.engine.step import StepResult
from groundwork.models.enums import SignalType
from groundwork.models.schemas import Signal


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
                confidence=evidence.confidence if evidence else 0.0,
                evidence_ids=item.evidence_ids,
                grounded=grounded,
            )
        )

    return StepResult(ok=True, detail=f"{verified} grounded, {demoted} demoted to unsupported")
