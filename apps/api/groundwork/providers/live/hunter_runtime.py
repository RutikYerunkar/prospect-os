"""`HunterRuntime` — the second live enrichment provider runtime (V2-DH),
alongside `providers/live/enrichment_runtime.py::ApolloRuntime`, behind the
same `LiveEnrichmentRuntime` shared lifecycle base defined there. Created
exactly once — in `main.py`'s lifespan, guarded by
`settings.enrichment_provider == "hunter" and settings.hunter_api_key` — and
closed exactly once at shutdown.

No Hunter Python SDK in this codebase by design: every HTTP concern lives in
`providers/live/hunter_enrichment.py`, never here — this module only owns the
pinned endpoint constants and the shared runtime lifecycle (client/semaphore/
bounds), mirroring `ApolloRuntime` exactly. `HUNTER_API_ORIGIN`/
`HUNTER_EMAIL_FINDER_PATH` are pinned module constants — never an
env-configurable `HUNTER_BASE_URL`.

Tests construct one with `http_client=httpx.AsyncClient(transport=...)`
(`httpx.MockTransport`) — see `tests/live_hunter_helpers.py`. Zero automated
test may hit the real Hunter API.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from groundwork.providers.live.enrichment_runtime import LiveEnrichmentRuntime

# Pinned per the frozen V2-DH contract (§Part 3) — no HUNTER_BASE_URL env
# override.
HUNTER_API_ORIGIN = "https://api.hunter.io"
HUNTER_EMAIL_FINDER_PATH = "/v2/email-finder"


@dataclass
class HunterRuntime(LiveEnrichmentRuntime):
    @classmethod
    def create(cls, settings, *, http_client: httpx.AsyncClient | None = None) -> "HunterRuntime":
        if not settings.hunter_api_key:
            raise ValueError("HunterRuntime.create() requires settings.hunter_api_key")
        client = http_client if http_client is not None else httpx.AsyncClient()
        client.base_url = HUNTER_API_ORIGIN
        # Auth MUST use the X-API-KEY header (§Part 3) — the key must never
        # appear in a query parameter or the request URL.
        client.headers["X-API-KEY"] = settings.hunter_api_key
        return cls(
            client=client,
            semaphore=asyncio.Semaphore(settings.hunter_max_concurrency),
            call_deadline_s=settings.hunter_call_deadline_s,
            max_transport_retries=settings.hunter_max_transport_retries,
            # No HUNTER_PRICE_USD_PER_CREDIT setting exists (frozen §Part
            # 12) — pricing stays permanently unconfigured for Hunter.
            price_usd_per_credit=None,
            provider_name="hunter",
        )
