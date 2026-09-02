"""`call_structured()` — the single call site every pipeline step uses to
invoke `ctx.providers.llm.structured(...)` (Checkpoint G Phase 3).

This is the CRITICAL BOUNDARY the plan names: `providers/` never imports a
repository or SQLAlchemy — providers only *return* attempt telemetry (or
carry it on a raised `ProviderError`). This module is the only thing that
persists it, via `ctx.llm_calls` (an `observability.llm_calls.LLMCallRecorder`,
pre-bound to `(run_id, prospect_id)` exactly like `ctx.trace`/`ctx.events`).

It also rolls the logical call's model/provider/token totals up onto
`ctx.llm_rollup[step_name]`, which `engine/step.py` folds into that step's
`agent_tasks` OK row — the "TraceTable Provider/model column becomes
useful" requirement.
"""

from __future__ import annotations

import logging
import uuid

from groundwork.engine.context import ProspectContext
from groundwork.providers.base import LLMOperation, LLMResult, PromptEnvelope, ProviderError, T

logger = logging.getLogger(__name__)


def _total_latency_ms(attempts: list) -> float:
    return sum(a.latency_ms for a in attempts if a.latency_ms is not None)


async def call_structured(
    ctx: ProspectContext,
    envelope: PromptEnvelope,
    schema: type[T],
    *,
    operation: LLMOperation,
    step_name: str,
    prompt_version: str,
) -> LLMResult[T]:
    call_group_id = str(uuid.uuid4())
    try:
        result = await ctx.providers.llm.structured(
            envelope, schema, ctx_key=envelope.ctx_key, operation=operation
        )
    except ProviderError as exc:
        await ctx.llm_calls.record(
            call_group_id=call_group_id,
            operation=operation.value,
            prompt_version=prompt_version,
            step_name=step_name,
            attempts=exc.attempts,
        )
        # Checkpoint I1 Phase 9C — one structured summary log line per
        # logical call, never the prompt/response bodies (those never
        # leave `llm_calls`, and even there only redacted error text is
        # kept). `extra` fields are picked up by the JSON formatter.
        logger.warning(
            "llm call failed operation=%s step=%s attempts=%d",
            operation.value, step_name, len(exc.attempts),
            extra={
                "run_id": ctx.run_id, "prospect_id": ctx.prospect_id,
                "latency_ms": _total_latency_ms(exc.attempts),
            },
        )
        raise

    await ctx.llm_calls.record(
        call_group_id=call_group_id,
        operation=operation.value,
        prompt_version=prompt_version,
        step_name=step_name,
        attempts=result.attempts,
    )
    ctx.note_llm_call(step_name, result)
    logger.info(
        "llm call ok operation=%s step=%s attempts=%d",
        operation.value, step_name, len(result.attempts),
        extra={
            "run_id": ctx.run_id, "prospect_id": ctx.prospect_id,
            "latency_ms": _total_latency_ms(result.attempts),
        },
    )
    return result
