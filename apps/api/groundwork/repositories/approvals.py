"""`approvals` — the audit trail of human approve/reject decisions (§20).

A prospect's engine-computed `status` (PASS / NEEDS_REVIEW / REJECTED / ...)
is never overwritten by a human decision — that would destroy the "why did
the engine land here" record. The human decision lives here instead, as its
own append-only trail; `latest_for_prospect` is what the API surfaces as the
prospect's approval state.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select

from groundwork.models.tables import ApprovalRow


class ApprovalRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(
        self, *, prospect_id: str, decision: str, actor: str, reason: str | None = None
    ) -> ApprovalRow:
        async with self._session_factory() as session:
            row = ApprovalRow(
                id=str(uuid.uuid4()),
                prospect_id=prospect_id,
                decision=decision,
                actor=actor,
                reason=reason,
                decided_at=datetime.utcnow(),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def latest_for_prospect(self, prospect_id: str) -> ApprovalRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApprovalRow)
                .where(ApprovalRow.prospect_id == prospect_id)
                .order_by(ApprovalRow.decided_at.desc())
            )
            return result.scalars().first()

    async def latest_for_prospects(self, prospect_ids: list[str]) -> dict[str, ApprovalRow]:
        """Batch lookup for run-level listings. Keeps only the most recent
        decision per prospect (rows are already fetched newest-first)."""
        if not prospect_ids:
            return {}
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApprovalRow)
                .where(ApprovalRow.prospect_id.in_(prospect_ids))
                .order_by(ApprovalRow.decided_at.desc())
            )
            latest: dict[str, ApprovalRow] = {}
            for row in result.scalars():
                latest.setdefault(row.prospect_id, row)
            return latest
