"""§N.1 — `DemoEnrichmentProvider`: returns observations, never verdicts;
origin is always `DEMO_FIXTURE`; every fixture value is synthetic (no
real-looking LinkedIn URL, no real-looking external email identity);
deterministic across runs; scripted failures and the call budget behave
exactly like the search/LLM boundaries.
"""

from __future__ import annotations

import pytest

from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.models.enums import EnrichmentAttemptStatus, EnrichmentOrigin
from groundwork.providers.contact_base import (
    EnrichmentBudgetExceeded,
    EnrichmentProviderUnavailable,
    PersonEnrichmentQuery,
)
from groundwork.providers.demo.contact_enrichment import DemoEnrichmentProvider
from groundwork.providers.demo.fixtures import load_fixture_pack


def _northwind_query() -> PersonEnrichmentQuery:
    return PersonEnrichmentQuery(
        full_name="Priya Natarajan", title="VP of Sales",
        company_name="Northwind Labs", company_domain="northwindlabs.com",
    )


async def test_matched_result_carries_synthetic_observations_only() -> None:
    pack = load_fixture_pack()
    provider = DemoEnrichmentProvider(pack, seed=1)

    result = await provider.enrich_person(_northwind_query(), ctx_key="r1:p1:contact_enrichment")

    assert result.origin is EnrichmentOrigin.DEMO_FIXTURE
    assert result.matched is True
    assert result.email is not None
    assert result.email.address == "priya.natarajan@northwindlabs.com"
    assert not result.email.address.startswith(("http://", "https://"))
    assert result.linkedin is not None
    assert result.linkedin.profile_url == "demo://linkedin/priya-natarajan"
    assert result.linkedin.profile_url.startswith("demo://linkedin/")
    assert not result.linkedin.profile_url.startswith(("http://", "https://"))
    assert result.telemetry and result.telemetry[0].status == EnrichmentAttemptStatus.OK


async def test_unmatched_company_is_a_legitimate_not_matched_observation() -> None:
    pack = load_fixture_pack()
    provider = DemoEnrichmentProvider(pack, seed=1)
    query = PersonEnrichmentQuery(
        full_name="Someone Nobody", title="Head of Sales",
        company_name="Riverbend Analytics", company_domain="riverbendanalytics.io",
    )
    result = await provider.enrich_person(query, ctx_key="r1:p2:contact_enrichment")
    assert result.matched is False
    assert result.email is None
    assert result.linkedin is None
    assert result.origin is EnrichmentOrigin.DEMO_FIXTURE


async def test_deterministic_across_runs() -> None:
    pack = load_fixture_pack()
    provider_a = DemoEnrichmentProvider(pack, seed=7)
    provider_b = DemoEnrichmentProvider(pack, seed=7)

    result_a = await provider_a.enrich_person(_northwind_query(), ctx_key="r1:p1:contact_enrichment")
    result_b = await provider_b.enrich_person(_northwind_query(), ctx_key="r1:p1:contact_enrichment")

    assert result_a.matched == result_b.matched
    assert result_a.email.address == result_b.email.address
    assert result_a.linkedin.profile_url == result_b.linkedin.profile_url
    assert result_a.raw_digest == result_b.raw_digest


async def test_scripted_failure_raises_then_succeeds_on_next_attempt() -> None:
    # Built directly rather than mutating the loaded singleton pack
    # (`load_fixture_pack` is `lru_cache`d) — mirrors the isolation test's
    # own hand-built `FixturePack` pattern.
    from groundwork.providers.demo.fixtures import FixtureFailureSpec, FixturePack

    pack = load_fixture_pack()
    fixture = pack.company_by_slug("northwind-labs")
    scripted_pack = FixturePack(
        play_spec=pack.play_spec,
        companies=[
            fixture.model_copy(
                update={
                    "enrichment_failure_script": {
                        "person_enrichment": FixtureFailureSpec(
                            fail_attempts=1, error="EnrichmentProviderUnavailable"
                        )
                    }
                }
            )
        ],
    )
    provider = DemoEnrichmentProvider(scripted_pack, seed=1)
    ctx_key = "r1:p1:contact_enrichment"

    with pytest.raises(EnrichmentProviderUnavailable):
        await provider.enrich_person(_northwind_query(), ctx_key=ctx_key)

    result = await provider.enrich_person(_northwind_query(), ctx_key=ctx_key)
    assert result.matched is True


async def test_call_budget_exhaustion_raises_not_attempted_budget() -> None:
    pack = load_fixture_pack()
    budget = EnrichmentCallBudget(max_calls=0)
    provider = DemoEnrichmentProvider(pack, seed=1, budget=budget)

    with pytest.raises(EnrichmentBudgetExceeded) as excinfo:
        await provider.enrich_person(_northwind_query(), ctx_key="r1:p1:contact_enrichment")
    assert excinfo.value.telemetry[0].status == EnrichmentAttemptStatus.NOT_ATTEMPTED_BUDGET
