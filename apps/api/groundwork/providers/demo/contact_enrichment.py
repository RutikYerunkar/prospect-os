"""DemoEnrichmentProvider — fixture-backed `EnrichmentProvider` (v2 §Part 7).

Same Protocol as a live enrichment provider. `enrich_person` returns the
fixture's own provider OBSERVATIONS — never a Groundwork verdict — with
seeded latency and fixture-configured scripted retry/failure behavior keyed
by `(run_id, prospect_id, step_name)`, exactly mirroring
`DemoSearchProvider`. `origin` is always `DEMO_FIXTURE`. Zero credentials
required; no real-looking email address or LinkedIn URL is ever invented for
a fixture observation.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timezone

from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.models.enums import EmailVerificationState, EnrichmentOperation, EnrichmentOrigin
from groundwork.providers.base import digest_of, parse_ctx_key, stable_seed
from groundwork.providers.contact_base import (
    ENRICHMENT_FAILURE_TYPES,
    EnrichmentAttemptKind,
    EnrichmentAttemptStatus,
    EnrichmentAttemptTelemetry,
    EnrichmentBudgetExceeded,
    EnrichmentProviderUnavailable,
    PersonEnrichmentQuery,
    PersonEnrichmentResult,
    ProviderEmailObservation,
    ProviderLinkedInObservation,
)
from groundwork.providers.demo.fixtures import FixturePack

_JITTER_MIN_S = 0.03
_JITTER_MAX_S = 0.15

# Adapter-owned provider-status vocabulary (§Part 4: "Apollo->state mapping
# lives with the adapter, not domain/") — the demo fixture pack's own raw
# provider words, never Groundwork verdicts. `domain/contact_identity.py`
# never sees this map's keys; it only ever receives the already-mapped
# `EmailVerificationState`. An unmapped status fails closed to `UNVERIFIED`
# inside `derive_email_channel` itself — this map need not (and does not)
# cover every conceivable word.
DEMO_EMAIL_STATUS_MAP: dict[str, EmailVerificationState] = {
    "verified": EmailVerificationState.VERIFIED,
    "catch_all": EmailVerificationState.RISKY,
    "risky": EmailVerificationState.RISKY,
    "unverifiable": EmailVerificationState.UNVERIFIABLE,
    "invalid": EmailVerificationState.INVALID,
}


class DemoEnrichmentProvider:
    name = "demo_fixture"
    origin = EnrichmentOrigin.DEMO_FIXTURE
    email_status_map = DEMO_EMAIL_STATUS_MAP

    def __init__(self, pack: FixturePack, seed: int, budget: EnrichmentCallBudget | None = None) -> None:
        self.pack = pack
        self.seed = seed
        self.budget = budget
        self._attempt_counts: dict[str, int] = {}

    def _jitter(self, ctx_key: str) -> float:
        rng = random.Random(stable_seed(str(self.seed), "contact_enrichment", ctx_key))
        return _JITTER_MIN_S + rng.random() * (_JITTER_MAX_S - _JITTER_MIN_S)

    async def enrich_person(self, q: PersonEnrichmentQuery, *, ctx_key: str) -> PersonEnrichmentResult:
        _run_id, _prospect_id, step_name = parse_ctx_key(ctx_key)
        input_digest = digest_of((q.full_name, q.title, q.company_name, q.company_domain))

        if self.budget is not None and not await self.budget.reserve_call():
            now = datetime.now(timezone.utc)
            blocked = EnrichmentAttemptTelemetry(
                provider=self.name, operation=EnrichmentOperation.PERSON_ENRICHMENT,
                call_group_id=str(uuid.uuid4()), attempt=1, attempt_kind=EnrichmentAttemptKind.INITIAL,
                status=EnrichmentAttemptStatus.NOT_ATTEMPTED_BUDGET, started_at=now, finished_at=now,
                latency_ms=0.0, input_digest=input_digest,
            )
            raise EnrichmentBudgetExceeded(
                "run enrichment-call budget already exhausted — call not attempted", telemetry=[blocked]
            )

        fixture = self.pack.company_by_domain(q.company_domain)
        # Keyed by `EnrichmentOperation` value (§Part 7's
        # `enrichment_failure_script: person_enrichment: {...}`), not by
        # pipeline step name — there is only ever one enrichment operation
        # in v2.
        failure = fixture.enrichment_failure_script.get(EnrichmentOperation.PERSON_ENRICHMENT.value) if fixture else None
        attempt = self._attempt_counts.get(ctx_key, 0) + 1
        self._attempt_counts[ctx_key] = attempt

        if failure is not None and attempt <= failure.fail_attempts:
            now = datetime.now(timezone.utc)
            exc_cls = ENRICHMENT_FAILURE_TYPES.get(failure.error, EnrichmentProviderUnavailable)
            telemetry = [
                EnrichmentAttemptTelemetry(
                    provider=self.name, operation=EnrichmentOperation.PERSON_ENRICHMENT,
                    call_group_id=str(uuid.uuid4()), attempt=attempt, attempt_kind=EnrichmentAttemptKind.INITIAL,
                    status=EnrichmentAttemptStatus.PROVIDER_ERROR, started_at=now, finished_at=now,
                    latency_ms=1.0, error_type=failure.error, input_digest=input_digest,
                )
            ]
            raise exc_cls(
                f"scripted {failure.error} for {q.company_domain}/{step_name}, attempt {attempt}",
                telemetry=telemetry,
            )

        started = datetime.now(timezone.utc)
        await asyncio.sleep(self._jitter(ctx_key))
        finished = datetime.now(timezone.utc)
        observed_at = finished

        enrichment = fixture.enrichment if fixture else None
        matched = bool(enrichment and enrichment.matched)

        email_obs: ProviderEmailObservation | None = None
        if enrichment and enrichment.email:
            email_obs = ProviderEmailObservation(
                address=enrichment.email.address,
                provider_status=enrichment.email.provider_status,
                provider_confidence=enrichment.email.provider_confidence,
                is_catch_all=enrichment.email.is_catch_all,
                observed_at=observed_at,
            )

        linkedin_obs: ProviderLinkedInObservation | None = None
        if enrichment and enrichment.linkedin:
            linkedin_obs = ProviderLinkedInObservation(
                profile_url=enrichment.linkedin.profile_url,
                asserted_full_name=enrichment.linkedin.asserted_full_name,
                asserted_company_name=enrichment.linkedin.asserted_company_name,
                asserted_company_domain=enrichment.linkedin.asserted_company_domain,
                asserted_title=enrichment.linkedin.asserted_title,
                observed_at=observed_at,
            )

        telemetry = [
            EnrichmentAttemptTelemetry(
                provider=self.name, operation=EnrichmentOperation.PERSON_ENRICHMENT,
                call_group_id=str(uuid.uuid4()), attempt=attempt, attempt_kind=EnrichmentAttemptKind.INITIAL,
                status=EnrichmentAttemptStatus.OK, started_at=started, finished_at=finished,
                latency_ms=(finished - started).total_seconds() * 1000,
                input_digest=input_digest,
                output_digest=digest_of((matched, email_obs, linkedin_obs)),
            )
        ]
        return PersonEnrichmentResult(
            matched=matched,
            provider_person_id=f"demo-person-{fixture.slug}" if (fixture and matched) else None,
            email=email_obs,
            linkedin=linkedin_obs,
            origin=EnrichmentOrigin.DEMO_FIXTURE,
            raw_digest=digest_of((fixture.slug if fixture else q.company_domain, matched)),
            telemetry=telemetry,
        )
