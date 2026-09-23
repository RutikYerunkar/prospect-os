"""v2.0.1 Live-Quality Hardening — deterministic event-date grounding.

Exercised directly against `engine/steps/signals.py`, the same mechanism
`test_profile_provenance.py` uses for the two profile facts. `announced_at`
must survive only when the exact claimed date is fully spelled out (year +
month + day) in the cited `LIVE_FETCH` evidence's own snippet and is not in
the future; Demo Mode's fixture-derived `DEMO_FIXTURE` evidence is never
subject to this check (its `announced_at` is deterministically computed
from `announced_days_ago`, never LLM-extracted from text) — see
`domain/grounding.py::date_claim_supported()`'s module note on why
`published_at` is never consulted either.
"""

from __future__ import annotations

from datetime import date

from groundwork.domain.grounding import date_claim_supported
from groundwork.engine.context import ProspectContext
from groundwork.engine.steps.signals import signals
from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import (
    CompanyProfileFacts,
    CompanySeed,
    EmployeeCountProfileFact,
    Evidence,
    FundingEvent,
    IndustryProfileFact,
    PlaySpec,
    ResearchFacts,
)


class _Noop:
    async def record(self, *a, **kw) -> None: ...
    async def has_succeeded(self, *a, **kw) -> bool:
        return False
    async def emit(self, *a, **kw) -> None: ...


def _play_spec() -> PlaySpec:
    return PlaySpec(objective_text="t", target_industries=["ai_infrastructure"])


def _company() -> CompanySeed:
    return CompanySeed(
        slug="acme", name="Acme Corp", domain="acme.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=120,
    )


def _evidence(snippet: str, *, origin: EvidenceOrigin, source_url: str | None = None) -> Evidence:
    return Evidence(
        id="ev-1", prospect_id="p-1", source_provider="tavily" if origin == EvidenceOrigin.LIVE_FETCH else "demo_fixture",
        title="note", claim="Acme Corp raised a Series B", snippet=snippet, confidence=0.9,
        origin=origin, source_url=source_url,
    )


def _ctx(*, evidence: Evidence, funding_event: FundingEvent, reference_date: date) -> ProspectContext:
    ctx = ProspectContext(
        run_id="r-1", prospect_id="p-1", company=_company(), dedupe_key="k",
        play_spec=_play_spec(), providers=None, reference_date=reference_date,  # type: ignore[arg-type]
        trace=_Noop(), events=_Noop(), llm_calls=_Noop(), search_calls=_Noop(),  # type: ignore[arg-type]
        enrichment_calls=_Noop(),  # type: ignore[arg-type]
    )
    ctx.evidence = [evidence]
    ctx.facts = ResearchFacts(
        company=_company(),
        funding_events=[funding_event],
        profile=CompanyProfileFacts(
            industry=IndustryProfileFact(), employee_count=EmployeeCountProfileFact()
        ),
    )
    return ctx


async def test_live_fetch_date_grounded_when_spelled_out_in_snippet() -> None:
    ev = _evidence(
        "Acme Corp raised a Series B on March 15, 2024 to expand its platform.",
        origin=EvidenceOrigin.LIVE_FETCH, source_url="https://example.com/acme-series-b",
    )
    fe = FundingEvent(
        stage="series_b", announced_at=date(2024, 3, 15), claim="Acme Corp raised a Series B",
        source_ref="ref", evidence_ids=["ev-1"],
    )
    ctx = _ctx(evidence=ev, funding_event=fe, reference_date=date(2024, 6, 1))
    await signals(ctx)
    assert ctx.facts.funding_events[0].announced_at == date(2024, 3, 15)


async def test_live_fetch_date_nulled_when_not_textually_present() -> None:
    ev = _evidence(
        "Acme Corp raised a Series B round to expand its platform.",
        origin=EvidenceOrigin.LIVE_FETCH, source_url="https://example.com/acme-series-b",
    )
    fe = FundingEvent(
        stage="series_b", announced_at=date(2024, 3, 15), claim="Acme Corp raised a Series B",
        source_ref="ref", evidence_ids=["ev-1"],
    )
    ctx = _ctx(evidence=ev, funding_event=fe, reference_date=date(2024, 6, 1))
    await signals(ctx)
    assert ctx.facts.funding_events[0].announced_at is None


async def test_live_fetch_date_nulled_when_in_the_future() -> None:
    ev = _evidence(
        "Acme Corp raised a Series B on March 15, 2025 to expand its platform.",
        origin=EvidenceOrigin.LIVE_FETCH, source_url="https://example.com/acme-series-b",
    )
    fe = FundingEvent(
        stage="series_b", announced_at=date(2025, 3, 15), claim="Acme Corp raised a Series B",
        source_ref="ref", evidence_ids=["ev-1"],
    )
    # reference_date is BEFORE the claimed date — the claim is from the future.
    ctx = _ctx(evidence=ev, funding_event=fe, reference_date=date(2024, 6, 1))
    await signals(ctx)
    assert ctx.facts.funding_events[0].announced_at is None


async def test_demo_fixture_origin_is_never_date_checked() -> None:
    # No calendar date anywhere in this snippet — a real DEMO_FIXTURE
    # snippet never has one (see demo_pack.yaml) — yet the date survives,
    # because DEMO_FIXTURE evidence is never subject to this check at all.
    ev = _evidence(
        "Acme Corp today announced it has closed a Series B financing round.",
        origin=EvidenceOrigin.DEMO_FIXTURE,
    )
    fe = FundingEvent(
        stage="series_b", announced_at=date(2024, 3, 15), claim="Acme Corp raised a Series B",
        source_ref="ref", evidence_ids=["ev-1"],
    )
    ctx = _ctx(evidence=ev, funding_event=fe, reference_date=date(2024, 6, 1))
    await signals(ctx)
    assert ctx.facts.funding_events[0].announced_at == date(2024, 3, 15)


def test_published_at_is_never_consulted() -> None:
    # date_claim_supported() takes only (snippet, claimed_date,
    # reference_date) — there is no parameter through which a
    # SourceDocument.published_at could be threaded in, structurally.
    assert date_claim_supported(
        "no date spelled out here, just prose.", date(2024, 3, 15), date(2024, 6, 1)
    ) is False


def test_bare_year_or_month_year_is_not_fully_specified() -> None:
    assert date_claim_supported("Announced in 2024.", date(2024, 3, 15), date(2024, 6, 1)) is False
    assert date_claim_supported("Announced in March 2024.", date(2024, 3, 15), date(2024, 6, 1)) is False


def test_iso_and_slash_forms_are_recognized() -> None:
    assert date_claim_supported("Filed on 2024-03-15 per records.", date(2024, 3, 15), date(2024, 6, 1)) is True
    assert date_claim_supported("Filed on 03/15/2024 per records.", date(2024, 3, 15), date(2024, 6, 1)) is True
