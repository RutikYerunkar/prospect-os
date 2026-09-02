"""H2 post-smoke — realistic, search-result-shaped fixtures for
`DISCOVERY_EXTRACTION`'s server-side validation (`domain/discovery.py`).

The real Tavily API cannot be exercised offline, so these tests can't
verify what a real model would *propose* from this text — only that the
server-side support/served-ref checks correctly ACCEPT a legitimate,
well-behaved proposal and REJECT a fabricated one for the same realistic
content shapes actually observed in the wild: a funding roundup naming
several companies in one excerpt, a Crunchbase/news-style funding article,
a job listing mentioning its employer, an AI-infrastructure market
analysis piece, and a generic article that names no company at all.
"""

from __future__ import annotations

import re

from groundwork.domain.discovery import company_name_textually_supported, label_supported_by_sources
from groundwork.engine.discovery import discover_live
from groundwork.models.llm_io import DiscoveryCandidate, DiscoveryExtractionOutput, DomainSelectionOutput
from groundwork.providers.base import LLMOperation, LLMResult, ProviderBundle
from tests.search_live_helpers import make_search_provider, search_response, search_result
from tests.test_live_discovery import PLAY, FakeLLM, FakeRepos, FakeEvents

FUNDING_ROUNDUP_EXCERPT = (
    "This week's funding roundup: Acme Robotics raised a $20M Series B led by "
    "Example Ventures. Northwind Analytics closed a $5M seed round. Ferrous Grid "
    "announced a $12M Series A to expand its industrial monitoring platform. "
    "Meanwhile, layoffs continued across the sector as several late-stage startups "
    "cut headcount."
)

CRUNCHBASE_STYLE_ARTICLE = (
    "Acme Robotics, Inc. today announced it has raised $20 million in Series B "
    "funding led by Example Ventures, with participation from Founders Fund. "
    "The company, which builds warehouse automation robots, will use the funds "
    "to expand its engineering team."
)

JOB_LISTING_EXCERPT = (
    "Senior Backend Engineer — Acme Robotics. Acme Robotics is hiring a senior "
    "backend engineer to join our infrastructure team in San Francisco. You will "
    "work on our real-time robotics control platform. Acme Robotics offers "
    "competitive compensation and equity."
)

AI_INFRA_ANALYSIS_EXCERPT = (
    "The AI infrastructure market has matured rapidly over the past two years, "
    "driven by rising demand for GPU orchestration, vector databases, and "
    "inference optimization tooling. Enterprises are increasingly consolidating "
    "vendors as the category shifts from experimentation to production workloads."
)

NO_COMPANY_EXCERPT = (
    "Five trends shaping enterprise software in the coming year: composable "
    "architecture, AI-assisted development, zero-trust security, edge "
    "computing, and sustainability-driven infrastructure choices."
)


def test_funding_roundup_supports_each_named_company_from_one_excerpt() -> None:
    for name in ("Acme Robotics", "Northwind Analytics", "Ferrous Grid"):
        assert company_name_textually_supported(name, [FUNDING_ROUNDUP_EXCERPT])
    # A company genuinely not in the roundup must not be supported by it.
    assert not company_name_textually_supported("Totally Unrelated Widgets", [FUNDING_ROUNDUP_EXCERPT])


def test_crunchbase_style_article_supports_legal_suffix_name() -> None:
    assert company_name_textually_supported("Acme Robotics, Inc.", [CRUNCHBASE_STYLE_ARTICLE])
    assert company_name_textually_supported("Acme Robotics", [CRUNCHBASE_STYLE_ARTICLE])


def test_job_listing_supports_employer_name() -> None:
    assert company_name_textually_supported("Acme Robotics", [JOB_LISTING_EXCERPT])


def test_analysis_article_does_not_support_a_fabricated_company() -> None:
    # A market-landscape piece names no specific company — a candidate
    # claiming one exists here must be rejected, not accepted on vibes.
    assert not company_name_textually_supported("Acme Robotics", [AI_INFRA_ANALYSIS_EXCERPT])
    assert not company_name_textually_supported("Nimbus AI Infra", [AI_INFRA_ANALYSIS_EXCERPT])


