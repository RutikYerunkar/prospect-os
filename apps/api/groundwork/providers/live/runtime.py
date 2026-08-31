"""`LiveProviderRuntime` — the PROCESS-scoped half of the live provider seam
(Checkpoint G Phase 5). Created exactly once, in FastAPI's `lifespan`
(`main.py`), and closed exactly once at shutdown. Multiple simultaneous runs
share the same `AsyncOpenAI` client and the same `asyncio.Semaphore` — that
sharing is the point: `LLM_MAX_CONCURRENCY` bounds *process-wide* concurrent
OpenAI calls, not per-run concurrency.

Tests construct one with `http_client=httpx2.AsyncClient(transport=...)`
(`httpx2.MockTransport`) so nothing here ever needs a real network call —
see `tests/test_live_openai_provider.py`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx2
import openai


@dataclass
class LiveProviderRuntime:
    client: openai.AsyncOpenAI
    semaphore: asyncio.Semaphore
    model: str
    reasoning_effort: str | None
    max_output_tokens: int
    call_deadline_s: float
    max_transport_retries: int
    max_schema_retries: int
    price_input_usd_per_mtok: float | None
    price_output_usd_per_mtok: float | None

    @property
    def pricing_configured(self) -> bool:
        return self.price_input_usd_per_mtok is not None and self.price_output_usd_per_mtok is not None

    def estimate_cost_usd(self, tokens_in: int, tokens_out: int) -> float | None:
        if not self.pricing_configured:
            return None
        return (tokens_in / 1_000_000) * self.price_input_usd_per_mtok + (
            tokens_out / 1_000_000
        ) * self.price_output_usd_per_mtok

    @classmethod
    def create(cls, settings, *, http_client: httpx2.AsyncClient | None = None) -> "LiveProviderRuntime":
        if not settings.openai_api_key:
            raise ValueError("LiveProviderRuntime.create() requires settings.openai_api_key")
        client = openai.AsyncOpenAI(
            api_key=settings.openai_api_key,
            max_retries=0,  # SDK-hidden retries disabled — the flat retry loop owns all retries
            http_client=http_client,
        )
        return cls(
            client=client,
            semaphore=asyncio.Semaphore(settings.llm_max_concurrency),
            model=settings.openai_model,
            reasoning_effort=settings.openai_reasoning_effort or None,
            max_output_tokens=settings.llm_max_output_tokens,
            call_deadline_s=settings.llm_call_deadline_s,
            max_transport_retries=settings.llm_max_transport_retries,
            max_schema_retries=settings.llm_max_schema_retries,
            price_input_usd_per_mtok=settings.openai_price_input_usd_per_mtok,
            price_output_usd_per_mtok=settings.openai_price_output_usd_per_mtok,
        )

    async def close(self) -> None:
        await self.client.close()
