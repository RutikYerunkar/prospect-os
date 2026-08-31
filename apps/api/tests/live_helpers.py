"""Shared scaffolding for exercising `OpenAILLMProvider` against a scripted
`httpx2.MockTransport` — no automated test may hit a paid API (Checkpoint G
test requirements), so every Responses-API HTTP exchange here is canned.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx2
import openai

from groundwork.providers.live.runtime import LiveProviderRuntime


def message_output(text: str, *, output_id: str = "msg_1") -> dict[str, Any]:
    return {
        "id": output_id, "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def refusal_output(refusal_text: str, *, output_id: str = "msg_1") -> dict[str, Any]:
    return {
        "id": output_id, "type": "message", "role": "assistant", "status": "completed",
        "content": [{"type": "refusal", "refusal": refusal_text}],
    }


def usage(tokens_in: int = 10, tokens_out: int = 5, reasoning_tokens: int = 0) -> dict[str, Any]:
    return {
        "input_tokens": tokens_in,
        "input_tokens_details": {"cache_write_tokens": 0, "cached_tokens": 0},
        "output_tokens": tokens_out,
        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
        "total_tokens": tokens_in + tokens_out,
    }


def response_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "resp_1", "object": "response", "created_at": 0.0, "model": "gpt-5.6-terra",
        "output": [], "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "status": "completed", "usage": usage(),
    }
    body.update(overrides)
    return body


class ScriptedTransport(httpx2.AsyncBaseTransport):
    """Replays `steps` in order, one per outbound request. A step is either
    `(status_code, json_body)` or an `Exception` instance to raise instead
    (e.g. `httpx2.ConnectTimeout` to exercise `ProviderTimeout`)."""

    def __init__(self, steps: list[tuple[int, dict] | Exception]) -> None:
        self.steps = list(steps)
        self.calls = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        step = self.steps[self.calls]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx2.Response(status, json=body, request=request)


def make_runtime(
    steps: list[tuple[int, dict] | Exception], **overrides: Any
) -> tuple[LiveProviderRuntime, ScriptedTransport]:
    transport = ScriptedTransport(steps)
    http_client = httpx2.AsyncClient(transport=transport)
    client = openai.AsyncOpenAI(api_key="test-sk-not-real", max_retries=0, http_client=http_client)
    kwargs: dict[str, Any] = dict(
        client=client,
        semaphore=asyncio.Semaphore(2),
        model="gpt-5.6-terra",
        reasoning_effort="low",
        max_output_tokens=2048,
        call_deadline_s=5.0,
        max_transport_retries=2,
        max_schema_retries=1,
        price_input_usd_per_mtok=None,
        price_output_usd_per_mtok=None,
    )
    kwargs.update(overrides)
    return LiveProviderRuntime(**kwargs), transport