def test_no_company_article_supports_nothing() -> None:
    assert not company_name_textually_supported("Acme Robotics", [NO_COMPANY_EXCERPT])
    assert not company_name_textually_supported("ComposableCo", [NO_COMPANY_EXCERPT])


def test_served_ref_gate_independent_of_text_support() -> None:
    # label_supported_by_sources only checks ref membership — a candidate
    # citing a real, served ref with genuinely supportive text still needs
    # BOTH checks to pass; this proves the ref check alone isn't sufficient
    # (company_name_textually_supported is the other, required half).
    served = frozenset({"r1", "r2"})
    assert label_supported_by_sources("Acme Robotics", served, "r1")
    assert not label_supported_by_sources("Acme Robotics", served, "r99")
    assert not label_supported_by_sources("Acme Robotics", served, None)


async def test_multi_company_listicle_extracts_all_via_full_discovery_pipeline() -> None:
    """End-to-end through `discover_live()`: one Stage-A hit is a funding
    roundup naming three companies. A well-behaved model proposing all
    three (citing the same ref) must see all three survive server-side
    validation and reach domain resolution — not just the first one."""
    steps = [(200, search_response(results=[search_result(
        id="hit1", url="https://news.example.com/roundup", title="This week's funding roundup",
        content=FUNDING_ROUNDUP_EXCERPT,
    )])) for _ in range(4)]
    # One domain-resolution query per surviving candidate (3), each with no
    # served candidates — keeps this test focused on Stage B extraction
    # breadth, not Stage C outcome.
    steps.extend((200, search_response(results=[])) for _ in range(3))
    search, transport = make_search_provider(steps)

    async def structured(envelope, schema, *, ctx_key, operation):
        if operation == LLMOperation.DISCOVERY_EXTRACTION:
            refs = re.findall(r'ref="([^"]+)"', envelope.user)
            ref = refs[0] if refs else "hit1"
            parsed = DiscoveryExtractionOutput(candidates=[
                DiscoveryCandidate(company_name="Acme Robotics", supporting_result_refs=[ref]),
                DiscoveryCandidate(company_name="Northwind Analytics", supporting_result_refs=[ref]),
                DiscoveryCandidate(company_name="Ferrous Grid", supporting_result_refs=[ref]),
            ])
        else:
            parsed = DomainSelectionOutput(selected_candidate_ref=None)
        return LLMResult(parsed=parsed, operation=operation, model="fake", provider="fake_llm", prompt_version="v1", attempts=[])

    llm = FakeLLM()
    llm.structured = structured
    providers = ProviderBundle(llm=llm, search=search)
    events = FakeEvents()
    repos = FakeRepos()
    result = await discover_live(
        run_id="run1", play_spec=PLAY, providers=providers, repos=repos, events=events,
        limit=5, max_plan_queries=4, max_domain_resolution_queries=8,
    )
    # All three survive Stage B (none rejected) even though domain
    # resolution then finds nothing for any of them.
    extraction_events = [p for t, p in events.log if t == "discovery.extraction_completed"]
    assert extraction_events and extraction_events[0]["candidates_valid"] == 3
    assert result.companies == []  # no domain candidates were served in this test


async def test_generic_article_yields_zero_candidates_not_a_crash() -> None:
    steps = [(200, search_response(results=[search_result(
        id="hit1", url="https://news.example.com/trends", title="Five trends shaping enterprise software",
        content=NO_COMPANY_EXCERPT,
    )])) for _ in range(4)]
    search, transport = make_search_provider(steps)
    # A well-behaved model correctly proposes nothing for this excerpt.
    llm = FakeLLM(extraction=DiscoveryExtractionOutput(candidates=[]))
    providers = ProviderBundle(llm=llm, search=search)
    events = FakeEvents()
    repos = FakeRepos()
    result = await discover_live(
        run_id="run1", play_spec=PLAY, providers=providers, repos=repos, events=events,
        limit=5, max_plan_queries=4, max_domain_resolution_queries=8,
    )
    assert result.companies == []
    extraction_events = [p for t, p in events.log if t == "discovery.extraction_completed"]
    assert extraction_events and extraction_events[0]["candidates_proposed"] == 0
