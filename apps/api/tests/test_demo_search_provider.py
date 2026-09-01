"""H1 Phase 13 — DemoSearchProvider ported to the new SearchProvider
contract (discover/resolve_domain/fetch_sources all return telemetry
alongside their payload). Zero credentials required; no real URLs are ever
invented for fixture sources.
"""

from __future__ import annotations

from groundwork.providers.base import DiscoveryResult, DomainCandidates, SourceBundle
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import load_fixture_pack


async def test_discover_returns_discovery_result_with_telemetry() -> None:
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    result = await provider.discover(pack.play_spec, limit=3)
    assert isinstance(result, DiscoveryResult)
    assert len(result.companies) == 3
    assert result.telemetry and result.telemetry[0].status == "OK"


async def test_fetch_sources_returns_source_bundle_with_telemetry() -> None:
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    company = pack.company_by_slug("sable-compute").to_company_seed()
    bundle = await provider.fetch_sources(company, ctx_key="run:prospect:research")
    assert isinstance(bundle, SourceBundle)
    assert len(bundle.documents) == len(pack.company_by_slug("sable-compute").sources)
    assert bundle.telemetry and bundle.telemetry[0].result_count == len(bundle.documents)


async def test_fetch_sources_no_real_urls_ever_invented() -> None:
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    for fixture in pack.companies:
        if not fixture.sources or fixture.failure_script:
            continue  # scripted-failure fixtures are exercised elsewhere
        company = fixture.to_company_seed()
        bundle = await provider.fetch_sources(company, ctx_key="run:prospect:research")
        for doc in bundle.documents:
            assert doc.url is None
            assert doc.canonical_url is None


async def test_resolve_domain_offline_lookup() -> None:
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    result = await provider.resolve_domain("Northwind Labs", ctx_key="run:prospect:resolve")
    assert isinstance(result, DomainCandidates)
    assert result.domains == ["northwindlabs.com"]


async def test_resolve_domain_no_match_returns_empty() -> None:
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    result = await provider.resolve_domain("Totally Unknown Company", ctx_key="run:prospect:resolve")
    assert result.domains == []


async def test_zero_credentials_required() -> None:
    """DemoSearchProvider never reads any API key/settings — constructible
    and fully functional with nothing but the fixture pack."""
    pack = load_fixture_pack()
    provider = DemoSearchProvider(pack, seed=1)
    assert provider.name == "demo_fixture"
    result = await provider.discover(pack.play_spec, limit=1)
    assert len(result.companies) == 1
