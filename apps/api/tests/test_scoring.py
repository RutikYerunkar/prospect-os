from datetime import date, timedelta

from groundwork.domain.scoring import WEIGHTS, ScoringInputs, compute_score
from groundwork.models.enums import ContactVerification, EvidenceOrigin, ExclusionEvaluation
from groundwork.models.schemas import (
    CompanySeed,
    Contact,
    EmployeeCountProfileFact,
    Evidence,
    FundingEvent,
    HiringRole,
    IndustryProfileFact,
    PlaySpec,
    TechMention,
)

TODAY = date(2026, 8, 31)


def _industry_fact(category: str = "ai_infrastructure", eid: str = "ev-industry") -> IndustryProfileFact:
    return IndustryProfileFact(category=category, claim="claim", source_ref="ref", evidence_ids=[eid])


def _employee_fact(count: int = 120, eid: str = "ev-size") -> EmployeeCountProfileFact:
    return EmployeeCountProfileFact(employee_count=count, claim="claim", source_ref="ref", evidence_ids=[eid])


def _company(**overrides) -> CompanySeed:
    defaults = dict(
        slug="acme",
        name="Acme Corp",
        domain="acme.com",
        industry="ai_infrastructure",
        size_band="51-200",
        employee_count=120,
    )
    defaults.update(overrides)
    return CompanySeed(**defaults)


def _play_spec(**overrides) -> PlaySpec:
    defaults = dict(
        objective_text="Find AI infra companies",
        target_industries=["ai_infrastructure"],
        excluded_industries=["retail_pos"],
        adjacent_industries={"data_tooling": ["ai_infrastructure"]},
        size_band_min=50,
        size_band_max=250,
        target_funding_stages=["series_a", "series_b"],
        target_technologies=["kubernetes", "pytorch", "triton"],
        persona_titles=["VP of Sales", "Head of Sales"],
        min_score=60,
        min_confidence=0.6,
        target_count=6,
    )
    defaults.update(overrides)
    return PlaySpec(**defaults)


def _evidence(eid: str, confidence: float = 0.85) -> Evidence:
    return Evidence(
        id=eid,
        prospect_id="p-1",
        source_provider="demo_fixture",
        title="note",
        claim="claim",
        snippet="snippet",
        confidence=confidence,
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )


def test_weights_sum_to_one() -> None:
    assert round(sum(WEIGHTS.values()), 6) == 1.0


def test_full_evidence_prospect_scores_high_and_fully_confident() -> None:
    inputs = ScoringInputs(
        company=_company(),
        play_spec=_play_spec(),
        funding_events=[
            FundingEvent(stage="series_b", announced_at=TODAY - timedelta(days=60), evidence_ids=["ev-fund"])
        ],
        hiring_roles=[
            HiringRole(title="VP Sales", is_gtm=True, posted_at=TODAY - timedelta(days=10), evidence_ids=["ev-hire"]),
            HiringRole(title="AE", is_gtm=True, posted_at=TODAY - timedelta(days=12), evidence_ids=["ev-hire"]),
            HiringRole(title="SDR", is_gtm=True, posted_at=TODAY - timedelta(days=15), evidence_ids=["ev-hire"]),
        ],
        tech_mentions=[
            TechMention(name="kubernetes", evidence_ids=["ev-tech"]),
            TechMention(name="pytorch", evidence_ids=["ev-tech"]),
            TechMention(name="triton", evidence_ids=["ev-tech"]),
        ],
        contact=Contact(
            prospect_id="p-1", full_name="Jane Doe", title="VP of Sales",
            verification=ContactVerification.VERIFIED, evidence_ids=["ev-contact"],
        ),
        evidence=[_evidence("ev-fund"), _evidence("ev-hire"), _evidence("ev-tech"), _evidence("ev-contact")],
        reference_date=TODAY,
        industry_fact=_industry_fact(),
        employee_count_fact=_employee_fact(120),
    )
    score = compute_score("p-1", inputs)
    assert score.overall >= 80
    assert score.confidence == 1.0
    assert score.disqualified is False
    assert all(not d.unsupported for d in score.dimensions)


def test_hard_disqualifier_caps_score_at_25() -> None:
    inputs = ScoringInputs(
        company=_company(industry="retail_pos"),
        play_spec=_play_spec(),
        evidence=[],
        industry_fact=_industry_fact("retail_pos"),
    )
    score = compute_score("p-1", inputs)
    assert score.disqualified is True
    assert score.exclusion_status == ExclusionEvaluation.EXCLUDED
    assert score.overall <= 25
    assert score.modifiers and score.modifiers[0].name == "hard_disqualifier"


