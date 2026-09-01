"""`OpenAILLMProvider` — the real OpenAI `LLMProvider` (Checkpoint G Phase 6).

Uses the Responses API with strict Structured Outputs, `store=False`, the
configured reasoning effort and output-token bound, and `max_retries=0` at
the SDK layer (the process-scoped `LiveProviderRuntime` already disables
SDK-hidden retries — see Phase 5).

CRITICAL RETRY ARCHITECTURE (Checkpoint G): one logical call is ONE flat
loop with counters initialized once, not nested `(1+T) * (1+S)` retry
loops. `transport_retry_index` is a single counter for the whole call that
never resets when a schema-repair attempt fires — it keeps counting through
it. `schema_round` flips from 0 to 1 exactly once, the moment the one
allowed repair attempt is issued, and stays 1 for any transport retries
after that. Max attempts with the default `T=2, S=1` is
`1 + T + S = 4`, never `(1+T)*(1+S) = 6`.

Response classification (Phase 6): `TRUNCATED` (max_output_tokens) and
`REFUSED`/`CONTENT_FILTERED`/`AUTH_ERROR` are permanent — no schema repair,
no step retry, raised immediately. `INVALID_JSON`/`SCHEMA_MISMATCH`/genuine
`NO_OUTPUT` are schema-repairable, once. `TIMEOUT`/`RATE_LIMITED`/5xx
`PROVIDER_ERROR` consume the shared transport-retry budget.

CRITICAL BOUNDARY: this module never imports a repository, SQLAlchemy, or
any DB table model — it only returns `LLMResult`/raises `ProviderError`,
both carrying attempt telemetry. `engine/llm.py` alone persists it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import openai
from pydantic import ValidationError

from groundwork.providers.base import (
    LLMAttemptKind,
    LLMAttemptStatus,
    LLMAttemptTelemetry,
    LLMOperation,
    LLMResult,
    PromptEnvelope,
    ProviderAuthError,
    ProviderBudgetExceeded,
    ProviderContentFiltered,
    ProviderError,
    ProviderOutputTruncated,
    ProviderRateLimited,
    ProviderRefusal,
    ProviderTimeout,
    ProviderUnavailable,
    SchemaViolation,
    T,
    digest_of,
)
from groundwork.providers.live.runtime import LiveProviderRuntime
from groundwork.providers.live.schemas import to_strict_json_schema

# Statuses that consume the shared transport-retry budget.
_TRANSPORT_ERROR_BY_STATUS: dict[LLMAttemptStatus, type[ProviderError]] = {
    LLMAttemptStatus.TIMEOUT: ProviderTimeout,
    LLMAttemptStatus.RATE_LIMITED: ProviderRateLimited,
    LLMAttemptStatus.PROVIDER_ERROR: ProviderUnavailable,
}
# Statuses eligible for the one schema-repair attempt.
_SCHEMA_REPAIRABLE_STATUSES = {
    LLMAttemptStatus.INVALID_JSON,
    LLMAttemptStatus.SCHEMA_MISMATCH,
    LLMAttemptStatus.NO_OUTPUT,
}
# Permanent — raised immediately regardless of remaining budget.
_PERMANENT_ERROR_BY_STATUS: dict[LLMAttemptStatus, type[ProviderError]] = {
    LLMAttemptStatus.REFUSED: ProviderRefusal,
    LLMAttemptStatus.TRUNCATED: ProviderOutputTruncated,
    LLMAttemptStatus.CONTENT_FILTERED: ProviderContentFiltered,
    LLMAttemptStatus.AUTH_ERROR: ProviderAuthError,
}


class _Classified:
    __slots__ = (
        "status", "parsed", "raw_text", "http_status", "request_id", "error_text",
        "incomplete_reason", "reasoning_tokens", "tokens_in", "tokens_out",
    )

    def __init__(self, status: LLMAttemptStatus, **kw: Any) -> None:
        self.status = status
        self.parsed = kw.get("parsed")
        self.raw_text = kw.get("raw_text")
        self.http_status = kw.get("http_status")
        self.request_id = kw.get("request_id")
        self.error_text = kw.get("error_text")
        self.incomplete_reason = kw.get("incomplete_reason")
        self.reasoning_tokens = kw.get("reasoning_tokens")
        self.tokens_in = kw.get("tokens_in", 0)
        self.tokens_out = kw.get("tokens_out", 0)


def _backoff_s(transport_retry_index: int) -> float:
    return min(0.5 * (2 ** (transport_retry_index - 1)), 4.0)


class OpenAILLMProvider:
    name = "openai"

    def __init__(self, *, runtime: LiveProviderRuntime, run_budget=None) -> None:
        self.runtime = runtime
        self.run_budget = run_budget

    async def structured(
        self, envelope: PromptEnvelope, schema: type[T], *, ctx_key: str, operation: LLMOperation
    ) -> LLMResult[T]:
        schema_payload = to_strict_json_schema(schema)
        # H2 post-smoke: a caller may ask for more time than the runtime's
        # shared default (`envelope.metadata["call_deadline_s"]`) — used by
        # `engine/discovery.py` for the bulkier DISCOVERY_EXTRACTION call.
        # Every other operation keeps the runtime default unchanged.
        deadline_s = envelope.metadata.get("call_deadline_s", self.runtime.call_deadline_s)

        if self.run_budget is not None and await self.run_budget.is_tripped():
            now = datetime.now(timezone.utc)
            blocked = LLMAttemptTelemetry(
                attempt=1, attempt_kind=LLMAttemptKind.INITIAL, schema_round=0, transport_retry_index=0,
                status=LLMAttemptStatus.NOT_ATTEMPTED_BUDGET, started_at=now, finished_at=now, latency_ms=0.0,
                model=self.runtime.model, reasoning_effort=self.runtime.reasoning_effort,
                input_digest=digest_of(envelope.user),
            )
            raise ProviderBudgetExceeded("run soft budget already tripped — call not attempted", attempts=[blocked])

        attempts: list[LLMAttemptTelemetry] = []
        transport_retries_consumed = 0
        schema_round = 0
        schema_repair_used = False
        flat_attempt = 0
        base_input = self._build_input(envelope)
        current_input = base_input
        kind_hint = "initial"

        while True:
            flat_attempt += 1
            if kind_hint == "initial":
                kind = LLMAttemptKind.INITIAL
                this_transport_index = 0
            elif kind_hint == "repair":
                kind = LLMAttemptKind.SCHEMA_REPAIR
                this_transport_index = transport_retries_consumed
            else:
                transport_retries_consumed += 1
                kind = LLMAttemptKind.TRANSPORT_RETRY
                this_transport_index = transport_retries_consumed
                await asyncio.sleep(_backoff_s(transport_retries_consumed))

            started = datetime.now(timezone.utc)
            classified = await self._issue(current_input, schema, schema_payload, deadline_s)
            finished = datetime.now(timezone.utc)

            cost = self.runtime.estimate_cost_usd(classified.tokens_in, classified.tokens_out)
            attempt_telemetry = LLMAttemptTelemetry(
                attempt=flat_attempt, attempt_kind=kind, schema_round=schema_round,
                transport_retry_index=this_transport_index, status=classified.status,
                started_at=started, finished_at=finished,
                latency_ms=(finished - started).total_seconds() * 1000,
                model=self.runtime.model, reasoning_effort=self.runtime.reasoning_effort,
                tokens_in=classified.tokens_in, tokens_out=classified.tokens_out,
                tokens_total=classified.tokens_in + classified.tokens_out,
                reasoning_tokens=classified.reasoning_tokens, cost_usd=cost,
                http_status=classified.http_status, provider_request_id=classified.request_id,
                incomplete_reason=classified.incomplete_reason,
                error_type=classified.status.value if classified.status != LLMAttemptStatus.OK else None,
                error_message=classified.error_text,
                input_digest=digest_of(current_input),
                output_digest=digest_of(classified.raw_text) if classified.raw_text else None,
            )
            attempts.append(attempt_telemetry)
            if self.run_budget is not None:
                await self.run_budget.charge(cost)

            if classified.status == LLMAttemptStatus.OK:
                return LLMResult(
                    parsed=classified.parsed, raw=classified.parsed.model_dump(mode="json"),
                    operation=operation, model=self.runtime.model, provider=self.name,
                    prompt_version="live-v1", attempts=attempts,
                )

            if classified.status in _PERMANENT_ERROR_BY_STATUS:
                raise _PERMANENT_ERROR_BY_STATUS[classified.status](
                    f"{classified.status.value}: {classified.error_text or 'permanent provider failure'}",
                    attempts=attempts,
                )

            if classified.status in _SCHEMA_REPAIRABLE_STATUSES:
                if not schema_repair_used:
                    schema_repair_used = True
                    schema_round = 1
                    kind_hint = "repair"
                    current_input = self._build_repair_input(base_input, classified)
                    continue
                raise SchemaViolation(
                    f"schema repair exhausted: {classified.status.value}: {classified.error_text or ''}",
                    attempts=attempts,
                )

            # transport-class failure
            if transport_retries_consumed < self.runtime.max_transport_retries:
                kind_hint = "transport"
                continue
            raise _TRANSPORT_ERROR_BY_STATUS[classified.status](
                f"transport retries exhausted: {classified.status.value}: {classified.error_text or ''}",
                attempts=attempts,
            )

    def _build_input(self, envelope: PromptEnvelope) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": envelope.system},
            {"role": "user", "content": envelope.user},
        ]

    def _build_repair_input(self, base_input: list[dict[str, Any]], classified: _Classified) -> list[dict[str, Any]]:
        problem = classified.error_text or classified.status.value
        previous = (classified.raw_text or "")[:1000]
        repair_note = (
            "Your previous response did not satisfy the required JSON schema. "
            f"Problem: {problem}\nPrevious output: {previous}\n"
            "Respond again with ONLY a single JSON object that strictly satisfies the schema — "
            "no prose, no markdown fences."
        )
        return [*base_input, {"role": "user", "content": repair_note}]

    async def _issue(
        self, input_messages: list[dict[str, Any]], schema: type[T], schema_payload: dict[str, Any],
        deadline_s: float,
    ) -> _Classified:
        kwargs: dict[str, Any] = dict(
            model=self.runtime.model,
            input=input_messages,
            text={"format": schema_payload},
            max_output_tokens=self.runtime.max_output_tokens,
            store=False,
            timeout=deadline_s,
        )
        if self.runtime.reasoning_effort:
            kwargs["reasoning"] = {"effort": self.runtime.reasoning_effort}

        async with self.runtime.semaphore:
            try:
                response = await self.runtime.client.responses.create(**kwargs)
            except openai.AuthenticationError as exc:
                return _Classified(LLMAttemptStatus.AUTH_ERROR, error_text=str(exc), http_status=getattr(exc, "status_code", None))
            except openai.RateLimitError as exc:
                return _Classified(LLMAttemptStatus.RATE_LIMITED, error_text=str(exc), http_status=getattr(exc, "status_code", None))
            except openai.APITimeoutError as exc:
                return _Classified(LLMAttemptStatus.TIMEOUT, error_text=str(exc))
            except openai.APIConnectionError as exc:
                return _Classified(LLMAttemptStatus.PROVIDER_ERROR, error_text=str(exc))
            except openai.APIStatusError as exc:
                return _Classified(LLMAttemptStatus.PROVIDER_ERROR, error_text=str(exc), http_status=exc.status_code)

        return self._classify_response(response, schema)

    def _classify_response(self, response: Any, schema: type[T]) -> _Classified:
        usage = getattr(response, "usage", None)
        tokens_in = usage.input_tokens if usage else 0
        tokens_out = usage.output_tokens if usage else 0
        reasoning_tokens = (
            usage.output_tokens_details.reasoning_tokens
            if usage and getattr(usage, "output_tokens_details", None)
            else None
        )
        request_id = getattr(response, "id", None)
        common = dict(
            http_status=200, request_id=request_id, reasoning_tokens=reasoning_tokens,
            tokens_in=tokens_in, tokens_out=tokens_out,
        )

        status = getattr(response, "status", None)
        if status == "incomplete":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            if reason == "content_filter":
                return _Classified(LLMAttemptStatus.CONTENT_FILTERED, incomplete_reason=reason, **common)
            # max_output_tokens (or an unspecified incomplete reason) is
            # TRUNCATED — permanent even if visible output is empty (Phase
            # 6: "classify it as TRUNCATED even if visible output is empty;
            # retain reasoning token information when exposed").
            return _Classified(LLMAttemptStatus.TRUNCATED, incomplete_reason=reason or "max_output_tokens", **common)

        if status == "failed":
            err = getattr(response, "error", None)
            return _Classified(LLMAttemptStatus.PROVIDER_ERROR, error_text=err.message if err else "response failed", **common)

        message_items = [item for item in (response.output or []) if getattr(item, "type", None) == "message"]
        if not message_items:
            return _Classified(LLMAttemptStatus.NO_OUTPUT, **common)

        content = message_items[0].content
        if any(getattr(c, "type", None) == "refusal" for c in content):
            refusal_text = next((c.refusal for c in content if getattr(c, "type", None) == "refusal"), "")
            return _Classified(LLMAttemptStatus.REFUSED, error_text=refusal_text, **common)

        text = getattr(response, "output_text", None) or "".join(
            getattr(c, "text", "") for c in content if getattr(c, "type", None) == "output_text"
        )
        if not text:
            return _Classified(LLMAttemptStatus.NO_OUTPUT, **common)

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return _Classified(LLMAttemptStatus.INVALID_JSON, raw_text=text, error_text=str(exc), **common)

        try:
            parsed = schema.model_validate(data)
        except ValidationError as exc:
            return _Classified(LLMAttemptStatus.SCHEMA_MISMATCH, raw_text=text, error_text=str(exc), **common)

        return _Classified(LLMAttemptStatus.OK, parsed=parsed, raw_text=text, **common)
