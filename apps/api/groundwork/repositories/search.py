"""`search_calls` + `source_documents` persistence (H1 Phase 9/10).

Every retrieval occurrence is a row in `source_documents`, `is_winner`
marking the deterministic winner of its identity group and
`canonical_source_id` pointing every loser at its group's winner row — the
"same URL through three queries -> three occurrences, at most one Evidence"
requirement, satisfied at the persistence layer as well as in-memory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from groundwork.domain.source_identity import evidence_id_for, group_occurrences, pick_winner, source_identity
from groundwork.models.tables import SearchCallRow, SourceDocumentRow
from groundwork.observability.redact import redact
from groundwork.providers.base import SearchAttemptTelemetry
from groundwork.models.schemas import SourceDocument


class SearchRepository:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def record_search(
        self,
        *,
        run_id: str,
        prospect_id: str | None = None,
        telemetry: list[SearchAttemptTelemetry],
        documents: list[SourceDocument],
    ) -> None:
        """`prospect_id=None` is the run-level-only case (H1 Phase 1
        deviation closure) — `discover()` runs once per run, before any
        `ProspectContext`/prospect exists, so its `search_calls` rows
        legitimately have no prospect to reference. `documents` is always
        empty for a run-level call (`discover()` returns companies, not
        retrieval occurrences), so the winner/loser `source_documents`
        logic below is simply never exercised for that case — no special
        casing needed."""
        async with self._session_factory() as session:
            call_ids: list[str] = []
            for t in telemetry:
                call_id = str(uuid.uuid4())
                call_ids.append(call_id)
                session.add(
                    SearchCallRow(
                        id=call_id,
                        call_group_id=t.call_group_id,
                        attempt=t.attempt,
                        attempt_kind=t.attempt_kind.value,
                        operation=t.operation.value,
                        run_id=run_id,
                        prospect_id=prospect_id,
                        provider=t.provider,
                        query_group_id=t.query_group_id,
                        template_id=t.template_id,
                        rendered_query=t.rendered_query,
                        query_digest=t.query_digest,
                        status=t.status.value,
                        started_at=t.started_at,
                        finished_at=t.finished_at,
                        latency_ms=t.latency_ms,
                        result_count=t.result_count,
                        selected_count=t.selected_count,
                        provider_request_id=t.provider_request_id,
                        http_status=t.http_status,
                        error_type=t.error_type,
                        error_message=redact(t.error_message),
                        cost_usd=t.cost_usd,
                        chars_retrieved=t.chars_retrieved,
                        credits_used=t.credits_used,
                    )
                )
            if call_ids:
                await session.flush()
            primary_call_id = call_ids[-1] if call_ids else None

            row_id_by_object: dict[int, str] = {id(doc): str(uuid.uuid4()) for doc in documents}
            groups = group_occurrences(documents)
            winner_row_id_by_object: dict[int, str] = {}
            for group in groups:
                winner = pick_winner(group)
                winner_row_id = row_id_by_object[id(winner)]
                for doc in group:
                    winner_row_id_by_object[id(doc)] = winner_row_id

            def _row_kwargs(doc: SourceDocument, *, row_id: str, is_winner: bool, winner_row_id: str) -> dict:
                return dict(
                    id=row_id,
                    search_call_id=doc.search_call_id or primary_call_id,
                    run_id=run_id,
                    prospect_id=prospect_id,
                    ref=doc.ref,
                    title=doc.title,
                    url=doc.url,
                    canonical_url=doc.canonical_url,
                    domain=doc.domain,
                    publisher=doc.publisher,
                    excerpt=doc.text,
                    full_text_length=doc.full_text_length,
                    content_sha256=doc.content_sha256,
                    source_type=doc.source_type,
                    retrieved_at=doc.retrieved_at or datetime.now(timezone.utc),
                    published_at=(
                        datetime.combine(doc.published_at, datetime.min.time()) if doc.published_at else None
                    ),
                    provider=doc.source_provider,
                    provider_result_id=doc.provider_result_id,
                    rank=doc.rank,
                    relevance_score=doc.relevance_score,
                    extraction_method=doc.extraction_method,
                    status=doc.status.value if hasattr(doc.status, "value") else doc.status,
                    origin=doc.origin.value if hasattr(doc.origin, "value") else doc.origin,
                    identity_key=source_identity(doc),
                    is_winner=is_winner,
                    canonical_source_id=None if is_winner else winner_row_id,
                    # Run-scoped (prospect_id=None) discovery-stage occurrences
                    # never become Evidence — nothing downstream ever creates
                    # an Evidence row for them, so there is no real id to
                    # point at.
                    evidence_id=(
                        evidence_id_for(prospect_id, doc) if (is_winner and prospect_id is not None) else None
                    ),
                )

            # Two passes, winners first: `canonical_source_id` is a
            # self-referential FK with no ORM `relationship()` in this
            # codebase (consistent with every other table here — see
            # `repositories/llm_calls.py::create_play_with_attempts`'s
            # docstring for why that means insert ORDER matters under
            # `PRAGMA foreign_keys=ON`). A loser row must never be flushed
            # before the winner row it points at.
            for doc in documents:
                row_id = row_id_by_object[id(doc)]
                winner_row_id = winner_row_id_by_object[id(doc)]
                if row_id == winner_row_id:
                    session.add(SourceDocumentRow(**_row_kwargs(doc, row_id=row_id, is_winner=True, winner_row_id=winner_row_id)))
            await session.flush()
            for doc in documents:
                row_id = row_id_by_object[id(doc)]
                winner_row_id = winner_row_id_by_object[id(doc)]
                if row_id != winner_row_id:
                    session.add(SourceDocumentRow(**_row_kwargs(doc, row_id=row_id, is_winner=False, winner_row_id=winner_row_id)))
            await session.commit()

    async def source_documents_for_run(self, run_id: str) -> list[SourceDocumentRow]:
        async with self._session_factory() as session:
            result = await session.execute(select(SourceDocumentRow).where(SourceDocumentRow.run_id == run_id))
            return list(result.scalars())

    async def search_calls_for_run(self, run_id: str) -> list[SearchCallRow]:
        async with self._session_factory() as session:
            result = await session.execute(select(SearchCallRow).where(SearchCallRow.run_id == run_id))
            return list(result.scalars())
