"""`LiveSearchRuntime` — the PROCESS-scoped half of the live search provider
seam (H2 Phase 2), the search-side analogue of `providers/live/runtime.py::
LiveProviderRuntime`. Created exactly once, in FastAPI's `lifespan`
(`main.py`), and closed exactly once at shutdown. Multiple simultaneous runs
share the same `AsyncTavilyClient` and the same `asyncio.Semaphore` —
`SEARCH_MAX_CONCURRENCY` bounds *process-wide* concurrent Tavily calls, not
per-run concurrency. No hidden module globals, no per-prospect client
construction, no per-run semaphore.

Tests construct one with `http_client=httpx.AsyncClient(transport=...)`
(`httpx.MockTransport`) so nothing here ever needs a real network call — see
`tests/search_live_helpers.py`. `tavily-python==0.8.0`'s `AsyncTavilyClient`
has no SDK-side retry logic at all (confirmed by reading the installed
package: `search()`/`extract()` each issue exactly one HTTP POST) — every
retry/backoff/error-classification decision belongs to
`providers/live/tavily_search.py`, not this runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
from tavily import AsyncTavilyClient


@dataclass
class LiveSearchRuntime:
    client: AsyncTavilyClient
    semaphore: asyncio.Semaphore
    search_depth: str
    call_deadline_s: float
    max_transport_retries: int
    price_usd_per_credit: float | None

    @property
    def pricing_configured(self) -> bool:
        return self.price_usd_per_credit is not None

    def estimate_cost_usd(self, credits_used: float | None) -> float | None:
        """`None` unless BOTH a credit figure was actually reported by the
        provider call AND a trustworthy per-credit USD rate is configured —
        never a guessed or partial number (mirrors `LiveProviderRuntime.
        estimate_cost_usd`'s same "unset -> null" contract for OpenAI)."""
        if not self.pricing_configured or credits_used is None:
            return None
        return credits_used * self.price_usd_per_credit

    @classmethod
    def create(cls, settings, *, http_client: httpx.AsyncClient | None = None) -> "LiveSearchRuntime":
        if not settings.tavily_api_key:
            raise ValueError("LiveSearchRuntime.create() requires settings.tavily_api_key")
        client = AsyncTavilyClient(api_key=settings.tavily_api_key, client=http_client)
        return cls(
            client=client,
            semaphore=asyncio.Semaphore(settings.search_max_concurrency),
            search_depth=settings.tavily_search_depth,
            call_deadline_s=settings.search_call_deadline_s,
            max_transport_retries=settings.search_max_transport_retries,
            price_usd_per_credit=settings.tavily_price_usd_per_credit,
        )

    async def close(self) -> None:
        await self.client.close()
