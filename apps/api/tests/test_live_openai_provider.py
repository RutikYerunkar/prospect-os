"""`OpenAILLMProvider` against a scripted `httpx2.MockTransport` — no
automated test may hit a paid API. Covers: valid structured output, schema
repair success/exhaustion, refusal, truncation, content filtering, genuine
empty output, timeout, rate limit, provider 5xx, auth error, the flat
retry-loop's attempt/telemetry shape, and the property that no sequence of
attempts ever exceeds `1 + T + S`.
"""

from __future__ import annotations

import random

import httpx2
import pytest
from pydantic import BaseModel

from groundwork.providers.base import (
    LLMAttemptKind,
    LLMAttemptStatus,
    LLMOperation,
    PromptEnvelope,
    ProviderAuthError,
    ProviderContentFiltered,
    ProviderOutputTruncated,
    ProviderRateLimited,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    SchemaViolation,
)
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from tests.live_helpers import make_runtime, message_output, refusal_output, response_body


class Out(BaseModel):
    explanation: str


ENVELOPE = PromptEnvelope(ctx_key="run:prospect:score", system="sys", user="user")


async def _call(steps, **runtime_overrides):
    runtime, transport = make_runtime(steps, **runtime_overrides)
    provider = OpenAILLMProvider(runtime=runtime)
    try:
        return await provider.structured(ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)
    finally:
        await runtime.close()


def _ok_body(text='{"explanation": "ok"}'):
    return (200, response_body(output=[message_output(text)]))


async def test_valid_structured_output_single_attempt():
    result = await _call([_ok_body()])
    assert result.parsed.explanation == "ok"
    assert len(result.attempts) == 1
    a = result.attempts[0]
    assert (a.attempt, a.attempt_kind, a.schema_round, a.transport_retry_index, a.status) == (
        1, LLMAttemptKind.INITIAL, 0, 0, LLMAttemptStatus.OK,
    )


async def test_schema_repair_success():
    steps = [(200, response_body(output=[message_output("not json")])), _ok_body('{"explanation": "fixed"}')]
    result = await _call(steps)
    assert result.parsed.explanation == "fixed"
    kinds = [(a.attempt, a.attempt_kind, a.schema_round, a.status) for a in result.attempts]
    assert kinds == [
        (1, LLMAttemptKind.INITIAL, 0, LLMAttemptStatus.INVALID_JSON),
        (2, LLMAttemptKind.SCHEMA_REPAIR, 1, LLMAttemptStatus.OK),
    ]


async def test_schema_repair_exhaustion_raises_permanent():
    steps = [(200, response_body(output=[message_output("not json")]))] * 2
    with pytest.raises(SchemaViolation) as exc_info:
        await _call(steps)
    assert len(exc_info.value.attempts) == 2
    assert exc_info.value.attempts[1].attempt_kind == LLMAttemptKind.SCHEMA_REPAIR


async def test_genuine_empty_output_is_schema_repairable():
    steps = [(200, response_body(output=[])), _ok_body()]
    result = await _call(steps)
    assert result.attempts[0].status == LLMAttemptStatus.NO_OUTPUT
    assert result.attempts[1].attempt_kind == LLMAttemptKind.SCHEMA_REPAIR


async def test_refusal_is_permanent_no_retry():
    steps = [(200, response_body(output=[refusal_output("cannot help with that")]))]
    with pytest.raises(ProviderRefusal) as exc_info:
        await _call(steps)
    assert len(exc_info.value.attempts) == 1


async def test_truncation_is_permanent_even_with_empty_output():
    steps = [(200, response_body(status="incomplete", incomplete_details={"reason": "max_output_tokens"}, output=[]))]
    with pytest.raises(ProviderOutputTruncated) as exc_info:
        await _call(steps)
    assert exc_info.value.attempts[0].status == LLMAttemptStatus.TRUNCATED
    assert exc_info.value.attempts[0].incomplete_reason == "max_output_tokens"


async def test_content_filtered_is_permanent():
    steps = [(200, response_body(status="incomplete", incomplete_details={"reason": "content_filter"}, output=[]))]
    with pytest.raises(ProviderContentFiltered):
        await _call(steps)


async def test_timeout_retries_then_succeeds():
    steps = [httpx2.ConnectTimeout("t"), _ok_body()]
    result = await _call(steps)
    assert [a.status for a in result.attempts] == [LLMAttemptStatus.TIMEOUT, LLMAttemptStatus.OK]
    assert result.attempts[1].attempt_kind == LLMAttemptKind.TRANSPORT_RETRY
    assert result.attempts[1].transport_retry_index == 1


async def test_timeout_exhausts_transport_budget():
    steps = [httpx2.ConnectTimeout("t")] * 3  # initial + 2 retries (T=2)
    with pytest.raises(ProviderTimeout) as exc_info:
        await _call(steps)
    assert len(exc_info.value.attempts) == 3
    assert [a.transport_retry_index for a in exc_info.value.attempts] == [0, 1, 2]


async def test_rate_limited_maps_to_provider_rate_limited():
    steps = [(429, {"error": {"message": "slow down", "type": "rate_limit"}})] * 3
    with pytest.raises(ProviderRateLimited):
        await _call(steps)


async def test_provider_5xx_maps_to_provider_unavailable():
    steps = [(500, {"error": {"message": "boom", "type": "server_error"}})] * 3
    with pytest.raises(ProviderUnavailable):
        await _call(steps)


