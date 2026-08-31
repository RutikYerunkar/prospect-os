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

import uuid

from groundwork.engine.context import ProspectContext
from groundwork.providers.base import LLMOperation, LLMResult, PromptEnvelope, ProviderError, T


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
        raise

    await ctx.llm_calls.record(
        call_group_id=call_group_id,
        operation=operation.value,
        prompt_version=prompt_version,
        step_name=step_name,
        attempts=result.attempts,
    )
    ctx.note_llm_call(step_name, result)
    return result
