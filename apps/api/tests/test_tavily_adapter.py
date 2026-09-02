"""H2 Phase 22 — SDK/adapter tests for `TavilySearchProvider`. Every HTTP
exchange is scripted via `httpx.MockTransport` (`tests/search_live_helpers.py`)
— no automated test may hit the real Tavily API.
"""

from __future__ import annotations

import importlib.metadata

import httpx
import pytest

from groundwork.models.schemas import CompanySeed, PlaySpec
from groundwork.providers.base import (
    SearchAttemptStatus,
    SearchAuthError,
    SearchInvalidResponse,
    SearchOperation,
    SearchProviderUnavailable,
    SearchRateLimited,
    SearchTimeout,
)
from tests.search_live_helpers import extract_response, make_search_provider, search_response, search_result


def test_tavily_python_pinned_version() -> None:
    assert importlib.metadata.version("tavily-python") == "0.8.0"


async def test_search_runtime_lifecycle_close() -> None:
    provider, transport = make_search_provider([(200, search_response())])
    await provider.runtime.close()  # must not raise


async def test_search_response_parsing_maps_provider_result_id_and_request_id() -> None:
    provider, transport = make_search_provider(
        [(200, search_response(request_id="req-abc", results=[search_result(id="res-1", url="https://acme.example.com/a")]))]
    )
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=1)
    assert len(raw.documents) == 1
    doc = raw.documents[0]
    assert doc.provider_result_id == "res-1"
    assert doc.url == "https://acme.example.com/a"
    telemetry = raw.telemetry[0]
    assert telemetry.provider_request_id == "req-abc"
    assert telemetry.status == SearchAttemptStatus.OK


async def test_include_usage_credits_mapped_to_telemetry() -> None:
    provider, transport = make_search_provider(
        [(200, search_response(usage={"credits": 3}))], settings_overrides={"tavily_price_usd_per_credit": 0.002}
    )
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=1)
    telemetry = raw.telemetry[0]
    assert telemetry.credits_used == 3
    assert telemetry.cost_usd == pytest.approx(0.006)


async def test_usage_absent_leaves_credits_and_cost_null() -> None:
    provider, transport = make_search_provider([(200, search_response())])
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=1)
    telemetry = raw.telemetry[0]
    assert telemetry.credits_used is None
    assert telemetry.cost_usd is None


async def test_extract_results_and_failed_results_mapped() -> None:
    provider, transport = make_search_provider(
        [
            (200, search_response(results=[
                search_result(id="a", url="https://acme.example.com/a", content="Acme funding news snippet."),
                search_result(id="b", url="https://acme.example.com/b", content="Acme careers page snippet."),
            ])),
            (200, extract_response(
                results=[{"url": "https://acme.example.com/a", "raw_content": "Full extracted text about Acme."}],
                failed_results=[{"url": "https://acme.example.com/b", "error": "timeout"}],
            )),
        ],
        max_source_queries_per_prospect=1,
    )
    company = CompanySeed(slug="acme", name="Acme", domain="acme.example.com", industry="unknown", size_band="unknown", employee_count=0)
    bundle = await provider.fetch_sources(company, ctx_key="run1:p1:research")
    by_url = {d.url: d for d in bundle.documents}
    assert by_url["https://acme.example.com/a"].status.value == "ok"
    assert by_url["https://acme.example.com/a"].extraction_method == "tavily_extract"
    assert by_url["https://acme.example.com/b"].status.value == "partial"
    extract_telemetry = [t for t in bundle.telemetry if t.operation == SearchOperation.EXTRACT]
    assert extract_telemetry and extract_telemetry[0].status == SearchAttemptStatus.PARTIAL_EXTRACTION


async def test_excerpt_bound_and_content_hash() -> None:
    long_text = "x" * 5000
    provider, transport = make_search_provider(
        [(200, search_response(results=[search_result(content=long_text)]))], max_source_excerpt_chars=100
    )
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=1)
    doc = raw.documents[0]
    assert len(doc.text) == 100
    assert doc.full_text_length == 5000
    assert doc.content_sha256 is not None and len(doc.content_sha256) == 64


async def test_no_raw_html_persisted_only_bounded_excerpt() -> None:
    provider, transport = make_search_provider(
        [(200, search_response(results=[search_result(content="short")]))], max_source_excerpt_chars=1200
    )
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=1)
    assert raw.documents[0].text == "short"


## `resolve_domain`/`raw_discover`/`fetch_sources` all catch
## `SearchProviderError` internally and degrade gracefully (one query's
## exhausted retries never crashes the whole discovery/retrieval call —
## Phase 21) — so the retry/classification mechanics live in `_call_tavily`
## and are tested directly here, a legitimate white-box test of the one
## place that logic lives; the black-box degradation behavior is verified
## separately below.


