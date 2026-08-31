"""ICP scoring — the deterministic weighted rubric (IMPLEMENTATION_PLAN.md §13).

Pure function over structured features. No I/O, no provider or repository
imports. The LLM never touches these numbers — it may only write prose *from*
the finished `ICPScore` (see `groundwork.models.llm_io.ScoreExplanationOutput`
and `engine/steps/score.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from groundwork.models.schemas import (
    Contact,
    DimensionScore,
    Evidence,
    FundingEvent,
    HiringRole,
    ICPScore,
    PlaySpec,
    ScoreModifier,
    TechMention,
)
from groundwork.models.schemas import CompanySeed

RUBRIC_VERSION = "v1"

WEIGHTS: dict[str, float] = {
    "industry_fit": 0.20,
    "size_fit": 0.15,
    "funding_signal": 0.15,
    "hiring_signal": 0.15,
    "tech_fit": 0.10,
    "persona_availability": 0.10,
    "signal_freshness": 0.10,
    "evidence_confidence": 0.05,
}

# Ordered so adjacency (one position off) can be computed as a distance of 1.
_FUNDING_STAGE_ORDER = [
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c",
    "growth",
]

PERSONA_RAW = {"VERIFIED": 1.0, "PERSONA_ONLY": 0.5, "UNAVAILABLE": 0.0}

# industry_fit and size_fit are derived directly from CompanySeed — a
# structural profile fact discovery always populates, not a claim that needs
# grounding evidence. The §13 evidence gate applies to the other six, whose
# raw value comes from an assertion (funding, hiring, tech, persona, signal
# timing, evidence quality) that could be entirely absent.
_STRUCTURAL_DIMENSIONS = {"industry_fit", "size_fit"}


@dataclass
class ScoringInputs:
    company: CompanySeed
    play_spec: PlaySpec
    funding_events: list[FundingEvent] = field(default_factory=list)
    hiring_roles: list[HiringRole] = field(default_factory=list)
    tech_mentions: list[TechMention] = field(default_factory=list)
    contact: Contact | None = None
    evidence: list[Evidence] = field(default_factory=list)
    reference_date: date = field(default_factory=date.today)


def _industry_fit(inputs: ScoringInputs) -> DimensionScore:
    industry = inputs.company.industry
    spec = inputs.play_spec
    if industry in spec.target_industries:
        raw = 1.0
    elif any(industry in adj for adj in spec.adjacent_industries.values()) or any(
        adj_target in spec.target_industries
        for adj_target in spec.adjacent_industries.get(industry, [])
    ):
        raw = 0.6
    else:
        raw = 0.0
    return DimensionScore(name="industry_fit", raw=raw, weight=WEIGHTS["industry_fit"], contribution=0.0)


def _size_fit(inputs: ScoringInputs) -> DimensionScore:
    spec = inputs.play_spec
    count = inputs.company.employee_count
    lo, hi = spec.size_band_min, spec.size_band_max
    if lo <= count <= hi:
        raw = 1.0
    else:
        band_width = max(hi - lo, 1)
        distance = (lo - count) if count < lo else (count - hi)
        raw = max(0.0, 1.0 - distance / band_width)
    return DimensionScore(name="size_fit", raw=raw, weight=WEIGHTS["size_fit"], contribution=0.0)


def _stage_match(stage: str, targets: list[str]) -> float:
    if stage in targets:
        return 1.0
    if stage not in _FUNDING_STAGE_ORDER or not targets:
        return 0.0
    stage_idx = _FUNDING_STAGE_ORDER.index(stage)
    for target in targets:
        if target in _FUNDING_STAGE_ORDER and abs(_FUNDING_STAGE_ORDER.index(target) - stage_idx) == 1:
            return 0.5
    return 0.0


def _recency_decay(reference: date, occurred: date | None, half_life_days: float) -> float:
    if occurred is None:
        return 0.0
    days = max((reference - occurred).days, 0)
    return math.exp(-days / half_life_days)


def _funding_signal(inputs: ScoringInputs) -> DimensionScore:
    grounded_events = [e for e in inputs.funding_events if e.evidence_ids]
    if not grounded_events:
        return DimensionScore(
            name="funding_signal", raw=0.0, weight=WEIGHTS["funding_signal"], contribution=0.0,
            evidence_ids=[], unsupported=True,
        )
    newest = max(grounded_events, key=lambda e: e.announced_at or date.min)
    stage_score = _stage_match(newest.stage, inputs.play_spec.target_funding_stages)
    recency = _recency_decay(inputs.reference_date, newest.announced_at, 180.0)
    raw = stage_score * recency
    return DimensionScore(
        name="funding_signal", raw=raw, weight=WEIGHTS["funding_signal"], contribution=0.0,
        evidence_ids=list(newest.evidence_ids), unsupported=False,
    )


def _hiring_signal(inputs: ScoringInputs) -> DimensionScore:
    relevant = [r for r in inputs.hiring_roles if r.is_gtm and r.evidence_ids]
    if not relevant:
        return DimensionScore(
            name="hiring_signal", raw=0.0, weight=WEIGHTS["hiring_signal"], contribution=0.0,
            evidence_ids=[], unsupported=True,
        )
    role_score = min(1.0, len(relevant) / 3.0)
    newest = max(relevant, key=lambda r: r.posted_at or date.min)
    recency = _recency_decay(inputs.reference_date, newest.posted_at, 180.0)
    raw = role_score * recency
    evidence_ids = sorted({eid for r in relevant for eid in r.evidence_ids})
    return DimensionScore(
        name="hiring_signal", raw=raw, weight=WEIGHTS["hiring_signal"], contribution=0.0,
        evidence_ids=evidence_ids, unsupported=False,
    )


def _tech_fit(inputs: ScoringInputs) -> DimensionScore:
    grounded = [t for t in inputs.tech_mentions if t.evidence_ids]
    if not grounded:
        return DimensionScore(
            name="tech_fit", raw=0.0, weight=WEIGHTS["tech_fit"], contribution=0.0,
            evidence_ids=[], unsupported=True,
        )
    detected = {t.name.lower() for t in grounded}
    target = {t.lower() for t in inputs.play_spec.target_technologies}
    union = detected | target
    raw = len(detected & target) / len(union) if union else 0.0
    evidence_ids = sorted({eid for t in grounded for eid in t.evidence_ids})
    return DimensionScore(
        name="tech_fit", raw=raw, weight=WEIGHTS["tech_fit"], contribution=0.0,
        evidence_ids=evidence_ids, unsupported=False,
    )


def _persona_availability(inputs: ScoringInputs) -> DimensionScore:
    contact = inputs.contact
    if contact is None or not contact.evidence_ids:
        return DimensionScore(
            name="persona_availability", raw=0.0, weight=WEIGHTS["persona_availability"],
            contribution=0.0, evidence_ids=[], unsupported=True,
        )
    raw = PERSONA_RAW.get(contact.verification.value, 0.0)
    return DimensionScore(
        name="persona_availability", raw=raw, weight=WEIGHTS["persona_availability"],
        contribution=0.0, evidence_ids=list(contact.evidence_ids), unsupported=False,
    )


def _signal_freshness(inputs: ScoringInputs) -> DimensionScore:
    dated_funding = [(e.announced_at, e.evidence_ids) for e in inputs.funding_events if e.evidence_ids and e.announced_at]
    dated_hiring = [(r.posted_at, r.evidence_ids) for r in inputs.hiring_roles if r.evidence_ids and r.posted_at]
    dated = dated_funding + dated_hiring
    if not dated:
        return DimensionScore(
            name="signal_freshness", raw=0.0, weight=WEIGHTS["signal_freshness"], contribution=0.0,
            evidence_ids=[], unsupported=True,
        )
    newest_date, evidence_ids = max(dated, key=lambda item: item[0])
    raw = _recency_decay(inputs.reference_date, newest_date, 90.0)
    return DimensionScore(
        name="signal_freshness", raw=raw, weight=WEIGHTS["signal_freshness"], contribution=0.0,
        evidence_ids=list(evidence_ids), unsupported=False,
    )


def _evidence_confidence(inputs: ScoringInputs) -> DimensionScore:
    if not inputs.evidence:
        return DimensionScore(
            name="evidence_confidence", raw=0.0, weight=WEIGHTS["evidence_confidence"],
            contribution=0.0, evidence_ids=[], unsupported=True,
        )
    raw = sum(e.confidence for e in inputs.evidence) / len(inputs.evidence)
    return DimensionScore(
        name="evidence_confidence", raw=raw, weight=WEIGHTS["evidence_confidence"],
        contribution=0.0, evidence_ids=[e.id for e in inputs.evidence], unsupported=False,
    )


_DIMENSION_FNS = [
    _industry_fit,
    _size_fit,
    _funding_signal,
    _hiring_signal,
    _tech_fit,
    _persona_availability,
    _signal_freshness,
    _evidence_confidence,
]


def compute_score(prospect_id: str, inputs: ScoringInputs) -> ICPScore:
    dimensions = [fn(inputs) for fn in _DIMENSION_FNS]

    # Evidence gate, enforced defensively even though each dimension fn
    # already zeroes itself out when ungrounded: a dimension with no
    # supporting evidence can never contribute points.
    for dim in dimensions:
        if dim.name not in _STRUCTURAL_DIMENSIONS and not dim.evidence_ids:
            dim.unsupported = True
            dim.raw = 0.0
        dim.contribution = round(dim.weight * dim.raw, 6)

    base = sum(d.contribution for d in dimensions)
    overall = round(100 * base)

    modifiers: list[ScoreModifier] = []
    disqualified = inputs.company.industry in inputs.play_spec.excluded_industries
    if disqualified:
        capped = min(overall, 25)
        modifiers.append(
            ScoreModifier(
                name="hard_disqualifier",
                reason=f"industry '{inputs.company.industry}' is on the exclude list",
                detail=f"overall capped from {overall} to {capped}",
            )
        )
        overall = capped

    supported = sum(1 for d in dimensions if not d.unsupported)
    confidence = supported / len(dimensions)

    return ICPScore(
        prospect_id=prospect_id,
        overall=overall,
        dimensions=dimensions,
        modifiers=modifiers,
        disqualified=disqualified,
        confidence=confidence,
        rubric_version=RUBRIC_VERSION,
        explanation="",
    )