async def test_auth_error_is_permanent_no_retry():
    steps = [(401, {"error": {"message": "bad key", "type": "invalid_request_error"}})]
    with pytest.raises(ProviderAuthError) as exc_info:
        await _call(steps)
    assert len(exc_info.value.attempts) == 1


async def test_transport_budget_never_resets_after_schema_repair():
    """timeout, timeout (transport budget now fully consumed at T=2),
    invalid_json (schema-repairable -> schedules the one repair attempt),
    then the repair attempt itself times out. Since the transport budget
    was already exhausted by the two earlier retries, that final timeout is
    permanent — it does NOT get a fresh transport-retry round just because
    a repair happened in between. Exactly 4 attempts, never 5."""
    steps = [
        httpx2.ConnectTimeout("t"),
        httpx2.ConnectTimeout("t"),
        (200, response_body(output=[message_output("not json")])),
        httpx2.ConnectTimeout("t"),
    ]
    with pytest.raises(ProviderTimeout) as exc_info:
        await _call(steps)
    attempts = exc_info.value.attempts
    assert len(attempts) == 4
    assert [a.status for a in attempts] == [
        LLMAttemptStatus.TIMEOUT, LLMAttemptStatus.TIMEOUT, LLMAttemptStatus.INVALID_JSON, LLMAttemptStatus.TIMEOUT,
    ]
    assert [a.attempt_kind for a in attempts] == [
        LLMAttemptKind.INITIAL, LLMAttemptKind.TRANSPORT_RETRY, LLMAttemptKind.TRANSPORT_RETRY, LLMAttemptKind.SCHEMA_REPAIR,
    ]
    assert [a.transport_retry_index for a in attempts] == [0, 1, 2, 2]
    assert [a.schema_round for a in attempts] == [0, 0, 0, 1]


async def test_reasoning_tokens_exposed_when_present():
    body = response_body(output=[message_output('{"explanation": "ok"}')])
    body["usage"]["output_tokens_details"]["reasoning_tokens"] = 42
    result = await _call([(200, body)])
    assert result.attempts[0].reasoning_tokens == 42


async def test_provider_request_id_captured():
    body = response_body(id="resp_abc123", output=[message_output('{"explanation": "ok"}')])
    result = await _call([(200, body)])
    assert result.attempts[0].provider_request_id == "resp_abc123"


async def test_cost_null_when_pricing_unconfigured():
    result = await _call([_ok_body()])
    assert result.attempts[0].cost_usd is None


async def test_cost_computed_when_pricing_configured():
    result = await _call(
        [_ok_body()], price_input_usd_per_mtok=5.0, price_output_usd_per_mtok=15.0,
    )
    a = result.attempts[0]
    expected = (a.tokens_in / 1_000_000) * 5.0 + (a.tokens_out / 1_000_000) * 15.0
    assert a.cost_usd == pytest.approx(expected)


async def test_reasoning_effort_omitted_when_empty():
    runtime, transport = make_runtime([_ok_body()], reasoning_effort=None)
    provider = OpenAILLMProvider(runtime=runtime)

    captured = {}
    original_create = runtime.client.responses.create

    async def spy(**kwargs):
        captured.update(kwargs)
        return await original_create(**kwargs)

    runtime.client.responses.create = spy
    await provider.structured(ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)
    await runtime.close()
    assert "reasoning" not in captured


async def test_reasoning_effort_included_when_configured():
    runtime, transport = make_runtime([_ok_body()], reasoning_effort="low")
    provider = OpenAILLMProvider(runtime=runtime)

    captured = {}
    original_create = runtime.client.responses.create

    async def spy(**kwargs):
        captured.update(kwargs)
        return await original_create(**kwargs)

    runtime.client.responses.create = spy
    await provider.structured(ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)
    await runtime.close()
    assert captured["reasoning"] == {"effort": "low"}


# --- property test: no sequence of attempts ever exceeds 1 + T + S -------

_STATUS_POOL = ["timeout", "invalid_json", "ok"]


def _steps_for_plan(plan: list[str]) -> list:
    steps = []
    for kind in plan:
        if kind == "timeout":
            steps.append(httpx2.ConnectTimeout("t"))
        elif kind == "invalid_json":
            steps.append((200, response_body(output=[message_output("not json")])))
        else:
            steps.append(_ok_body())
    return steps


async def test_property_attempt_count_never_exceeds_budget():
    rng = random.Random(1234)
    for _ in range(60):
        # A long random plan of failures, terminated by success — the
        # provider must never actually consume more than 1+T+S=4 of it.
        plan = [rng.choice(["timeout", "invalid_json"]) for _ in range(6)] + ["ok"]
        steps = _steps_for_plan(plan)
        runtime, transport = make_runtime(steps)
        provider = OpenAILLMProvider(runtime=runtime)
        try:
            result = await provider.structured(
                ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION
            )
            attempts = result.attempts
        except Exception as exc:  # noqa: BLE001 — permanent failure is a valid outcome too
            attempts = getattr(exc, "attempts", [])
        assert 1 <= len(attempts) <= 4, f"plan={plan} produced {len(attempts)} attempts"
        # transport_retry_index must be monotonic non-decreasing and never
        # exceed the transport budget (T=2).
        indices = [a.transport_retry_index for a in attempts]
        assert indices == sorted(indices)
        assert max(indices) <= 2
        # at most one schema_repair attempt ever appears.
        assert sum(1 for a in attempts if a.attempt_kind == LLMAttemptKind.SCHEMA_REPAIR) <= 1
        await runtime.close()
