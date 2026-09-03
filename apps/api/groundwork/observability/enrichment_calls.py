"""`EnrichmentCallRecorder` — pre-bound to `(run_id, prospect_id)` on
`ProspectContext`, mirroring `LLMCallRecorder`/`SearchCallRecorder`.

Deliberately DOES NOT swallow persistence exceptions the way
`LLMCallRecorder`/`SearchCallRecorder` do. Those two only ever persist pure
telemetry — a write failure there must never turn a successful model/search
call into a failed prospect. This recorder also derives and persists
`contact_channels`, which is load-bearing state a later checkpoint's action
policy reads — a persistence failure here is a real defect, not an
observability blind spot, so it is left to propagate. Since
`engine/steps/contact_enrichment.py`'s step is `optional=True`, a propagated
failure still only degrades this one prospect's enrichment (visible in the
trace) rather than crashing the whole run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from groundwork.models.enums import EmailVerificationState
from groundwork.providers.contact_base import EnrichmentAttemptTelemetry, PersonEnrichmentResult
from groundwork.repositories.contact_enrichment import ContactEnrichmentRepository


@dataclass
class EnrichmentCallRecorder:
    run_id: str
    prospect_id: str
    provider: str
    repo: ContactEnrichmentRepository

    async def record_success(
        self,
        *,
        call_group_id: str,
        telemetry: list[EnrichmentAttemptTelemetry],
        result: PersonEnrichmentResult,
        email_status_map: Mapping[str, EmailVerificationState],
        grounded_full_name: str | None,
        grounded_company_name: str | None,
        grounded_company_domain: str | None,
    ) -> None:
        await self.repo.record_success(
            run_id=self.run_id, prospect_id=self.prospect_id, provider=self.provider,
            call_group_id=call_group_id, telemetry=telemetry, result=result,
            email_status_map=email_status_map, grounded_full_name=grounded_full_name,
            grounded_company_name=grounded_company_name, grounded_company_domain=grounded_company_domain,
        )

    async def record_failure(self, *, call_group_id: str, telemetry: list[EnrichmentAttemptTelemetry]) -> None:
        await self.repo.record_failure(
            run_id=self.run_id, prospect_id=self.prospect_id, provider=self.provider,
            call_group_id=call_group_id, telemetry=telemetry,
        )
