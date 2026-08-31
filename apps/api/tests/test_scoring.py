from datetime import date, timedelta

from groundwork.domain.scoring import WEIGHTS, ScoringInputs, compute_score
from groundwork.models.enums import ContactVerification, EvidenceOrigin
from groundwork.models.schemas import (
    CompanySeed,
    Contact,
    Evidence,
    FundingEvent,
    HiringRole,
    PlaySpec,
    TechMention,
)

TODAY = date(2026, 8, 31)


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
    )
    score = compute_score("p-1", inputs)
    assert score.disqualified is True
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
    inputs = ScoringInputs(company=_company(), play_spec=_play_spec(), evidence=[])
    score = compute_score("p-1", inputs)
    supported = sum(1 for d in score.dimensions if not d.unsupported)
    assert score.confidence == supported / len(score.dimensions)


def test_industry_fit_exact_adjacent_unrelated_boundaries() -> None:
    spec = _play_spec()
    exact = compute_score("p-1", ScoringInputs(company=_company(industry="ai_infrastructure"), play_spec=spec, evidence=[]))
    adjacent = compute_score("p-1", ScoringInputs(company=_company(industry="data_tooling"), play_spec=spec, evidence=[]))
    unrelated = compute_score("p-1", ScoringInputs(company=_company(industry="widgets"), play_spec=spec, evidence=[]))

    def dim(score, name):
        return next(d for d in score.dimensions if d.name == name)

    assert dim(exact, "industry_fit").raw == 1.0
    assert dim(adjacent, "industry_fit").raw == 0.6
    assert dim(unrelated, "industry_fit").raw == 0.0


def test_size_fit_inside_band_is_perfect_outside_decays() -> None:
    spec = _play_spec(size_band_min=50, size_band_max=250)
    inside = compute_score("p-1", ScoringInputs(company=_company(employee_count=150), play_spec=spec, evidence=[]))
    outside = compute_score("p-1", ScoringInputs(company=_company(employee_count=500), play_spec=spec, evidence=[]))

    def dim(score, name):
        return next(d for d in score.dimensions if d.name == name)

    assert dim(inside, "size_fit").raw == 1.0
    assert 0.0 <= dim(outside, "size_fit").raw < 1.0
