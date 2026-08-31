"""Persistence for everything hanging off a prospect: evidence, signals,
scores, contacts, outreach drafts, review results, approvals."""

from __future__ import annotations

import uuid

from groundwork.models.schemas import Contact, Evidence, ICPScore, OutreachDraft, ReviewResult, Signal
from groundwork.models.tables import (
    ContactRow,
    EvidenceRow,
    ICPScoreRow,
    OutreachDraftRow,
    ReviewResultRow,
    SignalRow,
)


class ProspectDataRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def insert_evidence(self, evidence: list[Evidence]) -> None:
        if not evidence:
            return
        async with self._session_factory() as session:
            for e in evidence:
                session.add(
                    EvidenceRow(
                        id=e.id,
                        prospect_id=e.prospect_id,
                        source_url=e.source_url,
                        source_ref=e.source_ref,
                        source_provider=e.source_provider,
                        title=e.title,
                        claim=e.claim,
                        snippet=e.snippet,
                        signal_type=e.signal_type.value if e.signal_type else None,
                        retrieved_at=e.retrieved_at,
                        confidence=e.confidence,
                        origin=e.origin.value,
                    )
                )
            await session.commit()

    async def insert_signals(self, signals: list[Signal]) -> None:
        if not signals:
            return
        async with self._session_factory() as session:
            for s in signals:
                session.add(
                    SignalRow(
                        id=s.id,
                        prospect_id=s.prospect_id,
                        type=s.type.value,
                        summary=s.summary,
                        confidence=s.confidence,
                        evidence_ids=s.evidence_ids,
                    )
                )
            await session.commit()

    async def upsert_score(self, score: ICPScore) -> None:
        async with self._session_factory() as session:
            session.add(
                ICPScoreRow(
                    id=str(uuid.uuid4()),
                    prospect_id=score.prospect_id,
                    overall=score.overall,
                    dimensions=[d.model_dump(mode="json") for d in score.dimensions],
                    modifiers=[m.model_dump(mode="json") for m in score.modifiers],
                    disqualified=score.disqualified,
                    explanation=score.explanation,
                    confidence=score.confidence,
                    rubric_version=score.rubric_version,
                )
            )
            await session.commit()

    async def upsert_contact(self, contact: Contact) -> None:
        async with self._session_factory() as session:
            session.add(
                ContactRow(
                    id=str(uuid.uuid4()),
                    prospect_id=contact.prospect_id,
                    full_name=contact.full_name,
                    title=contact.title,
                    persona=contact.persona_match,
                    linkedin_url=contact.linkedin_url,
                    email=contact.email,
                    verification=contact.verification.value,
                    evidence_ids=contact.evidence_ids,
                )
            )
            await session.commit()

    async def insert_drafts(self, drafts: list[OutreachDraft]) -> None:
        if not drafts:
            return
        async with self._session_factory() as session:
            for d in drafts:
                session.add(
                    OutreachDraftRow(
                        id=str(uuid.uuid4()),
                        prospect_id=d.prospect_id,
                        channel=d.channel,
                        step_index=d.step_index,
                        subject=d.subject,
                        body=d.body,
                        claim_map=[c.model_dump(mode="json") for c in d.claim_map],
                        version=d.version,
                        status="DRAFT",
                    )
                )
            await session.commit()

    async def insert_review_result(self, review: ReviewResult) -> None:
        async with self._session_factory() as session:
            session.add(
                ReviewResultRow(
                    id=str(uuid.uuid4()),
                    prospect_id=review.prospect_id,
                    verdict=review.verdict.value,
                    checks=[c.model_dump(mode="json") for c in review.checks],
                    reasons=review.reasons,
                )
            )
            await session.commit()
