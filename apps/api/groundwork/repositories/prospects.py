from __future__ import annotations

import uuid

from sqlalchemy import select  # noqa: F401 (re-exported for callers building filters)

from groundwork.models.schemas import CompanySeed
from groundwork.models.tables import CompanyRow, ProspectRow
from groundwork.observability.redact import redact
from groundwork.timeutil import utcnow


class CompanyRepository:
    """Companies are canonical and persist across runs (§20). Unique on
    `canonical_domain` — the cross-run dedupe target."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def get_or_create(
        self, company: CompanySeed, canonical_domain: str, normalized_name: str, *, origin: str = "demo_fixture"
    ) -> str:
        async with self._session_factory() as session:
            result = await session.execute(
                select(CompanyRow).where(CompanyRow.canonical_domain == canonical_domain)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return existing.id
            row = CompanyRow(
                id=str(uuid.uuid4()),
                canonical_domain=canonical_domain,
                normalized_name=normalized_name,
                display_name=company.name,
                profile=company.model_dump(mode="json"),
                origin=origin,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def get(self, company_id: str) -> CompanyRow | None:
        async with self._session_factory() as session:
            return await session.get(CompanyRow, company_id)

    async def get_many(self, company_ids: list[str]) -> dict[str, CompanyRow]:
        if not company_ids:
            return {}
        async with self._session_factory() as session:
            result = await session.execute(select(CompanyRow).where(CompanyRow.id.in_(company_ids)))
            return {row.id: row for row in result.scalars()}


class ProspectRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(
        self, *, run_id: str, company_id: str, dedupe_key: str, duplicate_of: str | None, status: str
    ) -> str:
        prospect_id = str(uuid.uuid4())
        async with self._session_factory() as session:
            session.add(
                ProspectRow(
                    id=prospect_id,
                    run_id=run_id,
                    company_id=company_id,
                    status=status,
                    current_stage="DISCOVERED",
                    dedupe_key=dedupe_key,
                    duplicate_of=duplicate_of,
                )
            )
            await session.commit()
        return prospect_id

    async def update_stage(self, prospect_id: str, stage: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(ProspectRow, prospect_id)
            row.current_stage = stage
            await session.commit()

    async def finalize(self, prospect_id: str, *, status: str, error: str | None = None) -> None:
        async with self._session_factory() as session:
            row = await session.get(ProspectRow, prospect_id)
            row.status = status
            # Checkpoint I1 Phase 9: same redaction choke point as
            # `runs.error`/`agent_tasks.error_message` — `error` here is
            # typically `str(exception)` from `engine/runner.py`.
            row.error = redact(error)
            row.completed_at = utcnow()
            await session.commit()

    async def get(self, prospect_id: str) -> ProspectRow | None:
        async with self._session_factory() as session:
            return await session.get(ProspectRow, prospect_id)

    async def list_for_run(self, run_id: str) -> list[ProspectRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ProspectRow).where(ProspectRow.run_id == run_id).order_by(ProspectRow.created_at)
            )
            return list(result.scalars())
