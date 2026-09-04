"""`LiveEnrichmentRuntime` — the shared PROCESS-scoped lifecycle for a live
enrichment provider runtime (V2-D Apollo, V2-DH Hunter), the enrichment-side
analogue of `providers/live/search_runtime.py::LiveSearchRuntime`. Each
concrete runtime (`ApolloRuntime` here, `HunterRuntime` in `hunter_runtime.
py`) is created exactly once — in `main.py`'s lifespan, guarded by
`settings.enrichment_provider` matching that provider's name AND its own API
key being configured — and closed exactly once at shutdown. Multiple
simultaneous runs share the same `httpx.AsyncClient` and the same
`asyncio.Semaphore` (bounds process-wide concurrent calls to that one
provider, not per-run concurrency).

There is no Apollo/Hunter Python SDK in this codebase by design (§Part 4/
V2-D, extended by V2-DH): every HTTP concern (retry, error classification,
the exact query-parameter contract) belongs to each provider's own
`*_enrichment.py` adapter, never this module — this file only owns the
shared `httpx.AsyncClient`/semaphore/bounds/pricing lifecycle genuinely
common to both, mirroring `LiveSearchRuntime`. Provider-specific wiring (auth
header name, base_url/pinned endpoint constants, which settings fields feed
which bound) stays in each subclass's own `create()`.

Tests construct one with `http_client=httpx.AsyncClient(transport=...)`
(`httpx.MockTransport`) so nothing here ever needs a real network call — see
`tests/live_enrichment_helpers.py`/`tests/live_hunter_helpers.py`. Zero
automated test may hit a real Apollo or Hunter API.
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
class LiveEnrichmentRuntime:
    """Fields genuinely common to every live enrichment provider runtime.
    `provider_name` is this runtime's own provider slug (`"apollo"`,
    `"hunter"`) — informational, never read to branch behavior anywhere
    outside a subclass's own `create()`."""

    client: httpx.AsyncClient
    semaphore: asyncio.Semaphore
    call_deadline_s: float
    max_transport_retries: int
    price_usd_per_credit: float | None
    provider_name: str

    @property
    def pricing_configured(self) -> bool:
        return self.price_usd_per_credit is not None

    def estimate_cost_usd(self, credits_used: float | None) -> float | None:
        """`None` unless BOTH a credit figure was actually reported by a
        verified provider response field AND a trustworthy per-credit USD
        rate is configured — mirrors `LiveSearchRuntime.estimate_cost_usd`'s
        "unset -> null" contract. `credits_used` must never be inferred from
        a merely plausible-sounding field name — only from an observed,
        confirmed numeric usage field."""
        if not self.pricing_configured or credits_used is None:
            return None
        return credits_used * self.price_usd_per_credit

    async def close(self) -> None:
        await self.client.aclose()


@dataclass
class ApolloRuntime(LiveEnrichmentRuntime):
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
            provider_name="apollo",
        )
