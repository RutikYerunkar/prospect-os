"""Phase 5/7: `RunBudget` race-safety under concurrent charges, the SOFT
(never hard) threshold semantics, `NOT_ATTEMPTED_BUDGET` telemetry for a
blocked call, and the process-scoped `LiveProviderRuntime`'s semaphore
being genuinely shared across two concurrent logical calls (a stand-in for
"two simultaneous runs share the concurrency semaphore" — both runs would
construct their own `OpenAILLMProvider` referencing the SAME runtime).
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from groundwork.engine.run_budget import RunBudget
from groundwork.providers.base import LLMOperation, PromptEnvelope, ProviderBudgetExceeded
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from tests.live_helpers import make_runtime, message_output, response_body


class Out(BaseModel):
    explanation: str


async def test_run_budget_disabled_when_no_threshold_configured():
    budget = RunBudget(None)
    assert budget.enforceable is False
    assert await budget.is_tripped() is False
    await budget.charge(999.0)
    assert await budget.is_tripped() is False  # never enforced without a configured threshold


async def test_run_budget_trips_at_soft_threshold():
    budget = RunBudget(1.0)
    assert budget.enforceable is True
    await budget.charge(0.6)
    assert await budget.is_tripped() is False
    await budget.charge(0.5)
    assert await budget.is_tripped() is True


async def test_run_budget_concurrent_charges_no_lost_updates():
    budget = RunBudget(1000.0)

    async def charge_many():
        for _ in range(200):
            await budget.charge(0.01)

    await asyncio.gather(*[charge_many() for _ in range(10)])
    assert await budget.spent_usd() == pytest.approx(0.01 * 200 * 10)


async def test_blocked_call_records_not_attempted_budget_telemetry():
    runtime, transport = make_runtime([(200, response_body(output=[message_output('{"explanation":"unused"}')]))])
    budget = RunBudget(0.0)
    await budget.charge(0.01)  # trips it before any call is attempted
    provider = OpenAILLMProvider(runtime=runtime, run_budget=budget)
    envelope = PromptEnvelope(ctx_key="run:p:score", system="s", user="u")

    with pytest.raises(ProviderBudgetExceeded) as exc_info:
        await provider.structured(envelope, Out, ctx_key=envelope.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)

    assert transport.calls == 0  # never actually attempted the HTTP call
    assert len(exc_info.value.attempts) == 1
    assert exc_info.value.attempts[0].status.value == "NOT_ATTEMPTED_BUDGET"
    await runtime.close()


async def test_process_scoped_semaphore_bounds_concurrent_calls():
    """`LLM_MAX_CONCURRENCY=1` — two logical calls issued concurrently from
    two separate `OpenAILLMProvider`s referencing the SAME runtime (as two
    simultaneous runs would) must still serialize through one semaphore."""
    body = response_body(output=[message_output('{"explanation":"ok"}')])
    runtime, transport = make_runtime([(200, body), (200, body)])
    runtime.semaphore = asyncio.Semaphore(1)

    concurrent_count = 0
    max_concurrent = 0
    lock = asyncio.Lock()
    original_create = runtime.client.responses.create

    async def tracking_create(**kwargs):
        nonlocal concurrent_count, max_concurrent
        async with lock:
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)
        result = await original_create(**kwargs)
        async with lock:
            concurrent_count -= 1
        return result

    runtime.client.responses.create = tracking_create

    provider_a = OpenAILLMProvider(runtime=runtime)  # stands in for "run A"
    provider_b = OpenAILLMProvider(runtime=runtime)  # stands in for "run B"
    envelope = PromptEnvelope(ctx_key="run:p:score", system="s", user="u")

    await asyncio.gather(
        provider_a.structured(envelope, Out, ctx_key=envelope.ctx_key, operation=LLMOperation.SCORE_EXPLANATION),
        provider_b.structured(envelope, Out, ctx_key=envelope.ctx_key, operation=LLMOperation.SCORE_EXPLANATION),
    )
    await runtime.close()
    assert max_concurrent == 1, "two runs sharing one runtime must serialize through its semaphore"


async def test_runtime_close_closes_underlying_client():
    runtime, transport = make_runtime([(200, response_body(output=[message_output('{"explanation":"ok"}')]))])
    assert runtime.client.is_closed() is False
    await runtime.close()
    assert runtime.client.is_closed() is True
