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
    ProviderQuotaExceeded,
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


async def test_call_deadline_override_from_envelope_metadata_reaches_request(monkeypatch) -> None:
    """H2 post-smoke: `engine/discovery.py` requests a longer deadline for
    DISCOVERY_EXTRACTION than the runtime default
    (`config.py::llm_discovery_call_deadline_s`) via
    `envelope.metadata["call_deadline_s"]`. Proves that value — not the
    runtime's own default — is the one actually passed to the outbound
    `responses.create(timeout=...)` call: wraps the real (scripted-
    transport-backed) `create()` to capture its kwargs while still letting
    the normal request/response flow execute, rather than hand-faking an
    SDK response object."""
    runtime, transport = make_runtime([_ok_body(), _ok_body()])
    captured: list[dict] = []
    original_create = runtime.client.responses.create

    async def capturing_create(**kwargs):
        captured.append(kwargs)
        return await original_create(**kwargs)

    monkeypatch.setattr(runtime.client.responses, "create", capturing_create)
    provider = OpenAILLMProvider(runtime=runtime)

    # No override -> the runtime's own default deadline.
    await provider.structured(ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)
    assert captured[0]["timeout"] == runtime.call_deadline_s

    # An envelope carrying an override -> that value, not the default.
    long_envelope = PromptEnvelope(
        ctx_key=ENVELOPE.ctx_key, system=ENVELOPE.system, user=ENVELOPE.user,
        metadata={"call_deadline_s": 999.0},
    )
    await provider.structured(
        long_envelope, Out, ctx_key=long_envelope.ctx_key, operation=LLMOperation.DISCOVERY_EXTRACTION
    )
    assert captured[1]["timeout"] == 999.0
    await runtime.close()


async def test_rate_limited_maps_to_provider_rate_limited():
    steps = [(429, {"error": {"message": "slow down", "type": "rate_limit"}})] * 3
    with pytest.raises(ProviderRateLimited):
        await _call(steps)


## H2 second post-smoke fix — quota/credit exhaustion vs. an ordinary
## transient rate limit. A real smoke hit exactly this: a 429 whose body
## carried `type=insufficient_quota, code=credit_balance_exhausted` was
## being misclassified as RATE_LIMITED and burned all 3 transport retries
## chasing an account balance retrying can never refill.


async def test_temporary_rate_limit_429_is_retried_then_succeeds():
    """(A) An ordinary transient 429 (no quota/billing signal in the body)
    stays RATE_LIMITED and transport-retryable, unchanged."""
    steps = [(429, {"error": {"message": "slow down", "type": "rate_limit"}}), _ok_body()]
    result = await _call(steps)
    assert [a.status for a in result.attempts] == [LLMAttemptStatus.RATE_LIMITED, LLMAttemptStatus.OK]
    assert result.attempts[1].attempt_kind == LLMAttemptKind.TRANSPORT_RETRY


async def test_quota_exhausted_429_is_permanent_single_attempt():
    """(B) A 429 identifying account/project quota or billing exhaustion
    is classified QUOTA_EXHAUSTED and raised immediately — no transport
    retry, no schema repair, exactly one attempt total, regardless of how
    many retries would otherwise be available."""
    error_body = {
        "error": {
            "message": (
                "You exceeded your current quota, please check your plan and billing details. "
                "https://platform.openai.com/account/billing"
            ),
            "type": "insufficient_quota",
            "code": "credit_balance_exhausted",
        }
    }
    steps = [(429, error_body)]
    with pytest.raises(ProviderQuotaExceeded) as exc_info:
        await _call(steps, max_transport_retries=2, max_schema_retries=1)
    assert len(exc_info.value.attempts) == 1
    attempt = exc_info.value.attempts[0]
    assert attempt.status == LLMAttemptStatus.QUOTA_EXHAUSTED
    assert attempt.attempt_kind == LLMAttemptKind.INITIAL


