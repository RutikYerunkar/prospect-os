"""H2 Phase 22 — real per-company source retrieval (Phase 10/11): include_domains
scoping, source-query hard bounds, duplicate-URL winner selection, only
winners get Extracted, and the full `engine/steps/research.py` step produces
real `LIVE_FETCH` Evidence with provider-originated URLs. No network calls.
"""

from __future__ import annotations

import json
from datetime import date

from groundwork.engine.context import ProspectContext
from groundwork.engine.search import call_search
from groundwork.models.enums import EvidenceOrigin
from groundwork.models.schemas import CompanySeed
from groundwork.observability.enrichment_calls import EnrichmentCallRecorder
from groundwork.observability.events import EventEmitter
from groundwork.observability.llm_calls import LLMCallRecorder
from groundwork.observability.search_calls import SearchCallRecorder
from groundwork.providers.base import LLMOperation, LLMResult, ProviderBundle
from groundwork.models.llm_io import ResearchExtractionOutput
from groundwork.models.schemas import CompanyProfileFacts, ResearchFacts
from groundwork.repositories.llm_calls import LLMCallRepository
from groundwork.repositories.search import SearchRepository
from groundwork.engine.runner import Repos
from tests.search_live_helpers import make_search_provider, search_response, search_result

COMPANY = CompanySeed(
    slug="acme-robotics", name="Acme Robotics", domain="acme-robotics.com",
    industry="unknown", size_band="unknown", employee_count=0, hq_country="unknown",
)


async def test_include_domains_sent_on_every_source_query() -> None:
    provider, transport = make_search_provider(
        [(200, search_response(results=[])) for _ in range(3)], max_source_queries_per_prospect=3
    )
    await provider.fetch_sources(COMPANY, ctx_key="run1:p1:research")
    assert transport.calls == 3
    for request in transport.requests:
        body = json.loads(request.content)
        assert body.get("include_domains") == [COMPANY.domain]


async def test_source_query_count_bounded_per_prospect() -> None:
    provider, transport = make_search_provider(
        [(200, search_response(results=[])) for _ in range(1)], max_source_queries_per_prospect=1
    )
    await provider.fetch_sources(COMPANY, ctx_key="run1:p1:research")
    assert transport.calls == 1  # bounded, never 3 (the full category count)


async def test_duplicate_url_across_queries_yields_one_winner() -> None:
    same_url = "https://acme-robotics.com/news/funding"
    steps = [
        (200, search_response(results=[search_result(id="r1", url=same_url, content="Acme raised funding.")])),
        (200, search_response(results=[search_result(id="r2", url=same_url, content="Acme raised funding.")])),
        (200, search_response(results=[search_result(id="r3", url=same_url, content="Acme raised funding.")])),
        (200, {"results": [{"url": same_url, "raw_content": "Acme raised funding, full text."}], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, ctx_key="run1:p1:research")
    from groundwork.domain.source_identity import select_winners

    winners = select_winners(bundle.documents)
    assert len(bundle.documents) == 3  # 3 occurrences persisted
    assert len(winners) == 1  # exactly one winner


async def test_only_winners_are_extracted() -> None:
    steps = [
        (200, search_response(results=[
            search_result(id="r1", url="https://acme-robotics.com/a", content="Distinct content A about funding."),
            search_result(id="r2", url="https://acme-robotics.com/a", content="Distinct content A about funding."),
        ])),
        (200, search_response(results=[])),
        (200, search_response(results=[])),
        (200, {"results": [{"url": "https://acme-robotics.com/a", "raw_content": "Real extracted body."}], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, ctx_key="run1:p1:research")
    extract_requests = [r for r in transport.requests if "urls" in json.loads(r.content)]
    assert len(extract_requests) == 1
    extracted_urls = json.loads(extract_requests[0].content)["urls"]
    assert extracted_urls == ["https://acme-robotics.com/a"]  # the one winner, not both occurrences


async def test_research_step_produces_live_fetch_evidence_with_real_urls(session_factory) -> None:
    """Full `engine/steps/research.py` step against a real `TavilySearchProvider`
    (scripted) — Evidence rows must be LIVE_FETCH-origin with provider URLs,
    never a model-authored or synthetic URL, and scoped to this prospect
    only."""
    from groundwork.engine.steps.research import research

    provider, transport = make_search_provider(
        [
            (200, search_response(results=[search_result(
                id="r1", url="https://acme-robotics.com/funding", title="Acme raises Series B",
                content="Acme Robotics announced a $20M Series B round led by Example Ventures.",
            )])),
            (200, search_response(results=[])),
            (200, search_response(results=[])),
            (200, {"results": [{"url": "https://acme-robotics.com/funding", "raw_content": "Acme Robotics announced a $20M Series B round led by Example Ventures."}], "failed_results": []}),
        ],
        max_source_queries_per_prospect=3,
    )

    class FakeLLM:
        name = "fake_llm"

        async def structured(self, envelope, schema, *, ctx_key, operation):
            facts = ResearchFacts(company=COMPANY, profile=CompanyProfileFacts())
            return LLMResult(
                parsed=ResearchExtractionOutput(facts=facts), operation=operation, model="fake",
                provider="fake_llm", prompt_version="v1", attempts=[],
            )

    repos = Repos.build(session_factory)
    from groundwork.repositories.plays import PlayRepository

    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="obj", icp_spec={}, mode="live")
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=1, provider_profile={})
    company_id = await repos.companies.get_or_create(COMPANY, "acme-robotics.com", "acme robotics", origin="live_fetch")
    prospect_id = await repos.prospects.create(run_id=run_id, company_id=company_id, dedupe_key="domain:acme-robotics.com", duplicate_of=None, status="RUNNING")

    from groundwork.models.schemas import PlaySpec

    ctx = ProspectContext(
        run_id=run_id, prospect_id=prospect_id, company=COMPANY, dedupe_key="domain:acme-robotics.com",
        play_spec=PlaySpec(objective_text="find robotics companies", target_industries=["robotics"]),
        providers=ProviderBundle(llm=FakeLLM(), search=provider), reference_date=date.today(),
        trace=None, events=EventEmitter(run_id=run_id, events=repos.events),
        llm_calls=LLMCallRecorder(run_id=run_id, prospect_id=prospect_id, provider="fake_llm", repo=repos.llm_calls),
        search_calls=SearchCallRecorder(run_id=run_id, prospect_id=prospect_id, repo=repos.search),
        enrichment_calls=EnrichmentCallRecorder(
            run_id=run_id, prospect_id=prospect_id, provider="none", repo=repos.contact_enrichment
        ),
    )
    result = await research(ctx)
    assert result.ok
    assert len(ctx.evidence) >= 1
    for evidence in ctx.evidence:
        assert evidence.origin == EvidenceOrigin.LIVE_FETCH
        assert evidence.source_url is not None
        assert evidence.source_url.startswith("https://acme-robotics.com")
        assert evidence.prospect_id == prospect_id
