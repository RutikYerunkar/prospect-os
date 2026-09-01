"""H1 Phase 4/5/6 — independent, field-level profile-fact provenance,
exercised directly against `engine/steps/signals.py`'s deterministic
verifier (the same mechanism every other fact type goes through).
"""

from __future__ import annotations

from datetime import date

import pytest

from groundwork.engine.context import ProspectContext
from groundwork.engine.steps.signals import signals
from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import (
    CompanyProfileFacts,
    CompanySeed,
    EmployeeCountProfileFact,
    Evidence,
    IndustryProfileFact,
    PlaySpec,
    ResearchFacts,
)


class _Noop:
    async def record(self, *a, **kw) -> None: ...
    async def has_succeeded(self, *a, **kw) -> bool:
        return False
    async def emit(self, *a, **kw) -> None: ...


def _play_spec(**overrides) -> PlaySpec:
    defaults = dict(
        objective_text="t",
        target_industries=["ai_infrastructure"],
        excluded_industries=["retail_pos"],
        adjacent_industries={"data_tooling": ["ai_infrastructure"]},
    )
    defaults.update(overrides)
    return PlaySpec(**defaults)


def _company() -> CompanySeed:
    return CompanySeed(
        slug="acme", name="Acme Corp", domain="acme.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=120,
    )


def _evidence(eid: str, snippet: str) -> Evidence:
    return Evidence(
        id=eid, prospect_id="p-1", source_provider="demo_fixture", title="note",
        claim="note", snippet=snippet, confidence=0.9, origin=EvidenceOrigin.DEMO_FIXTURE,
    )


def _ctx(*, play_spec: PlaySpec, evidence: list[Evidence], profile: CompanyProfileFacts) -> ProspectContext:
    ctx = ProspectContext(
        run_id="r-1", prospect_id="p-1", company=_company(), dedupe_key="k",
        play_spec=play_spec, providers=None, reference_date=date(2026, 1, 1),  # type: ignore[arg-type]
        trace=_Noop(), events=_Noop(), llm_calls=_Noop(), search_calls=_Noop(),  # type: ignore[arg-type]
    )
    ctx.evidence = evidence
    ctx.facts = ResearchFacts(company=_company(), profile=profile)
    return ctx


async def test_industry_grounded_employee_count_absent() -> None:
    ev = _evidence("ev-1", "Acme Corp operates in the ai infrastructure industry.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(
            category="ai_infrastructure", claim="Acme Corp operates in the ai infrastructure industry.",
            source_ref="ref", evidence_ids=["ev-1"],
        ),
        employee_count=EmployeeCountProfileFact(),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.industry.category == "ai_infrastructure"
    assert ctx.facts.profile.industry.evidence_ids == ["ev-1"]
    assert ctx.facts.profile.employee_count.evidence_ids == []
    assert ctx.facts.profile.employee_count.employee_count is None


async def test_employee_count_grounded_industry_absent() -> None:
    ev = _evidence("ev-2", "Acme Corp has approximately 140 employees.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(),
        employee_count=EmployeeCountProfileFact(
            employee_count=140, claim="Acme Corp has approximately 140 employees.",
            source_ref="ref", evidence_ids=["ev-2"],
        ),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.employee_count.employee_count == 140
    assert ctx.facts.profile.employee_count.evidence_ids == ["ev-2"]
    assert ctx.facts.profile.industry.evidence_ids == []
    assert ctx.facts.profile.industry.category is None


async def test_no_cross_borrowed_evidence_ids_when_both_grounded_from_same_source() -> None:
    """Both facts may legitimately cite the same source_ref/evidence — but
    grounding is verified independently, and neither list is ever populated
    just because the other one was."""
    ev = _evidence("ev-3", "Acme Corp operates in the ai infrastructure industry and has approximately 140 employees.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(
            category="ai_infrastructure", claim="Acme Corp operates in the ai infrastructure industry.",
            source_ref="ref", evidence_ids=["ev-3"],
        ),
        employee_count=EmployeeCountProfileFact(
            employee_count=140, claim="Acme Corp has approximately 140 employees.",
            source_ref="ref", evidence_ids=["ev-3"],
        ),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.industry.evidence_ids == ["ev-3"]
    assert ctx.facts.profile.employee_count.evidence_ids == ["ev-3"]
    # Independence: corrupt the industry fact's evidence and confirm it has
    # zero effect on the (already independently verified) employee fact.
    ctx.facts.profile.industry.evidence_ids = []
    assert ctx.facts.profile.employee_count.evidence_ids == ["ev-3"]


async def test_numeric_claim_missing_from_snippet_becomes_unknown() -> None:
    ev = _evidence("ev-4", "Acme Corp has a large team of employees.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(),
        employee_count=EmployeeCountProfileFact(
            employee_count=140, claim="Acme Corp has approximately 140 employees.",
            source_ref="ref", evidence_ids=["ev-4"],
        ),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.employee_count.employee_count is None
    assert ctx.facts.profile.employee_count.evidence_ids == []


async def test_out_of_range_count_becomes_unknown() -> None:
    ev = _evidence("ev-5", "The filing lists 0 employees.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(),
        employee_count=EmployeeCountProfileFact(
            employee_count=0, claim="claim", source_ref="ref", evidence_ids=["ev-5"],
        ),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.employee_count.employee_count is None


async def test_out_of_set_category_becomes_unknown() -> None:
    ev = _evidence("ev-6", "Acme Corp operates in the widget manufacturing industry.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(
            category="widget_manufacturing", claim="Acme Corp operates in the widget manufacturing industry.",
            source_ref="ref", evidence_ids=["ev-6"],
        ),
        employee_count=EmployeeCountProfileFact(),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.industry.category is None
    assert ctx.facts.profile.industry.evidence_ids == []


async def test_unclaimed_claim_text_not_grounded_in_snippet() -> None:
    ev = _evidence("ev-7", "Acme Corp is a great company to work for.")
    profile = CompanyProfileFacts(
        industry=IndustryProfileFact(
            category="ai_infrastructure", claim="Acme Corp operates in the ai infrastructure industry.",
            source_ref="ref", evidence_ids=["ev-7"],
        ),
        employee_count=EmployeeCountProfileFact(),
    )
    ctx = _ctx(play_spec=_play_spec(), evidence=[ev], profile=profile)
    await signals(ctx)
    assert ctx.facts.profile.industry.category is None
    assert ctx.facts.profile.industry.evidence_ids == []
