"""`approvals` — the audit trail of human approve/reject decisions (§20).

A prospect's engine-computed `status` (PASS / NEEDS_REVIEW / REJECTED / ...)
is never overwritten by a human decision — that would destroy the "why did
the engine land here" record. The human decision lives here instead, as its
own append-only trail; `latest_for_prospect` is what the API surfaces as the
prospect's approval state.

V2-H, Part 5/D4: this table now also carries `ACTION`-scope rows — one per
governed-outreach approve/reject decision, binding `action_proposal_id` +
`content_hash` + `hash_version` together (never just the hash; enforced by
`ck_approvals_action_scope_complete`, §H). `latest_for_prospect`/
`latest_for_prospects` are filtered to `scope == PROSPECT` explicitly (the
V2-H-introduced scope leak this checkpoint fixes BEFORE any ACTION-scope row
is ever written) — the existing v1 prospect-approval aggregate/UI must never
see an ACTION-scope row mixed into its "latest decision" lookup, and
vice versa (§12 in the V2-H test matrix).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from groundwork.models.enums import ApprovalScope
from groundwork.models.tables import ApprovalRow
from groundwork.timeutil import utcnow


class ApprovalRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def create(
        self, *, prospect_id: str, decision: str, actor: str, reason: str | None = None
    ) -> ApprovalRow:
        """PROSPECT-scope only (v1, unchanged) — every row this method
        writes takes `scope="PROSPECT"` from the model default."""
        async with self._session_factory() as session:
            row = ApprovalRow(
                id=str(uuid.uuid4()),
                prospect_id=prospect_id,
                decision=decision,
                actor=actor,
                reason=reason,
                decided_at=utcnow(),
                scope=ApprovalScope.PROSPECT.value,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def latest_for_prospect(self, prospect_id: str) -> ApprovalRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApprovalRow)
                .where(
                    ApprovalRow.prospect_id == prospect_id,
                    ApprovalRow.scope == ApprovalScope.PROSPECT.value,
                )
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
                .where(
                    ApprovalRow.prospect_id.in_(prospect_ids),
                    ApprovalRow.scope == ApprovalScope.PROSPECT.value,
                )
                .order_by(ApprovalRow.decided_at.desc())
            )
            latest: dict[str, ApprovalRow] = {}
            for row in result.scalars():
                latest.setdefault(row.prospect_id, row)
            return latest

    # --- V2-H: ACTION-scope approvals (§3.2/§D4) ---

    async def create_action_approval(
        self,
        *,
        prospect_id: str,
        action_proposal_id: str,
        content_hash: str,
        hash_version: str,
        decision: str,
        actor: str,
        reason: str | None = None,
    ) -> ApprovalRow:
        """An `ACTION`-scope approval binds `action_proposal_id` +
        `content_hash` + `hash_version` together — never just the hash
        (structurally enforced by `ck_approvals_action_scope_complete`, so a
        caller that omitted one of these three would fail at the DB, not
        silently persist an incomplete binding)."""
        async with self._session_factory() as session:
            row = ApprovalRow(
                id=str(uuid.uuid4()),
                prospect_id=prospect_id,
                decision=decision,
                actor=actor,
                reason=reason,
                decided_at=utcnow(),
                scope=ApprovalScope.ACTION.value,
                action_proposal_id=action_proposal_id,
                content_hash=content_hash,
                hash_version=hash_version,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def latest_for_proposal(self, action_proposal_id: str) -> ApprovalRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ApprovalRow)
                .where(
                    ApprovalRow.action_proposal_id == action_proposal_id,
                    ApprovalRow.scope == ApprovalScope.ACTION.value,
                )
                .order_by(ApprovalRow.decided_at.desc())
            )
            return result.scalars().first()

    async def get_many(self, approval_ids: list[str]) -> dict[str, ApprovalRow]:
        """V2-J evaluation metrics — batch lookup by primary key (e.g. every
        `action_executions.approval_id` in a run), so the
        approval-to-execution latency metric doesn't issue one query per
        execution."""
        ids = [i for i in approval_ids if i]
        if not ids:
            return {}
        async with self._session_factory() as session:
            result = await session.execute(select(ApprovalRow).where(ApprovalRow.id.in_(ids)))
            return {row.id: row for row in result.scalars()}
