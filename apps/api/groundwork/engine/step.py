"""`Step` — retry/timeout wrapper (docs/ARCHITECTURE.md "Per-step reliability").

Each attempt runs under `asyncio.wait_for`, is recorded as one `agent_tasks`
row (so retries are visible in the trace rather than hidden inside a helper),
and — on a retryable exception — is retried with exponential backoff plus
jitter (0.4s, 0.8s, 1.6s). Idempotent by `(run_id, prospect_id, step_name)`:
a prior `OK` attempt means the work is already done.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from groundwork.engine.context import ProspectContext
from groundwork.providers.base import stable_seed

BACKOFFS_S: tuple[float, ...] = (0.4, 0.8, 1.6)


class StepResult(BaseModel):
    ok: bool = True
    skipped: bool = False
    detail: str = ""


@dataclass
class Step:
    name: str
    run_fn: Callable[[ProspectContext], Awaitable[StepResult]]
    depends_on: tuple[str, ...] = ()
    timeout_s: float = 5.0
    max_retries: int = 0
    retry_on: tuple[type[Exception], ...] = ()
    optional: bool = False

    async def execute(self, ctx: ProspectContext) -> StepResult:
        if await ctx.trace.has_succeeded(self.name):
            return StepResult(ok=True, skipped=True, detail="idempotent: already succeeded")

        attempt = 0
        while True:
            attempt += 1
            started = time.monotonic()
            error: Exception | None = None
            retryable = False
            try:
                result = await asyncio.wait_for(self.run_fn(ctx), timeout=self.timeout_s)
            except TimeoutError as exc:
                error, retryable = exc, True
            except self.retry_on as exc:  # type: ignore[misc]
                error, retryable = exc, True
            except Exception as exc:  # noqa: BLE001 — genuinely "anything else fails immediately"
                error, retryable = exc, False
            else:
                duration_ms = (time.monotonic() - started) * 1000
                await ctx.trace.record(
                    step_name=self.name, attempt=attempt, status="OK", duration_ms=duration_ms,
                    evidence_count=len(ctx.evidence),
                )
                return result

            duration_ms = (time.monotonic() - started) * 1000
            can_retry = retryable and attempt <= self.max_retries
            status = "RETRY" if can_retry else ("TIMEOUT" if isinstance(error, TimeoutError) else "FAILED")
            await ctx.trace.record(
                step_name=self.name, attempt=attempt, status=status, duration_ms=duration_ms,
                error_type=type(error).__name__, error_message=str(error),
            )

            if can_retry:
                backoff = BACKOFFS_S[min(attempt - 1, len(BACKOFFS_S) - 1)]
                jitter = random.Random(
                    stable_seed(ctx.run_id, ctx.prospect_id, self.name, str(attempt))
                ).uniform(0, 0.1)
                await ctx.events.emit(
                    "step.retrying", prospect_id=ctx.prospect_id, step=self.name,
                    attempt=attempt, error_type=type(error).__name__,
                )
                await asyncio.sleep(backoff + jitter)
                continue

            if self.optional:
                ctx.error = f"{self.name}: {error}"
                return StepResult(ok=False, skipped=True, detail=f"{self.name} degraded: {error}")
            raise error