def test_unsupported_dimension_contributes_zero() -> None:
    inputs = ScoringInputs(company=_company(), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    funding_dim = next(d for d in score.dimensions if d.name == "funding_signal")
    assert funding_dim.unsupported is True
    assert funding_dim.raw == 0.0
    assert funding_dim.contribution == 0.0


def test_same_input_produces_same_score() -> None:
    inputs = ScoringInputs(company=_company(), play_spec=_play_spec(), evidence=[])
    first = compute_score("p-1", inputs)
    second = compute_score("p-1", inputs)
    assert first.overall == second.overall
    assert first.confidence == second.confidence


def test_confidence_equals_supported_dimension_coverage() -> None:
    inputs = ScoringInputs(
        company=_company(), play_spec=_play_spec(), evidence=[],
        industry_fact=_industry_fact(), employee_count_fact=_employee_fact(),
    )
    score = compute_score("p-1", inputs)
    supported = sum(1 for d in score.dimensions if not d.unsupported)
    assert score.confidence == supported / len(score.dimensions)


def test_industry_fit_exact_adjacent_unrelated_boundaries() -> None:
    spec = _play_spec()
    exact = compute_score("p-1", ScoringInputs(
        company=_company(industry="ai_infrastructure"), play_spec=spec, evidence=[],
        industry_fact=_industry_fact("ai_infrastructure"),
    ))
    adjacent = compute_score("p-1", ScoringInputs(
        company=_company(industry="data_tooling"), play_spec=spec, evidence=[],
        industry_fact=_industry_fact("data_tooling"),
    ))
    unrelated = compute_score("p-1", ScoringInputs(
        company=_company(industry="widgets"), play_spec=spec, evidence=[],
        industry_fact=_industry_fact("widgets"),
    ))

    def dim(score, name):
        return next(d for d in score.dimensions if d.name == name)

    assert dim(exact, "industry_fit").raw == 1.0
    assert dim(adjacent, "industry_fit").raw == 0.6
    assert dim(unrelated, "industry_fit").raw == 0.0


def test_size_fit_inside_band_is_perfect_outside_decays() -> None:
    spec = _play_spec(size_band_min=50, size_band_max=250)
    inside = compute_score("p-1", ScoringInputs(
        company=_company(employee_count=150), play_spec=spec, evidence=[],
        employee_count_fact=_employee_fact(150),
    ))
    outside = compute_score("p-1", ScoringInputs(
        company=_company(employee_count=500), play_spec=spec, evidence=[],
        employee_count_fact=_employee_fact(500),
    ))

    def dim(score, name):
        return next(d for d in score.dimensions if d.name == name)

    assert dim(inside, "size_fit").raw == 1.0
    assert 0.0 <= dim(outside, "size_fit").raw < 1.0


# --- H1 Phase 7: scoring honesty regressions --------------------------------


def test_industry_fit_ignores_company_seed_when_no_profile_fact() -> None:
    """`CompanySeed.industry` alone must never earn score support — without
    an independently grounded `IndustryProfileFact`, industry_fit is
    UNKNOWN regardless of what `company.industry` says."""
    inputs = ScoringInputs(company=_company(industry="ai_infrastructure"), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    dim = next(d for d in score.dimensions if d.name == "industry_fit")
    assert dim.raw == 0.0
    assert dim.support.value == "UNKNOWN"
    assert score.exclusion_status == ExclusionEvaluation.UNKNOWN


def test_size_fit_ignores_company_seed_when_no_profile_fact() -> None:
    inputs = ScoringInputs(company=_company(employee_count=150), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    dim = next(d for d in score.dimensions if d.name == "size_fit")
    assert dim.raw == 0.0
    assert dim.support.value == "UNKNOWN"


def test_company_seed_industry_disagreeing_with_fact_is_ignored() -> None:
    """Changing `company.industry` alone (fact unchanged) must not move
    industry_fit at all — only the grounded fact can."""
    spec = _play_spec()
    seed_says_excluded = compute_score("p-1", ScoringInputs(
        company=_company(industry="retail_pos"), play_spec=spec, evidence=[],
        industry_fact=_industry_fact("ai_infrastructure"),
    ))
    seed_says_target = compute_score("p-1", ScoringInputs(
        company=_company(industry="ai_infrastructure"), play_spec=spec, evidence=[],
        industry_fact=_industry_fact("ai_infrastructure"),
    ))
    assert seed_says_excluded.disqualified is False
    assert seed_says_target.disqualified is False
    dim = lambda s: next(d for d in s.dimensions if d.name == "industry_fit")  # noqa: E731
    assert dim(seed_says_excluded).raw == dim(seed_says_target).raw == 1.0


def test_unknown_dimension_excluded_from_confidence_denominator() -> None:
    """UNKNOWN dimensions (no grounded profile fact) are excluded from the
    confidence denominator entirely — never counted as either supported or
    unsupported."""
    inputs = ScoringInputs(company=_company(), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    evaluable = [d for d in score.dimensions if d.support.value != "UNKNOWN"]
    assert len(evaluable) < len(score.dimensions)
    supported = sum(1 for d in evaluable if d.support.value == "SUPPORTED")
    assert score.confidence == supported / len(evaluable)


def test_exclusion_unknown_when_industry_not_grounded() -> None:
    inputs = ScoringInputs(company=_company(), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    assert score.exclusion_status == ExclusionEvaluation.UNKNOWN
    assert score.disqualified is False
    assert any(m.name == "exclusion_not_evaluable" for m in score.modifiers)


def test_exclusion_not_excluded_when_industry_grounded_and_not_on_exclude_list() -> None:
    inputs = ScoringInputs(
        company=_company(), play_spec=_play_spec(), evidence=[],
        industry_fact=_industry_fact("ai_infrastructure"),
    )
    score = compute_score("p-1", inputs)
    assert score.exclusion_status == ExclusionEvaluation.NOT_EXCLUDED
    assert not any(m.name == "exclusion_not_evaluable" for m in score.modifiers)
