"""`SearchCallRecorder` — pre-bound to `(run_id, prospect_id)` on
`ProspectContext`, mirroring `LLMCallRecorder`. Persists search telemetry
and retrieval occurrences; a persistence failure here must never convert a
successful search into a failed prospect.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from groundwork.models.schemas import SourceDocument
from groundwork.providers.base import SearchAttemptTelemetry
from groundwork.repositories.search import SearchRepository

logger = logging.getLogger(__name__)


@dataclass
class SearchCallRecorder:
    run_id: str
    prospect_id: str
    repo: SearchRepository

    async def record(
        self, *, telemetry: list[SearchAttemptTelemetry], documents: list[SourceDocument]
    ) -> None:
        try:
            await self.repo.record_search(
                run_id=self.run_id,
                prospect_id=self.prospect_id,
                telemetry=telemetry,
                documents=documents,
            )
        except Exception:  # noqa: BLE001 — observability must never fail the prospect
            logger.exception(
                "search_calls persistence failed for run=%s prospect=%s", self.run_id, self.prospect_id
            )
