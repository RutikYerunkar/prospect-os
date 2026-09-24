"""v2.0.3 source-boundary audit — `domain/psl.py::is_company_domain()` (the
pure predicate) and its enforcement in `TavilySearchProvider.fetch_sources()`
(the only scoped call site; `resolve_domain()`/`raw_discover()` — domain
resolution and discovery — stay deliberately unscoped).
"""

from __future__ import annotations

import json

from groundwork.domain.psl import is_company_domain
from groundwork.models.schemas import CompanySeed, PlaySpec
from tests.search_live_helpers import make_search_provider, search_response, search_result

COMPANY = CompanySeed(
    slug="acme-robotics", name="Acme Robotics", domain="acme-robotics.com",
    industry="unknown", size_band="unknown", employee_count=0, hq_country="unknown",
)
SPEC = PlaySpec(objective_text="find robotics companies", target_industries=["robotics"])


# --- pure predicate -------------------------------------------------------


def test_same_domain_matches():
    assert is_company_domain("https://acme-robotics.com/news", "acme-robotics.com") is True


def test_legitimate_subdomain_matches():
    assert is_company_domain("https://careers.acme-robotics.com/jobs", "acme-robotics.com") is True
    assert is_company_domain("https://www.acme-robotics.com/", "acme-robotics.com") is True


def test_different_registrable_domain_rejected():
    assert is_company_domain("https://evil.com/acme-robotics.com", "acme-robotics.com") is False
    assert is_company_domain("https://acme-robotics.com.evil.com/phish", "acme-robotics.com") is False


def test_unrelated_press_domain_rejected():
    # A legitimate third-party news article about the company is still
    # off-domain — this boundary is about the company's OWN site only.
    assert is_company_domain("https://techcrunch.com/acme-robotics-raises-funding", "acme-robotics.com") is False


def test_unparseable_or_empty_url_fails_closed():
    assert is_company_domain(None, "acme-robotics.com") is False
    assert is_company_domain("", "acme-robotics.com") is False
    assert is_company_domain("not a url at all", "acme-robotics.com") is False


def test_unparseable_company_domain_fails_closed():
    assert is_company_domain("https://acme-robotics.com/news", "") is False
    assert is_company_domain("https://acme-robotics.com/news", "co.uk") is False


def test_case_insensitive_match():
    assert is_company_domain("https://WWW.Acme-Robotics.COM/news", "Acme-Robotics.com") is True


def test_co_uk_style_registrable_domain_subdomain_matches():
    assert is_company_domain("https://careers.acme.co.uk/jobs", "acme.co.uk") is True
    assert is_company_domain("https://acme.co.uk.evil.com/jobs", "acme.co.uk") is False


# --- enforcement in fetch_sources() ---------------------------------------


