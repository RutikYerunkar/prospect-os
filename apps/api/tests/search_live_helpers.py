"""Shared scaffolding for exercising `TavilySearchProvider` against a
scripted `httpx.MockTransport` — no automated test may hit the real Tavily
API (H2 Phase 22), so every HTTP exchange here is canned.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from groundwork.providers.live.search_runtime import LiveSearchRuntime
from groundwork.providers.live.tavily_search import TavilySearchProvider


class _Settings:
    def __init__(self, **overrides: Any) -> None:
        self.tavily_api_key = "test-tvly-not-real"
        self.search_max_concurrency = 2
        self.tavily_search_depth = "basic"
        self.search_call_deadline_s = 5.0
        self.search_max_transport_retries = 1
        self.tavily_price_usd_per_credit = None
        for k, v in overrides.items():
            setattr(self, k, v)


def search_result(
    *, id: str = "r1", url: str = "https://acme.example.com/news", title: str = "Acme News",
    content: str = "Acme Robotics announced a funding round today.", raw_content: str | None = None,
    score: float = 0.9, published_date: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"id": id, "url": url, "title": title, "content": content, "score": score}
    if raw_content is not None:
        body["raw_content"] = raw_content
    if published_date is not None:
        body["published_date"] = published_date
    return body


def search_response(
    *, query: str = "q", results: list[dict[str, Any]] | None = None, request_id: str = "req-1",
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": query, "request_id": request_id, "response_time": 0.42,
        "results": results if results is not None else [search_result()],
        "answer": None, "follow_up_questions": None, "images": [],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def extract_response(
    *, results: list[dict[str, Any]] | None = None, failed_results: list[dict[str, Any]] | None = None,
    request_id: str = "req-extract",
) -> dict[str, Any]:
    return {
        "results": results or [],
        "failed_results": failed_results or [],
        "request_id": request_id,
        "response_time": 0.31,
    }


class ScriptedSearchTransport(httpx.AsyncBaseTransport):
    """Replays `steps` in order, one per outbound request. A step is either
    a `(status_code, json_body)` tuple or an `Exception` instance to raise
    instead (e.g. `httpx.ConnectTimeout`). `handler` (if given) overrides
    scripted replay entirely and computes a response per-request."""

    def __init__(
        self,
        steps: list[tuple[int, dict] | Exception] | None = None,
        *,
        handler: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.steps = list(steps or [])
        self.handler = handler
        self.calls = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        step = self.steps[self.calls]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx.Response(status, json=body, request=request)


def make_search_provider(
    steps: list[tuple[int, dict] | Exception] | None = None,
    *,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    settings_overrides: dict[str, Any] | None = None,
    search_budget: Any = None,
    **provider_overrides: Any,
) -> tuple[TavilySearchProvider, ScriptedSearchTransport]:
    transport = ScriptedSearchTransport(steps, handler=handler)
    http_client = httpx.AsyncClient(transport=transport)
    runtime = LiveSearchRuntime.create(_Settings(**(settings_overrides or {})), http_client=http_client)
    kwargs: dict[str, Any] = dict(
        runtime=runtime,
        search_budget=search_budget,
        max_results_per_query=5,
        max_source_queries_per_prospect=3,
        max_result_occurrences_per_prospect=15,
        max_sources_per_prospect=5,
        max_source_excerpt_chars=1200,
    )
    kwargs.update(provider_overrides)
    return TavilySearchProvider(**kwargs), transport
