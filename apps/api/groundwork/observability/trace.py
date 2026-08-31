"""`TraceRecorder` — pre-bound to `(run_id, prospect_id)` on `ProspectContext`
so step code never has to pass those ids around. One `record()` call per
step *attempt* (§15); `input_digest`/`output_digest` are sha256 prefixes,
enough to prove determinism without persisting full payloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from groundwork.repositories.tasks import TaskRepository


def digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode()).hexdigest()[:16]


@dataclass
class TraceRecorder:
    run_id: str
    prospect_id: str
    tasks: TaskRepository

    async def record(
        self,
        *,
        step_name: str,
        attempt: int,
        status: str,
        duration_ms: float,
        model: str | None = None,
        provider: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        error_type: str | None = None,
        error_message: str | None = None,
        input_digest: str | None = None,
        output_digest: str | None = None,
        evidence_count: int = 0,
    ) -> None:
        await self.tasks.record(
            run_id=self.run_id,
            prospect_id=self.prospect_id,
            step_name=step_name,
            attempt=attempt,
            status=status,
            duration_ms=duration_ms,
            model=model,
            provider=provider,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            error_type=error_type,
            error_message=error_message,
            input_digest=input_digest,
            output_digest=output_digest,
            evidence_count=evidence_count,
        )

    async def has_succeeded(self, step_name: str) -> bool:
        return await self.tasks.has_succeeded(self.run_id, self.prospect_id, step_name)
