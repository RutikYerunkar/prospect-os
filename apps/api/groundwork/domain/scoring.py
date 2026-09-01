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

from groundwork.models.enums import DimensionSupport, ExclusionEvaluation
from groundwork.models.schemas import (
    Contact,
    DimensionScore,
    EmployeeCountProfileFact,
    Evidence,
    FundingEvent,
    HiringRole,
    ICPScore,
    IndustryProfileFact,
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
    # H1 Phase 7 — industry_fit/size_fit read ONLY these independently
    # grounded profile facts, never `company.industry`/`company.
    # employee_count` (kept on `company` purely as the pre-research seed
    # display identity — a scoring dimension reading it directly is exactly
    # the "naked CompanySeed metadata earns score support" bug H1 closes).
    industry_fact: IndustryProfileFact | None = None
    employee_count_fact: EmployeeCountProfileFact | None = None


def _industry_fit(inputs: ScoringInputs) -> DimensionScore:
    """Reads ONLY the independently grounded `IndustryProfileFact` — never
    `inputs.company.industry`. Ungrounded (no evidence) or unclassified (no
    category) -> UNKNOWN, raw 0, excluded from the confidence denominator.
    """
    fact = inputs.industry_fact
    spec = inputs.play_spec
    if fact is None or not fact.evidence_ids or fact.category is None:
        return DimensionScore(
            name="industry_fit", raw=0.0, weight=WEIGHTS["industry_fit"], contribution=0.0,
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNKNOWN,
        )

    category = fact.category
    if category in spec.target_industries:
        raw = 1.0
    elif any(category in adj for adj in spec.adjacent_industries.values()) or any(
        adj_target in spec.target_industries
        for adj_target in spec.adjacent_industries.get(category, [])
    ):
        raw = 0.6
    else:
        raw = 0.0  # OTHER, or a served-but-unrelated category
    return DimensionScore(
        name="industry_fit", raw=raw, weight=WEIGHTS["industry_fit"], contribution=0.0,
        evidence_ids=list(fact.evidence_ids), unsupported=False, support=DimensionSupport.SUPPORTED,
    )


def _size_fit(inputs: ScoringInputs) -> DimensionScore:
    """Reads ONLY the independently grounded `EmployeeCountProfileFact` —
    never `inputs.company.employee_count`. Ungrounded -> UNKNOWN, raw 0,
    excluded from the confidence denominator. Exact count only — never a
    model-authored `size_band` range."""
    fact = inputs.employee_count_fact
    spec = inputs.play_spec
    if fact is None or not fact.evidence_ids or fact.employee_count is None:
        return DimensionScore(
            name="size_fit", raw=0.0, weight=WEIGHTS["size_fit"], contribution=0.0,
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNKNOWN,
        )

    count = fact.employee_count
    lo, hi = spec.size_band_min, spec.size_band_max
    if lo <= count <= hi:
        raw = 1.0
    else:
        band_width = max(hi - lo, 1)
        distance = (lo - count) if count < lo else (count - hi)
        raw = max(0.0, 1.0 - distance / band_width)
    return DimensionScore(
        name="size_fit", raw=raw, weight=WEIGHTS["size_fit"], contribution=0.0,
        evidence_ids=list(fact.evidence_ids), unsupported=False, support=DimensionSupport.SUPPORTED,
    )


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
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    newest = max(grounded_events, key=lambda e: e.announced_at or date.min)
    stage_score = _stage_match(newest.stage, inputs.play_spec.target_funding_stages)
    recency = _recency_decay(inputs.reference_date, newest.announced_at, 180.0)
    raw = stage_score * recency
    return DimensionScore(
        name="funding_signal", raw=raw, weight=WEIGHTS["funding_signal"], contribution=0.0,
        evidence_ids=list(newest.evidence_ids), unsupported=False, support=DimensionSupport.SUPPORTED,
    )


def _hiring_signal(inputs: ScoringInputs) -> DimensionScore:
    relevant = [r for r in inputs.hiring_roles if r.is_gtm and r.evidence_ids]
    if not relevant:
        return DimensionScore(
            name="hiring_signal", raw=0.0, weight=WEIGHTS["hiring_signal"], contribution=0.0,
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    role_score = min(1.0, len(relevant) / 3.0)
    newest = max(relevant, key=lambda r: r.posted_at or date.min)
    recency = _recency_decay(inputs.reference_date, newest.posted_at, 180.0)
    raw = role_score * recency
    evidence_ids = sorted({eid for r in relevant for eid in r.evidence_ids})
    return DimensionScore(
        name="hiring_signal", raw=raw, weight=WEIGHTS["hiring_signal"], contribution=0.0,
        evidence_ids=evidence_ids, unsupported=False, support=DimensionSupport.SUPPORTED,
    )


def _tech_fit(inputs: ScoringInputs) -> DimensionScore:
    grounded = [t for t in inputs.tech_mentions if t.evidence_ids]
    if not grounded:
        return DimensionScore(
            name="tech_fit", raw=0.0, weight=WEIGHTS["tech_fit"], contribution=0.0,
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    detected = {t.name.lower() for t in grounded}
    target = {t.lower() for t in inputs.play_spec.target_technologies}
    union = detected | target
    raw = len(detected & target) / len(union) if union else 0.0
    evidence_ids = sorted({eid for t in grounded for eid in t.evidence_ids})
    return DimensionScore(
        name="tech_fit", raw=raw, weight=WEIGHTS["tech_fit"], contribution=0.0,
        evidence_ids=evidence_ids, unsupported=False, support=DimensionSupport.SUPPORTED,
    )


def _persona_availability(inputs: ScoringInputs) -> DimensionScore:
    contact = inputs.contact
    if contact is None or not contact.evidence_ids:
        return DimensionScore(
            name="persona_availability", raw=0.0, weight=WEIGHTS["persona_availability"],
            contribution=0.0, evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    raw = PERSONA_RAW.get(contact.verification.value, 0.0)
    return DimensionScore(
        name="persona_availability", raw=raw, weight=WEIGHTS["persona_availability"],
        contribution=0.0, evidence_ids=list(contact.evidence_ids), unsupported=False,
        support=DimensionSupport.SUPPORTED,
    )


def _signal_freshness(inputs: ScoringInputs) -> DimensionScore:
    dated_funding = [(e.announced_at, e.evidence_ids) for e in inputs.funding_events if e.evidence_ids and e.announced_at]
    dated_hiring = [(r.posted_at, r.evidence_ids) for r in inputs.hiring_roles if r.evidence_ids and r.posted_at]
    dated = dated_funding + dated_hiring
    if not dated:
        return DimensionScore(
            name="signal_freshness", raw=0.0, weight=WEIGHTS["signal_freshness"], contribution=0.0,
            evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    newest_date, evidence_ids = max(dated, key=lambda item: item[0])
    raw = _recency_decay(inputs.reference_date, newest_date, 90.0)
    return DimensionScore(
        name="signal_freshness", raw=raw, weight=WEIGHTS["signal_freshness"], contribution=0.0,
        evidence_ids=list(evidence_ids), unsupported=False, support=DimensionSupport.SUPPORTED,
    )


def _evidence_confidence(inputs: ScoringInputs) -> DimensionScore:
    if not inputs.evidence:
        return DimensionScore(
            name="evidence_confidence", raw=0.0, weight=WEIGHTS["evidence_confidence"],
            contribution=0.0, evidence_ids=[], unsupported=True, support=DimensionSupport.UNSUPPORTED,
        )
    raw = sum(e.confidence for e in inputs.evidence) / len(inputs.evidence)
    return DimensionScore(
        name="evidence_confidence", raw=raw, weight=WEIGHTS["evidence_confidence"],
        contribution=0.0, evidence_ids=[e.id for e in inputs.evidence], unsupported=False,
        support=DimensionSupport.SUPPORTED,
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


EXCLUSION_NOT_EVALUABLE_MODIFIER = "exclusion_not_evaluable"
HARD_DISQUALIFIER_MODIFIER = "hard_disqualifier"


def _evaluate_exclusion(category: str | None, excluded_industries: list[str]) -> ExclusionEvaluation:
    """Tri-state exclusion-policy evaluation (H1 Phase 7). `category` here
    is already the independently *grounded* category (or `None`) — never
    read from `CompanySeed`."""
    if category is None:
        return ExclusionEvaluation.UNKNOWN
    if category in excluded_industries:
        return ExclusionEvaluation.EXCLUDED
    return ExclusionEvaluation.NOT_EXCLUDED


def exclusion_status_from_persisted(*, disqualified: bool, modifiers: list[dict]) -> ExclusionEvaluation:
    """Reconstruct the tri-state exclusion evaluation from a persisted
    `ICPScoreRow` alone — `disqualified` (bool) and `modifiers` (the JSON
    list of `{name, reason, detail}` dicts `ICPScoreRow.modifiers` already
    stores) are sufficient; no dedicated `exclusion_status` column exists
    because this pair already represents all three states unambiguously
    (H1 deviation-closure investigation, see docs/PROGRESS.md):

    - EXCLUDED: `compute_score` only ever sets `disqualified=True` when
      `exclusion_status == EXCLUDED` — the two are equivalent by
      construction (`groundwork.models.schemas.ICPScore` isn't required
      here at all).
    - UNKNOWN: `disqualified=False` and `modifiers` contains a
      `"exclusion_not_evaluable"` entry — `compute_score` adds this
      modifier if and only if the exclusion status is `UNKNOWN`.
    - NOT_EXCLUDED: `disqualified=False` and no such modifier.

    Takes plain `bool`/`list[dict]` (exactly what a repository read off
    `ICPScoreRow` yields) rather than an in-memory `ICPScore` — this must
    work after a process restart, with zero `ProspectContext`/`ICPScore`
    Python objects from the original execution still alive.
    """
    if disqualified:
        return ExclusionEvaluation.EXCLUDED
    if any(m.get("name") == EXCLUSION_NOT_EVALUABLE_MODIFIER for m in modifiers):
        return ExclusionEvaluation.UNKNOWN
    return ExclusionEvaluation.NOT_EXCLUDED


def exclusion_reason_from_persisted(modifiers: list[dict]) -> str | None:
    """The exact UNKNOWN-exclusion reason text, read back from a persisted
    `ICPScoreRow.modifiers` list — `None` if no such modifier is present
    (EXCLUDED and NOT_EXCLUDED both carry no `exclusion_not_evaluable`
    entry)."""
    for m in modifiers:
        if m.get("name") == EXCLUSION_NOT_EVALUABLE_MODIFIER:
            return m.get("detail")
    return None


def compute_score(prospect_id: str, inputs: ScoringInputs) -> ICPScore:
    dimensions = [fn(inputs) for fn in _DIMENSION_FNS]

    # Evidence gate, enforced defensively even though each dimension fn
    # already zeroes itself out when ungrounded: a dimension with no
    # supporting evidence can never contribute points. Every dimension is
    # evidence-gated now — there is no structural exemption left (H1 Phase
    # 7 deleted it): industry_fit/size_fit are gated exactly like the other
    # six, through their own grounded profile facts.
    for dim in dimensions:
        if not dim.evidence_ids:
            dim.unsupported = True
            if dim.support == DimensionSupport.SUPPORTED:
                dim.support = DimensionSupport.UNSUPPORTED
            dim.raw = 0.0
        dim.contribution = round(dim.weight * dim.raw, 6)

    base = sum(d.contribution for d in dimensions)
    overall = round(100 * base)

    # Exclusion policy is evaluated from the SAME independently grounded
    # industry category the industry_fit dimension used — never
    # `inputs.company.industry`.
    grounded_category = (
        inputs.industry_fact.category
        if inputs.industry_fact is not None and inputs.industry_fact.evidence_ids
        else None
    )
    exclusion_status = _evaluate_exclusion(grounded_category, inputs.play_spec.excluded_industries)

    modifiers: list[ScoreModifier] = []
    disqualified = exclusion_status == ExclusionEvaluation.EXCLUDED
    if disqualified:
        capped = min(overall, 25)
        modifiers.append(
            ScoreModifier(
                name=HARD_DISQUALIFIER_MODIFIER,
                reason=f"industry '{grounded_category}' is on the exclude list",
                detail=f"overall capped from {overall} to {capped}",
            )
        )
        overall = capped
    elif exclusion_status == ExclusionEvaluation.UNKNOWN:
        # Never silently pass: surfaced as a modifier here, and
        # `engine/runner.py::_derive_final_status` forces NEEDS_REVIEW for
        # it rather than adding an eighth review guardrail — the seven
        # deterministic checks stay exactly seven. This modifier (name +
        # `detail` text) is also the ONLY persisted representation of the
        # UNKNOWN exclusion state — see `exclusion_status_from_persisted()`
        # above. Changing this string is a persisted-data-shape change.
        modifiers.append(
            ScoreModifier(
                name=EXCLUSION_NOT_EVALUABLE_MODIFIER,
                reason="industry was not established from evidence",
                detail="Exclusion policy could not be evaluated because industry was not established from evidence.",
            )
        )

    # UNKNOWN dimensions are excluded from the confidence denominator
    # entirely (H1 Phase 7) — a fact that was never independently
    # established should neither help nor hurt confidence. UNSUPPORTED
    # still counts in the denominator (it WAS checked for, and wasn't
    # found) — this preserves the pre-H1 "confidence = coverage" semantics
    # for every dimension that isn't in an UNKNOWN state.
    evaluable = [d for d in dimensions if d.support != DimensionSupport.UNKNOWN]
    supported = sum(1 for d in evaluable if d.support == DimensionSupport.SUPPORTED)
    confidence = supported / len(evaluable) if evaluable else 0.0

    return ICPScore(
        prospect_id=prospect_id,
        overall=overall,
        dimensions=dimensions,
        modifiers=modifiers,
        disqualified=disqualified,
        confidence=confidence,
        rubric_version=RUBRIC_VERSION,
        explanation="",
        exclusion_status=exclusion_status,
    )
