"""`llm_calls` — one row per provider attempt (Checkpoint G Phase 3).

`record_attempts` is the hot path (called after every logical LLM call, demo
or live); `create_play_with_attempts` is the one-transaction write Phase 9's
objective parser needs so an `llm_calls` row can never reference a `Play`
that doesn't exist (and a failed transaction rolls back both together).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from groundwork.models.tables import LLMCallRow, PlayRow
from groundwork.observability.redact import redact
from groundwork.providers.base import LLMAttemptTelemetry


def _attempt_to_row_kwargs(attempt: LLMAttemptTelemetry, *, operation: str, provider: str) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "attempt": attempt.attempt,
        "attempt_kind": attempt.attempt_kind.value,
        "schema_round": attempt.schema_round,
        "transport_retry_index": attempt.transport_retry_index,
        "operation": operation,
        "provider": provider,
        "model": attempt.model,
        "reasoning_effort": attempt.reasoning_effort,
        "status": attempt.status.value,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "latency_ms": attempt.latency_ms,
        "tokens_in": attempt.tokens_in,
        "tokens_out": attempt.tokens_out,
        "tokens_total": attempt.tokens_total,
        "reasoning_tokens": attempt.reasoning_tokens,
        "cost_usd": attempt.cost_usd,
        "http_status": attempt.http_status,
        "provider_request_id": attempt.provider_request_id,
        "incomplete_reason": attempt.incomplete_reason,
        "error_type": attempt.error_type,
        "error_message": redact(attempt.error_message),
        "validation_error": redact(attempt.validation_error),
        "input_digest": attempt.input_digest,
        "output_digest": attempt.output_digest,
    }


class LLMCallRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def record_attempts(
        self,
        *,
        call_group_id: str,
        operation: str,
        provider: str,
        prompt_version: str,
        attempts: list[LLMAttemptTelemetry],
        run_id: str | None = None,
        prospect_id: str | None = None,
        step_name: str | None = None,
        play_id: str | None = None,
    ) -> None:
        if not attempts:
            return
        async with self._session_factory() as session:
            for attempt in attempts:
                session.add(
                    LLMCallRow(
                        call_group_id=call_group_id,
                        prompt_version=prompt_version,
                        run_id=run_id,
                        prospect_id=prospect_id,
                        step_name=step_name,
                        play_id=play_id,
                        **_attempt_to_row_kwargs(attempt, operation=operation, provider=provider),
                    )
                )
            await session.commit()

    async def create_play_with_attempts(
        self,
        *,
        play_kwargs: dict[str, Any],
        call_group_id: str,
        operation: str,
        provider: str,
        prompt_version: str,
        attempts: list[LLMAttemptTelemetry],
    ) -> str:
        """Phase 9: `Play` creation and its `objective_parse` telemetry in
        ONE transaction — if either insert fails, both roll back, so no
        `llm_calls` row can ever reference a nonexistent `Play`.

        `models/tables.py` has no ORM `relationship()` between `PlayRow` and
        `LLMCallRow` (this codebase uses raw FK columns + manual joins
        throughout, never `relationship()`) — which means SQLAlchemy's
        unit-of-work has no dependency processor telling it `plays` must be
        INSERTed before `llm_calls`, and a single `session.add()` for each
        followed by one `commit()` does NOT guarantee that order. Under
        `PRAGMA foreign_keys=ON` this surfaced as a real
        `sqlite3.IntegrityError: FOREIGN KEY constraint failed` on the
        `llm_calls` INSERT (confirmed by reproduction — the real live
        smoke's second run hit exactly this). The fix is an explicit
        `flush()` after adding the `Play`: `flush()` sends the pending
        INSERT to the database *within the current transaction* (not a
        commit — nothing is durable yet, and everything still rolls back
        together on any later failure or an uncaught exception exiting this
        `async with` block), so `plays.id` genuinely exists by the time the
        `llm_calls` rows are flushed, and the final `commit()` makes both
        durable together, atomically.
        """
        play_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            session.add(PlayRow(id=play_id, **play_kwargs))
            await session.flush()  # INSERT the Play now, same transaction — see docstring above
            for attempt in attempts:
                session.add(
                    LLMCallRow(
                        call_group_id=call_group_id,
                        prompt_version=prompt_version,
                        play_id=play_id,
                        run_id=None,
                        prospect_id=None,
                        step_name=None,
                        **_attempt_to_row_kwargs(attempt, operation=operation, provider=provider),
                    )
                )
            await session.commit()
        return play_id

    async def for_run(self, run_id: str) -> list[LLMCallRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LLMCallRow).where(LLMCallRow.run_id == run_id).order_by(LLMCallRow.started_at)
            )
            return list(result.scalars())

    async def for_play(self, play_id: str) -> list[LLMCallRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(LLMCallRow).where(LLMCallRow.play_id == play_id).order_by(LLMCallRow.started_at)
            )
            return list(result.scalars())

