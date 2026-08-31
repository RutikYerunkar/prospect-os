"""`agent_tasks` — the trace. One row per step *attempt* (§15)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from groundwork.models.tables import AgentTaskRow


class TaskRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def record(
        self,
        *,
        run_id: str,
        prospect_id: str,
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
        async with self._session_factory() as session:
            session.add(
                AgentTaskRow(
                    id=str(uuid.uuid4()),
                    run_id=run_id,
                    prospect_id=prospect_id,
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
            )
            await session.commit()

    async def has_succeeded(self, run_id: str, prospect_id: str, step_name: str) -> bool:
        """Idempotency check: a prior SUCCESS attempt for this key means the
        step's work is already done — see §10 "Idempotency"."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentTaskRow.id).where(
                    AgentTaskRow.run_id == run_id,
                    AgentTaskRow.prospect_id == prospect_id,
                    AgentTaskRow.step_name == step_name,
                    AgentTaskRow.status == "OK",
                )
            )
            return result.first() is not None

    async def for_run(self, run_id: str) -> list[AgentTaskRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentTaskRow).where(AgentTaskRow.run_id == run_id).order_by(AgentTaskRow.started_at)
            )
            return list(result.scalars())

    async def for_prospect(self, prospect_id: str) -> list[AgentTaskRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentTaskRow)
                .where(AgentTaskRow.prospect_id == prospect_id)
                .order_by(AgentTaskRow.started_at)
            )
            return list(result.scalars())