async def test_off_domain_result_dropped_before_becoming_an_occurrence():
    steps = [
        (200, search_response(results=[
            search_result(id="r1", url="https://acme-robotics.com/news", content="on-domain hit"),
            search_result(id="r2", url="https://some-other-site.example/about-acme", content="off-domain hit"),
        ])),
        (200, search_response(results=[])),
        (200, search_response(results=[])),
        (200, {"results": [{"url": "https://acme-robotics.com/news", "raw_content": "full text"}], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, SPEC, ctx_key="run1:p1:research")

    urls = [d.url for d in bundle.documents]
    assert "https://acme-robotics.com/news" in urls
    assert "https://some-other-site.example/about-acme" not in urls
    assert len(bundle.documents) == 1  # the off-domain hit never became an occurrence

    extract_requests = [r for r in transport.requests if "urls" in json.loads(r.content)]
    assert extract_requests, "expected the on-domain winner to still be extracted"
    extracted_urls = json.loads(extract_requests[0].content)["urls"]
    assert extracted_urls == ["https://acme-robotics.com/news"]


async def test_off_domain_unparseable_url_also_dropped():
    steps = [
        (200, search_response(results=[
            search_result(id="r1", url="https://acme-robotics.com/news", content="on-domain hit"),
            {"id": "r2", "title": "no url", "content": "malformed result", "url": None, "score": 0.5},
        ])),
        (200, search_response(results=[])),
        (200, search_response(results=[])),
        (200, {"results": [{"url": "https://acme-robotics.com/news", "raw_content": "full text"}], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, SPEC, ctx_key="run1:p1:research")
    assert len(bundle.documents) == 1
    assert bundle.documents[0].url == "https://acme-robotics.com/news"


async def test_selected_count_narrows_below_result_count_on_partial_drop():
    """Existing search telemetry proof — no new column: `result_count` stays
    the provider-returned count, `selected_count` narrows to what survived
    the domain-boundary filter."""
    steps = [
        (200, search_response(results=[
            search_result(id="r1", url="https://acme-robotics.com/a", content="on-domain 1"),
            search_result(id="r2", url="https://acme-robotics.com/b", content="on-domain 2"),
            search_result(id="r3", url="https://off-domain.example/c", content="off-domain"),
        ])),
        (200, search_response(results=[])),
        (200, search_response(results=[])),
        (200, {"results": [], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, SPEC, ctx_key="run1:p1:research")

    domain_search_attempts = [t for t in bundle.telemetry if t.operation.value == "domain_search"]
    first_attempt = next(t for t in domain_search_attempts if t.result_count > 0)
    assert first_attempt.result_count == 3  # provider-returned count, unchanged
    assert first_attempt.selected_count == 2  # narrowed after the domain-boundary filter
    assert first_attempt.selected_count < first_attempt.result_count


async def test_raw_discover_stays_unscoped_by_company_domain():
    """Discovery has no company yet to scope against — `raw_discover()`
    must pass through results from any domain untouched, never filtered by
    `is_company_domain()`. This is the deliberate "discovery and domain
    resolution must remain unscoped" boundary, locked in as a regression
    test."""
    steps = [(200, search_response(results=[
        search_result(id="r1", url="https://some-random-blog.example/post-about-acme"),
        search_result(id="r2", url="https://another-unrelated-domain.example/x"),
    ]))]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=1)
    result = await provider.raw_discover(SPEC, ctx_key="run1:discover", max_queries=1)
    urls = {h.url for h in result.hits}
    assert urls == {"https://some-random-blog.example/post-about-acme", "https://another-unrelated-domain.example/x"}
    assert len(result.documents) == 2


async def test_resolve_domain_stays_unscoped_by_company_domain():
    """Domain resolution's whole job is finding a company's real domain
    from an unscoped web search — it must never filter candidates by any
    domain (there is no known company domain yet to scope against)."""
    steps = [(200, search_response(results=[
        search_result(id="r1", url="https://acme-robotics.io/"),
        search_result(id="r2", url="https://totally-unrelated.example/"),
    ]))]
    provider, transport = make_search_provider(steps)
    candidates = await provider.resolve_domain("Acme Robotics", ctx_key="run1:resolve")
    assert set(candidates.domains) == {"acme-robotics.io", "totally-unrelated.example"}


async def test_all_on_domain_results_selected_count_equals_result_count():
    steps = [
        (200, search_response(results=[
            search_result(id="r1", url="https://acme-robotics.com/a", content="on-domain 1"),
        ])),
        (200, search_response(results=[])),
        (200, search_response(results=[])),
        (200, {"results": [], "failed_results": []}),
    ]
    provider, transport = make_search_provider(steps, max_source_queries_per_prospect=3)
    bundle = await provider.fetch_sources(COMPANY, SPEC, ctx_key="run1:p1:research")
    domain_search_attempts = [t for t in bundle.telemetry if t.operation.value == "domain_search"]
    first_attempt = next(t for t in domain_search_attempts if t.result_count > 0)
    assert first_attempt.result_count == first_attempt.selected_count == 1
