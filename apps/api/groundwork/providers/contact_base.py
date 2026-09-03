"""Contact-enrichment provider Protocol (v2 §Part 4), separate from
`providers/base.py` only to avoid a 700-line file — same idioms, same
error-hierarchy shape, same telemetry field names as the LLM/search
boundaries.

The Protocol returns provider OBSERVATIONS only (D2): a `PersonEnrichmentResult`
never carries `EmailDiscoveryState`, `EmailVerificationState`,
`LinkedInResolutionState`, `LinkedInIdentityState`, a review verdict, or
action eligibility — `domain/contact_identity.py`'s pure derivations turn an
observation into a state; this module never does that itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Mapping, Protocol

from pydantic import BaseModel, Field

from groundwork.models.enums import (
    EmailVerificationState,
    EnrichmentOperation,
    EnrichmentOrigin,
)
from groundwork.models.enums import EnrichmentAttemptStatus as EnrichmentAttemptStatus  # re-exported

# `ProviderEmailObservation`/`ProviderLinkedInObservation` are defined in
# `models/schemas.py` (pure data structures `domain/` also imports) and
# re-exported here for every call site that imports them from this
# provider-boundary module — the same precedent `providers/base.py` sets for
# `SourceDocument`.
from groundwork.models.schemas import ProviderEmailObservation, ProviderLinkedInObservation

__all__ = [
    "EnrichmentAttemptKind",
    "EnrichmentAttemptStatus",
    "EnrichmentAttemptTelemetry",
    "PersonEnrichmentQuery",
    "PersonEnrichmentResult",
    "ProviderEmailObservation",
    "ProviderLinkedInObservation",
    "EnrichmentProviderError",
    "EnrichmentTimeout",
    "EnrichmentRateLimited",
    "EnrichmentProviderUnavailable",
    "EnrichmentAuthError",
    "EnrichmentInvalidResponse",
    "EnrichmentQuotaExceeded",
    "EnrichmentBudgetExceeded",
    "ENRICHMENT_STEP_RETRYABLE",
    "EnrichmentProvider",
]


class EnrichmentAttemptKind(StrEnum):
    """Mirrors `SearchAttemptKind`/`LLMAttemptKind` — provider-boundary-local,
    not a domain concept."""

    INITIAL = "initial"
    TRANSPORT_RETRY = "transport_retry"


class EnrichmentAttemptTelemetry(BaseModel):
    """One provider call attempt — the enrichment-side analogue of
    `SearchAttemptTelemetry`. `engine/enrichment.py::call_enrichment()` is
    the only thing that persists these (into `enrichment_calls`); providers
    only produce them, exactly like the LLM/search boundaries."""

    provider: str
    operation: EnrichmentOperation
    call_group_id: str
    attempt: int = 1
    attempt_kind: EnrichmentAttemptKind = EnrichmentAttemptKind.INITIAL
    status: EnrichmentAttemptStatus = EnrichmentAttemptStatus.OK
    started_at: datetime
    finished_at: datetime
    latency_ms: float = 0.0
    http_status: int | None = None
    provider_request_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None  # redacted before persistence
    cost_usd: float | None = None
    credits_used: float | None = None
    input_digest: str | None = None
    output_digest: str | None = None


class PersonEnrichmentQuery(BaseModel):
    """What Groundwork asks a provider to look up — never carries an
    identifier of its own, only the grounded facts already established by
    the `contact`/`research` steps."""

    full_name: str | None = None
    title: str | None = None
    company_name: str
    company_domain: str


class PersonEnrichmentResult(BaseModel):
    """What the provider ASSERTED (D2) — never a Groundwork verdict.
    `origin` is carried, never inferred downstream (rev 3)."""

    matched: bool
    provider_person_id: str | None = None
    email: ProviderEmailObservation | None = None
    linkedin: ProviderLinkedInObservation | None = None
    origin: EnrichmentOrigin
    raw_digest: str
    telemetry: list[EnrichmentAttemptTelemetry] = Field(default_factory=list)


# --- errors -----------------------------------------------------------


class EnrichmentProviderError(Exception):
    """Base for exceptions provider implementations raise. Carries whatever
    `EnrichmentAttemptTelemetry` was produced before the failure, if any, so
    `engine/enrichment.py::call_enrichment()` can persist a FAILED
    `enrichment_calls` row even when the logical call never returns a
    result — mirrors `ProviderError`/`SearchProviderError` exactly."""

    def __init__(self, message: str, *, telemetry: list[EnrichmentAttemptTelemetry] | None = None) -> None:
        super().__init__(message)
        self.telemetry: list[EnrichmentAttemptTelemetry] = telemetry or []


# Step-level retryable (transient — retrying the whole step, and thus a
# fresh logical call, might succeed).
class EnrichmentTimeout(EnrichmentProviderError):
    pass


class EnrichmentRateLimited(EnrichmentProviderError):
    pass


class EnrichmentProviderUnavailable(EnrichmentProviderError):
    pass


# Permanent — never step-retried.
class EnrichmentAuthError(EnrichmentProviderError):
    pass


class EnrichmentInvalidResponse(EnrichmentProviderError):
    pass


class EnrichmentQuotaExceeded(EnrichmentProviderError):
    """The provider's own account/billing balance is exhausted — never
    retried, mirrors `ProviderQuotaExceeded`."""


class EnrichmentBudgetExceeded(EnrichmentProviderError):
    """The run's `EnrichmentCallBudget` was already exhausted when this call
    would have started (§Part 4/§E) — Groundwork's own structural ceiling,
    never the provider's — mirrors `ProviderBudgetExceeded`. Not step-
    retryable: retrying cannot recover a spent budget slot."""


# Legacy-name lookup for the demo fixture pack's `enrichment_failure_script.
# error` string, mirroring `FAILURE_TYPES` in `providers/base.py`.
ENRICHMENT_FAILURE_TYPES: dict[str, type[EnrichmentProviderError]] = {
    "EnrichmentTimeout": EnrichmentTimeout,
    "EnrichmentProviderUnavailable": EnrichmentProviderUnavailable,
    "EnrichmentRateLimited": EnrichmentRateLimited,
}

# Exactly the three types that are transient at the step-retry level —
# mirrors `STEP_RETRYABLE` in `providers/base.py`.
ENRICHMENT_STEP_RETRYABLE: tuple[type[Exception], ...] = (
    EnrichmentTimeout,
    EnrichmentProviderUnavailable,
    EnrichmentRateLimited,
)


class EnrichmentProvider(Protocol):
    name: str
    origin: EnrichmentOrigin  # a static property of the implementation

    # Adapter-owned provider-status vocabulary (§Part 4: "Apollo->state
    # mapping lives with the adapter, not domain/") — `engine/enrichment.py`
    # reads this generically off whichever provider ran and threads it into
    # `domain/contact_identity.py::derive_email_channel`'s `status_map`
    # parameter, so neither `engine/` nor `repositories/` ever hardcodes a
    # provider's name.
    email_status_map: Mapping[str, EmailVerificationState]

    async def enrich_person(self, q: PersonEnrichmentQuery, *, ctx_key: str) -> PersonEnrichmentResult: ...
