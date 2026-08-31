"""`LLMCallRecorder` — pre-bound to `(run_id, prospect_id)` on `ProspectContext`,
mirroring `TraceRecorder`/`EventEmitter`. The one place a Phase 3
observability-write failure is caught and logged rather than allowed to
convert a successful model operation into a failed prospect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from groundwork.providers.base import LLMAttemptTelemetry
from groundwork.repositories.llm_calls import LLMCallRepository

logger = logging.getLogger(__name__)


@dataclass
class LLMCallRecorder:
    run_id: str
    prospect_id: str
    provider: str
    repo: LLMCallRepository

    async def record(
        self,
        *,
        call_group_id: str,
        operation: str,
        prompt_version: str,
        step_name: str,
        attempts: list[LLMAttemptTelemetry],
    ) -> None:
        try:
            await self.repo.record_attempts(
                call_group_id=call_group_id,
                operation=operation,
                provider=self.provider,
                prompt_version=prompt_version,
                attempts=attempts,
                run_id=self.run_id,
                prospect_id=self.prospect_id,
                step_name=step_name,
            )
        except Exception:  # noqa: BLE001 — observability must never fail the prospect
            logger.exception(
                "llm_calls persistence failed for run=%s prospect=%s step=%s (call_group=%s)",
                self.run_id, self.prospect_id, step_name, call_group_id,
            )
