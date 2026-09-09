"""Shared scaffolding for exercising `HunterEnrichmentProvider` against a
scripted `httpx.MockTransport` — no automated test may hit the real Hunter
API (V2-DH), so every HTTP exchange here is canned. Mirrors
`tests/live_enrichment_helpers.py`.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from groundwork.providers.live.hunter_enrichment import HunterEnrichmentProvider
from groundwork.providers.live.hunter_runtime import HunterRuntime


class _Settings:
    def __init__(self, **overrides: Any) -> None:
        self.hunter_api_key = "test-hunter-not-real"
        self.hunter_max_concurrency = 2
        self.hunter_call_deadline_s = 5.0
        self.hunter_max_transport_retries = 1
        for k, v in overrides.items():
            setattr(self, k, v)


def hunter_data(
    *,
    email: str | None = "priya.natarajan@acme.example.com",
    verification_status: str | None = "valid",
    include_verification: bool = True,
    score: float | None = 92,
    accept_all: bool | None = False,
    first_name: str | None = "Priya",
    last_name: str | None = "Natarajan",
    company: str | None = "Acme Robotics",
    position: str | None = "VP of Sales",
    linkedin_url: str | None = "https://www.linkedin.com/in/priya-natarajan",
    domain: str | None = "acme.example.com",
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if email is not None:
        body["email"] = email
    if include_verification:
        body["verification"] = {"status": verification_status, "date": "2026-01-01"}
    if score is not None:
        body["score"] = score
    if accept_all is not None:
        body["accept_all"] = accept_all
    if first_name is not None:
        body["first_name"] = first_name
    if last_name is not None:
        body["last_name"] = last_name
    if company is not None:
        body["company"] = company
    if position is not None:
        body["position"] = position
    if linkedin_url is not None:
        body["linkedin_url"] = linkedin_url
    if domain is not None:
        body["domain"] = domain
    return body


def email_finder_response(*, data: dict[str, Any] | None = "__default__") -> dict[str, Any]:
    if data == "__default__":
        data = hunter_data()
    return {"data": data, "meta": {"params": {}}}


class ScriptedHunterTransport(httpx.AsyncBaseTransport):
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


def make_hunter_provider(
    steps: list[tuple[int, dict] | Exception] | None = None,
    *,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    settings_overrides: dict[str, Any] | None = None,
    budget: Any = None,
) -> tuple[HunterEnrichmentProvider, ScriptedHunterTransport]:
    transport = ScriptedHunterTransport(steps, handler=handler)
    http_client = httpx.AsyncClient(transport=transport)
    runtime = HunterRuntime.create(_Settings(**(settings_overrides or {})), http_client=http_client)
    return HunterEnrichmentProvider(runtime=runtime, budget=budget), transport