async def test_quota_exhausted_detected_from_code_alone():
    """`code=credit_balance_exhausted` alone (without a matching `type`)
    is still enough — the real smoke's observed body had both, but the
    classifier checks either field."""
    steps = [(429, {"error": {"message": "boom", "type": "some_other_type", "code": "credit_balance_exhausted"}})]
    with pytest.raises(ProviderQuotaExceeded) as exc_info:
        await _call(steps)
    assert len(exc_info.value.attempts) == 1


async def test_quota_exhausted_telemetry_persists_correctly(session_factory) -> None:
    """(C) The single QUOTA_EXHAUSTED attempt persists to `llm_calls` like
    any other attempt — no special-cased persistence path. Uses the
    repository directly (bypassing `LLMCallRecorder`'s per-prospect
    binding) so this doesn't need a real `plays`/`runs`/`prospects` row —
    the same run-scoped, no-prospect-yet pattern `engine/discovery.py`
    already uses for its own LLM operations."""
    from groundwork.repositories.llm_calls import LLMCallRepository

    steps = [(429, {"error": {"message": "boom", "type": "insufficient_quota", "code": "credit_balance_exhausted"}})]
    runtime, transport = make_runtime(steps)
    provider = OpenAILLMProvider(runtime=runtime)
    repo = LLMCallRepository(session_factory)
    try:
        with pytest.raises(ProviderQuotaExceeded) as exc_info:
            await provider.structured(ENVELOPE, Out, ctx_key=ENVELOPE.ctx_key, operation=LLMOperation.SCORE_EXPLANATION)
        await repo.record_attempts(
            call_group_id="cg1", operation=LLMOperation.SCORE_EXPLANATION.value, provider=provider.name,
            prompt_version="v1", attempts=exc_info.value.attempts,
        )
    finally:
        await runtime.close()

    async with session_factory() as session:
        from sqlalchemy import select

        from groundwork.models.tables import LLMCallRow

        result = await session.execute(select(LLMCallRow).where(LLMCallRow.call_group_id == "cg1"))
        persisted = list(result.scalars())
    assert len(persisted) == 1
    assert persisted[0].status == "QUOTA_EXHAUSTED"
    assert persisted[0].attempt == 1


async def test_quota_exhausted_error_message_redacted_of_configured_secret(monkeypatch) -> None:
    """(D) No secret leakage: a configured OPENAI_API_KEY embedded in the
    raw provider error text is stripped before it would ever be persisted
    — `redact()` is secret-agnostic and needs no QUOTA_EXHAUSTED-specific
    change, but this proves that stays true for this new status too."""
    import groundwork.config as config_module
    from groundwork.observability.redact import redact

    monkeypatch.setattr(config_module.settings, "openai_api_key", "sk-super-secret-test-key")
    steps = [(429, {"error": {
        "message": "invalid key sk-super-secret-test-key: insufficient_quota",
        "type": "insufficient_quota", "code": "credit_balance_exhausted",
    }})]
    with pytest.raises(ProviderQuotaExceeded) as exc_info:
        await _call(steps)
    raw_message = exc_info.value.attempts[0].error_message
    assert "sk-super-secret-test-key" in raw_message  # not redacted in-memory (matches existing LLM telemetry contract)
    assert "sk-super-secret-test-key" not in redact(raw_message)
    assert "[REDACTED]" in redact(raw_message)


def test_search_smoke_quota_message_never_echoes_raw_billing_url() -> None:
    """(D) The smoke script's own clean diagnostic never contains a URL,
    regardless of what the real provider error text says."""
    from groundwork.scripts.search_smoke import _QUOTA_EXHAUSTED_MESSAGE, _describe_error

    assert "http" not in _QUOTA_EXHAUSTED_MESSAGE
    raw = "You exceeded your quota: insufficient_quota. See https://platform.openai.com/account/billing"
    described = _describe_error(raw)
    assert described == _QUOTA_EXHAUSTED_MESSAGE
    assert "http" not in described


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