async def _call_domain_query(provider):
    from groundwork.domain.query_plan import build_domain_resolution_query
    from groundwork.providers.base import SearchOperation

    query = build_domain_resolution_query("Acme Robotics")
    return await provider._call_tavily(
        lambda: provider.runtime.client.search(query.query, search_depth="basic", max_results=5),
        operation=SearchOperation.RESOLVE_DOMAIN, query_group_id="run1:domain",
        template_id=query.template_id.value, rendered_query=query.query, query_digest=query.query_digest,
    )


async def test_timeout_retries_then_raises_search_timeout() -> None:
    provider, transport = make_search_provider(
        [httpx.ConnectTimeout("boom"), httpx.ConnectTimeout("boom again")],
        settings_overrides={"search_max_transport_retries": 1},
    )
    with pytest.raises(SearchTimeout) as excinfo:
        await _call_domain_query(provider)
    # 1 + max_transport_retries(1) = 2 attempts, never more.
    assert len(excinfo.value.telemetry) == 2
    assert all(t.status == SearchAttemptStatus.TIMEOUT for t in excinfo.value.telemetry)


async def test_rate_limited_retried_then_raises() -> None:
    provider, transport = make_search_provider(
        [(429, {"detail": {"error": "rate limited"}}), (429, {"detail": {"error": "rate limited"}})],
        settings_overrides={"search_max_transport_retries": 1},
    )
    with pytest.raises(SearchRateLimited) as excinfo:
        await _call_domain_query(provider)
    assert len(excinfo.value.telemetry) == 2
    assert all(t.status == SearchAttemptStatus.RATE_LIMITED for t in excinfo.value.telemetry)


async def test_auth_error_is_permanent_no_retry() -> None:
    provider, transport = make_search_provider(
        [(401, {"detail": {"error": "bad key"}})], settings_overrides={"search_max_transport_retries": 2}
    )
    with pytest.raises(SearchAuthError) as excinfo:
        await _call_domain_query(provider)
    assert len(excinfo.value.telemetry) == 1  # never retried
    assert excinfo.value.telemetry[0].status == SearchAttemptStatus.AUTH_ERROR
    assert transport.calls == 1


async def test_bad_request_is_permanent_invalid_response() -> None:
    provider, transport = make_search_provider(
        [(400, {"detail": {"error": "bad request"}})], settings_overrides={"search_max_transport_retries": 2}
    )
    with pytest.raises(SearchInvalidResponse):
        await _call_domain_query(provider)
    assert transport.calls == 1


async def test_server_5xx_retried_then_raises_provider_unavailable() -> None:
    provider, transport = make_search_provider(
        [(500, {"error": "boom"}), (503, {"error": "boom again"})],
        settings_overrides={"search_max_transport_retries": 1},
    )
    with pytest.raises(SearchProviderUnavailable):
        await _call_domain_query(provider)
    assert transport.calls == 2


async def test_resolve_domain_degrades_gracefully_on_exhausted_retries() -> None:
    """Black-box: the public method never raises — it returns an empty
    result and the caller (engine/discovery.py) treats that candidate as
    unresolved, not a crashed run."""
    provider, transport = make_search_provider(
        [httpx.ConnectTimeout("boom"), httpx.ConnectTimeout("boom again")],
        settings_overrides={"search_max_transport_retries": 1},
    )
    dc = await provider.resolve_domain("Acme Robotics", ctx_key="run1:domain")
    assert dc.domains == []
    assert dc.candidates == []
    assert len(dc.telemetry) == 2
    assert all(t.status == SearchAttemptStatus.TIMEOUT for t in dc.telemetry)


async def test_empty_results_is_not_an_exception() -> None:
    provider, transport = make_search_provider([(200, search_response(results=[]))])
    dc = await provider.resolve_domain("Nonexistent Co", ctx_key="run1:domain")
    assert dc.domains == []
    assert dc.candidates == []


async def test_search_budget_blocks_call_without_hitting_transport() -> None:
    class _AlwaysDenyBudget:
        async def reserve_search_call(self) -> bool:
            return False

        async def reserve_extract_call(self) -> bool:
            return False

    provider, transport = make_search_provider([], search_budget=_AlwaysDenyBudget())
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key="run1:discovery", max_queries=2)
    assert transport.calls == 0
    assert raw.hits == []
    assert all(t.status == SearchAttemptStatus.NOT_ATTEMPTED_BUDGET for t in raw.telemetry)


async def test_provider_purity_no_repository_or_sqlalchemy_imports() -> None:
    import ast
    import inspect

    from groundwork.providers.live import search_runtime, tavily_search

    for module in (tavily_search, search_runtime):
        tree = ast.parse(inspect.getsource(module))
        imported_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
        for name in imported_modules:
            assert not name.startswith("sqlalchemy"), f"{module.__name__} imports {name}"
            assert not name.startswith("groundwork.repositories"), f"{module.__name__} imports {name}"
            assert name != "groundwork.models.tables", f"{module.__name__} imports {name}"


async def test_no_arbitrary_http_fetch_path() -> None:
    import inspect

    from groundwork.providers.live import tavily_search

    source = inspect.getsource(tavily_search)
    assert "httpx.get(" not in source
    assert "requests.get(" not in source
    assert "requests.post(" not in source


