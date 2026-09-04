"""`ApolloRuntime` — the PROCESS-scoped half of the live Apollo enrichment
provider seam (V2-D), the enrichment-side analogue of `providers/live/
search_runtime.py::LiveSearchRuntime`. Created exactly once — in `main.py`'s
lifespan, guarded by `settings.enrichment_provider == "apollo" and
settings.apollo_api_key` — and closed exactly once at shutdown. Multiple
simultaneous runs share the same `httpx.AsyncClient` and the same
`asyncio.Semaphore` (`APOLLO_MAX_CONCURRENCY` bounds process-wide concurrent
Apollo calls, not per-run concurrency).

There is no Apollo Python SDK in this codebase by design (§Part 4/V2-D):
every HTTP concern (retry, error classification, the exact query-parameter
contract) belongs to `providers/live/apollo_enrichment.py`, not this
runtime — this module only owns the shared `httpx.AsyncClient`/semaphore/
bounds lifecycle, mirroring `LiveSearchRuntime` exactly.

Tests construct one with `http_client=httpx.AsyncClient(transport=...)`
(`httpx.MockTransport`) so nothing here ever needs a real network call — see
`tests/live_enrichment_helpers.py`. Zero automated test may hit the real
Apollo API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

# Pinned per the frozen V2-D contract (§Part 4) — never an env-configurable
# APOLLO_BASE_URL. Kept as adapter-visible constants so both this runtime and
# `apollo_enrichment.py` reference the same single source of truth.
APOLLO_API_ORIGIN = "https://api.apollo.io"
APOLLO_PEOPLE_MATCH_PATH = "/api/v1/people/match"


@dataclass
class ApolloRuntime:
    client: httpx.AsyncClient
    semaphore: asyncio.Semaphore
    call_deadline_s: float
    max_transport_retries: int
    price_usd_per_credit: float | None

    @property
    def pricing_configured(self) -> bool:
        return self.price_usd_per_credit is not None

    def estimate_cost_usd(self, credits_used: float | None) -> float | None:
        """`None` unless BOTH a credit figure was actually reported by a
        verified Apollo response field AND a trustworthy per-credit USD rate
        is configured — mirrors `LiveSearchRuntime.estimate_cost_usd`'s
        "unset -> null" contract. As of V2-D, `credits_used` is never
        populated at all (no verified numeric usage field has been observed
        on a real Apollo response yet — see docs/PROGRESS.md), so this
        always returns `None` until a future session wires a confirmed
        field; `APOLLO_PRICE_USD_PER_CREDIT` alone must never fabricate a
        cost from an unobserved credit count."""
        if not self.pricing_configured or credits_used is None:
            return None
        return credits_used * self.price_usd_per_credit

    @classmethod
    def create(cls, settings, *, http_client: httpx.AsyncClient | None = None) -> "ApolloRuntime":
        if not settings.apollo_api_key:
            raise ValueError("ApolloRuntime.create() requires settings.apollo_api_key")
        client = http_client if http_client is not None else httpx.AsyncClient()
        # Applied even to an injected test client (mirrors `AsyncTavilyClient
        # (api_key=..., client=http_client)`'s wrapping precedent) — tests
        # only need to hand in a bare `httpx.AsyncClient(transport=...)`, not
        # duplicate the base_url/header wiring themselves.
        client.base_url = APOLLO_API_ORIGIN
        client.headers["x-api-key"] = settings.apollo_api_key
        return cls(
            client=client,
            semaphore=asyncio.Semaphore(settings.apollo_max_concurrency),
            call_deadline_s=settings.apollo_call_deadline_s,
            max_transport_retries=settings.apollo_max_transport_retries,
            price_usd_per_credit=settings.apollo_price_usd_per_credit,
        )

    async def close(self) -> None:
        await self.client.aclose()
