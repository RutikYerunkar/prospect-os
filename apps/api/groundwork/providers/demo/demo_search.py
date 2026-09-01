"""DemoSearchProvider — fixture-backed `SearchProvider` (§11, ported to the
H1 Phase 12 contract in Phase 13).

Same Protocol as a live search provider. `discover` returns the fixture
company roster; `fetch_sources` returns the fixture's authored sources, with
seeded latency and fixture-configured scripted retry/failure behavior keyed
by `(run_id, prospect_id, step_name)`. Every method now returns its result
alongside `SearchAttemptTelemetry` — Demo Mode always produces exactly one
`OK` attempt per logical call, there being nothing to retry against a
fixture. Zero credentials required; no real URLs are ever invented for
fixture sources.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone

from groundwork.models.schemas import CompanySeed, PlaySpec, SourceDocument
from groundwork.providers.base import (
    FAILURE_TYPES,
    DiscoveryResult,
    DomainCandidates,
    ProviderUnavailable,
    SearchAttemptStatus,
    SearchAttemptTelemetry,
    SearchOperation,
    SourceBundle,
    parse_ctx_key,
    stable_seed,
)
from groundwork.providers.demo.fixtures import FixturePack

_JITTER_MIN_S = 0.03
_JITTER_MAX_S = 0.15


class DemoSearchProvider:
    name = "demo_fixture"

    def __init__(self, pack: FixturePack, seed: int) -> None:
        self.pack = pack
        self.seed = seed
        self._attempt_counts: dict[str, int] = {}

    def _jitter(self, ctx_key: str) -> float:
        rng = random.Random(stable_seed(str(self.seed), ctx_key))
        return _JITTER_MIN_S + rng.random() * (_JITTER_MAX_S - _JITTER_MIN_S)

    def _telemetry(
        self, *, operation: SearchOperation, started: datetime, finished: datetime,
        result_count: int, selected_count: int, query_group_id: str,
    ) -> SearchAttemptTelemetry:
        return SearchAttemptTelemetry(
            provider=self.name,
            operation=operation,
            query_group_id=query_group_id,
            call_group_id=str(uuid.uuid4()),
            status=SearchAttemptStatus.OK,
            started_at=started,
            finished_at=finished,
            latency_ms=(finished - started).total_seconds() * 1000,
            result_count=result_count,
            selected_count=selected_count,
        )

    async def discover(self, spec: PlaySpec, limit: int) -> DiscoveryResult:
        started = datetime.now(timezone.utc)
        await asyncio.sleep(self._jitter(f"discover:{self.seed}"))
        companies = [c.to_company_seed() for c in self.pack.companies[:limit]]
        finished = datetime.now(timezone.utc)
        telemetry = self._telemetry(
            operation=SearchOperation.DISCOVER, started=started, finished=finished,
            result_count=len(self.pack.companies), selected_count=len(companies),
            query_group_id=f"discover:{self.seed}",
        )
        return DiscoveryResult(companies=companies, telemetry=[telemetry])

    async def resolve_domain(self, company_name: str, *, ctx_key: str) -> DomainCandidates:
        """Not exercised by the H1 pipeline (no caller invokes domain
        resolution yet — H2 groundwork). Best-effort fixture lookup by
        case-insensitive name match, offline and deterministic."""
        started = datetime.now(timezone.utc)
        await asyncio.sleep(self._jitter(ctx_key))
        match = next(
            (c for c in self.pack.companies if c.name.lower() == company_name.lower()), None
        )
        domains = [match.domain] if match else []
        finished = datetime.now(timezone.utc)
        telemetry = self._telemetry(
            operation=SearchOperation.RESOLVE_DOMAIN, started=started, finished=finished,
            result_count=len(domains), selected_count=len(domains), query_group_id=ctx_key,
        )
        return DomainCandidates(domains=domains, telemetry=[telemetry])

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> SourceBundle:
        fixture = self.pack.company_by_slug(company.slug)
        _, _, step_name = parse_ctx_key(ctx_key)
        failure = fixture.failure_script.get(step_name)
        if failure is not None:
            attempt = self._attempt_counts.get(ctx_key, 0) + 1
            self._attempt_counts[ctx_key] = attempt
            if attempt <= failure.fail_attempts:
                exc_cls = FAILURE_TYPES.get(failure.error, ProviderUnavailable)
                raise exc_cls(
                    f"scripted {failure.error} for {company.slug}/{step_name}, attempt {attempt}"
                )

        started = datetime.now(timezone.utc)
        await asyncio.sleep(self._jitter(ctx_key))
        retrieved_at = datetime.now(timezone.utc)
        documents = [
            SourceDocument(
                ref=source.ref,
                title=source.title,
                claim=source.claim,
                text=source.snippet,
                source_provider=self.name,
                signal_type=source.signal_type.value if source.signal_type else None,
                confidence=source.confidence,
                url=None,
                canonical_url=None,
                source_type="demo_fixture",
                retrieved_at=retrieved_at,
                provider_result_id=f"{fixture.slug}:{source.ref}",
                rank=index,
                extraction_method="fixture",
            )
            for index, source in enumerate(fixture.sources)
        ]
        finished = datetime.now(timezone.utc)
        telemetry = self._telemetry(
            operation=SearchOperation.FETCH_SOURCES, started=started, finished=finished,
            result_count=len(documents), selected_count=len(documents), query_group_id=ctx_key,
        )
        return SourceBundle(documents=documents, telemetry=[telemetry])
