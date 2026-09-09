"""Shared scaffolding for exercising `ApolloEnrichmentProvider` against a
scripted `httpx.MockTransport` — no automated test may hit the real Apollo
API (V2-D), so every HTTP exchange here is canned. Mirrors
`tests/search_live_helpers.py`.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from groundwork.providers.live.apollo_enrichment import ApolloEnrichmentProvider
from groundwork.providers.live.enrichment_runtime import ApolloRuntime


class _Settings:
    def __init__(self, **overrides: Any) -> None:
        self.apollo_api_key = "test-apollo-not-real"
        self.apollo_max_concurrency = 2
        self.apollo_call_deadline_s = 5.0
        self.apollo_max_transport_retries = 1
        self.apollo_price_usd_per_credit = None
        for k, v in overrides.items():
            setattr(self, k, v)


def apollo_person(
    *,
    id: str = "person-abc123",
    name: str | None = "Priya Natarajan",
    first_name: str | None = "Priya",
    last_name: str | None = "Natarajan",
    title: str | None = "VP of Sales",
    email: str | None = "priya.natarajan@acme.example.com",
    email_status: str | None = "verified",
    linkedin_url: str | None = "https://www.linkedin.com/in/priya-natarajan",
    organization: dict[str, Any] | None = "__default__",
) -> dict[str, Any]:
    body: dict[str, Any] = {"id": id}
    if name is not None:
        body["name"] = name
    if first_name is not None:
        body["first_name"] = first_name
    if last_name is not None:
        body["last_name"] = last_name
    if title is not None:
        body["title"] = title
    if email is not None:
        body["email"] = email
    if email_status is not None:
        body["email_status"] = email_status
    if linkedin_url is not None:
        body["linkedin_url"] = linkedin_url
    if organization == "__default__":
        organization = {"id": "org-1", "name": "Acme Robotics", "primary_domain": "acme.example.com"}
    if organization is not None:
        body["organization"] = organization
    return body


def match_response(*, person: dict[str, Any] | None = "__default__") -> dict[str, Any]:
    if person == "__default__":
        person = apollo_person()
    return {"person": person}


class ScriptedApolloTransport(httpx.AsyncBaseTransport):
    """Replays `steps` in order, one per outbound request. A step is either
    a `(status_code, json_body)` tuple or an `Exception` instance to raise
    instead (e.g. `httpx.ConnectTimeout`)."""

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


def make_enrichment_provider(
    steps: list[tuple[int, dict] | Exception] | None = None,
    *,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    settings_overrides: dict[str, Any] | None = None,
    budget: Any = None,
) -> tuple[ApolloEnrichmentProvider, ScriptedApolloTransport]:
    transport = ScriptedApolloTransport(steps, handler=handler)
    http_client = httpx.AsyncClient(transport=transport)
    runtime = ApolloRuntime.create(_Settings(**(settings_overrides or {})), http_client=http_client)
    return ApolloEnrichmentProvider(runtime=runtime, budget=budget), transport
