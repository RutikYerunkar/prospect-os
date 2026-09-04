"""`ApolloEnrichmentProvider` — the real live `EnrichmentProvider` (V2-D),
behind the identical `EnrichmentProvider` Protocol `DemoEnrichmentProvider`
satisfies. There is no Apollo Python SDK in this codebase by design — every
HTTP request is issued directly via `httpx.AsyncClient` against the pinned
`POST /api/v1/people/match` contract (query parameters only, no JSON body —
`x-api-key` header auth). Tests inject a scripted `httpx.MockTransport`
(`tests/live_enrichment_helpers.py`); zero real Apollo calls are ever made
by an automated test.

CRITICAL BOUNDARY (mirrors `providers/live/tavily_search.py`): this module
never imports a repository, SQLAlchemy, or a DB table model — it only
returns `PersonEnrichmentResult`/`EnrichmentAttemptTelemetry` or raises a
typed `EnrichmentProviderError` carrying whatever telemetry was produced
before the failure; `engine/enrichment.py::call_enrichment()` alone persists
it. This provider returns provider OBSERVATIONS only (D2) — never a
Groundwork verdict, never PASS/NEEDS_REVIEW, never action eligibility, never
a precomputed identity-match state.

UNVERIFIED UNTIL THE V2-D SMOKE (§Part 15 risk 2): the exact Apollo
HTTP-200 no-match response shape has never been observed. `_issue()`
therefore recognizes exactly one 200 shape as a match —
`{"person": {"id": <truthy>, ...}}` — and treats every other 200 body
(including a genuine no-match, whose real shape is still unknown) as
`EnrichmentInvalidResponse` rather than silently guessing it means
`matched=False`. Once a real smoke confirms the no-match shape, add a single
recognizing branch inside `_issue()`; the retry/error/budget machinery below
is unaffected by that future change.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.models.enums import EmailVerificationState, EnrichmentOperation, EnrichmentOrigin
from groundwork.providers.base import digest_of
from groundwork.providers.contact_base import (
    EnrichmentAttemptKind,
    EnrichmentAttemptStatus,
    EnrichmentAttemptTelemetry,
    EnrichmentAuthError,
    EnrichmentBudgetExceeded,
    EnrichmentInvalidResponse,
    EnrichmentProviderError,
    EnrichmentProviderUnavailable,
    EnrichmentRateLimited,
    EnrichmentTimeout,
    PersonEnrichmentQuery,
    PersonEnrichmentResult,
    ProviderEmailObservation,
    ProviderLinkedInObservation,
)
from groundwork.providers.live.enrichment_runtime import APOLLO_PEOPLE_MATCH_PATH, ApolloRuntime

# Adapter-owned provider-status vocabulary (§Part 4: "Apollo->state mapping
# lives with the adapter, not domain/"). Keys are Apollo's own raw
# `email_status` words, verbatim — the two confirmed by the frozen plan.
# Anything else (including a future/unknown status word) fails closed to
# UNVERIFIED *inside* `derive_email_channel` itself, never guessed here.
APOLLO_EMAIL_STATUS_MAP: dict[str, EmailVerificationState] = {
    "verified": EmailVerificationState.VERIFIED,
    "extrapolated": EmailVerificationState.RISKY,
}

_TRANSPORT_RETRYABLE = {
    EnrichmentAttemptStatus.TIMEOUT,
    EnrichmentAttemptStatus.PROVIDER_ERROR,
    EnrichmentAttemptStatus.RATE_LIMITED,
}
_ERROR_CLASS_BY_STATUS: dict[EnrichmentAttemptStatus, type[EnrichmentProviderError]] = {
    EnrichmentAttemptStatus.TIMEOUT: EnrichmentTimeout,
    EnrichmentAttemptStatus.RATE_LIMITED: EnrichmentRateLimited,
    EnrichmentAttemptStatus.PROVIDER_ERROR: EnrichmentProviderUnavailable,
    EnrichmentAttemptStatus.AUTH_ERROR: EnrichmentAuthError,
    EnrichmentAttemptStatus.INVALID_RESPONSE: EnrichmentInvalidResponse,
}


def _backoff_s(retry_index: int) -> float:
    return min(0.5 * (2 ** (retry_index - 1)), 4.0)


def _asserted_full_name(person: dict[str, Any]) -> str | None:
    """`person.name` verbatim; falls back to `first_name + last_name` ONLY
    when `name` itself is absent/blank (pinned mapping rule) — never the
    reverse, and never a fabricated combination when both are missing."""
    name = person.get("name")
    if isinstance(name, str) and name.strip():
        return name
    first = person.get("first_name")
    last = person.get("last_name")
    parts = [p.strip() for p in (first, last) if isinstance(p, str) and p.strip()]
    return " ".join(parts) if parts else None


def _safe_error_text(response: httpx.Response) -> str:
    # Bounded — `observability/redact.py::redact()` truncates again at
    # persistence time, but an unbounded raw body should never even reach
    # that far. Never includes request headers (the `x-api-key` we sent is
    # never echoed back into telemetry from this side).
    try:
        return response.text[:500]
    except Exception:  # noqa: BLE001 — a body-decode failure must never crash telemetry construction
        return f"HTTP {response.status_code}"


def _request_id_from_headers(headers: httpx.Headers) -> str | None:
    # Best-effort only — Apollo's actual request-id header name (if any) is
    # unverified as of V2-D; this reads a conventional header name and is
    # simply `None` when absent. Never fabricated.
    return headers.get("x-request-id")


class ApolloEnrichmentProvider:
    name = "apollo"
    origin = EnrichmentOrigin.LIVE_PROVIDER
    email_status_map = APOLLO_EMAIL_STATUS_MAP

    def __init__(self, *, runtime: ApolloRuntime, budget: EnrichmentCallBudget | None = None) -> None:
        self.runtime = runtime
        self.budget = budget

    async def enrich_person(self, q: PersonEnrichmentQuery, *, ctx_key: str) -> PersonEnrichmentResult:
        input_digest = digest_of((q.full_name, q.title, q.company_name, q.company_domain))

        # Budget reserved ONCE per logical call, before any DNS/socket/
        # semaphore work — a denial makes zero network activity (§Part 4/§E).
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

        # Pinned contract (§Part 4): query parameters only, no JSON body, the
        # full name sent whole (never split), and all four opt-outs always
        # explicit. Never a `webhook_url` param.
        params = {
            "name": q.full_name or "",
            "domain": q.company_domain,
            "reveal_personal_emails": "false",
            "reveal_phone_number": "false",
            "run_waterfall_email": "false",
            "run_waterfall_phone": "false",
        }

        raw, telemetry = await self._call_apollo(params, input_digest=input_digest)
        # `_call_apollo`/`_issue` already raised `EnrichmentInvalidResponse`
        # for anything not strictly `{"person": {"id": <truthy>}}`-shaped —
        # reaching here means `person` is a dict with a non-empty `id`.
        person: dict[str, Any] = raw["person"]
        observed_at = telemetry[-1].finished_at

        email_obs = ProviderEmailObservation(
            address=person.get("email"),
            provider_status=person.get("email_status"),
            provider_confidence=None,
            is_catch_all=None,
            observed_at=observed_at,
        )

        organization = person.get("organization")
        organization = organization if isinstance(organization, dict) else None
        linkedin_obs = ProviderLinkedInObservation(
            profile_url=person.get("linkedin_url"),
            asserted_full_name=_asserted_full_name(person),
            asserted_company_name=organization.get("name") if organization else None,
            # Only ever populated when Apollo itself supplies it — never
            # back-filled from the query's `company_domain` (pinned rule).
            asserted_company_domain=organization.get("primary_domain") if organization else None,
            asserted_title=person.get("title"),
            observed_at=observed_at,
        )

        return PersonEnrichmentResult(
            matched=True,
            provider_person_id=str(person["id"]),
            email=email_obs,
            linkedin=linkedin_obs,
            origin=EnrichmentOrigin.LIVE_PROVIDER,
            raw_digest=digest_of(raw),  # digest only — the raw payload is never persisted
            telemetry=telemetry,
        )

    # -- internals -----------------------------------------------------------

    async def _call_apollo(
        self, params: dict[str, str], *, input_digest: str
    ) -> tuple[dict[str, Any], list[EnrichmentAttemptTelemetry]]:
        """One logical enrichment-provider call — a single, flat transport-
        retry loop (never nested), bounded at `1 + APOLLO_MAX_TRANSPORT_
        RETRIES` attempts. Every attempt (success or failure) is appended to
        the returned telemetry list; on exhaustion this raises the matching
        typed `EnrichmentProviderError` carrying every attempt made so far,
        mirroring `TavilySearchProvider._call_tavily` exactly."""
        call_group_id = str(uuid.uuid4())
        attempts: list[EnrichmentAttemptTelemetry] = []
        transport_retry_index = 0
        flat_attempt = 0

        while True:
            flat_attempt += 1
            kind = EnrichmentAttemptKind.INITIAL if transport_retry_index == 0 else EnrichmentAttemptKind.TRANSPORT_RETRY
            if transport_retry_index > 0:
                await asyncio.sleep(_backoff_s(transport_retry_index))

            started = datetime.now(timezone.utc)
            status, raw, http_status, request_id, error_text = await self._issue(params)
            finished = datetime.now(timezone.utc)

            attempt_telemetry = EnrichmentAttemptTelemetry(
                provider=self.name, operation=EnrichmentOperation.PERSON_ENRICHMENT,
                call_group_id=call_group_id, attempt=flat_attempt, attempt_kind=kind, status=status,
                started_at=started, finished_at=finished,
                latency_ms=(finished - started).total_seconds() * 1000,
                http_status=http_status, provider_request_id=request_id,
                error_type=(status.value if status != EnrichmentAttemptStatus.OK else None),
                error_message=error_text,
                # Never inferred (§Part 4/telemetry) — no verified numeric
                # usage field has been observed on a real Apollo response.
                cost_usd=None, credits_used=None,
                input_digest=input_digest, output_digest=digest_of(raw) if raw is not None else None,
            )
            attempts.append(attempt_telemetry)

            if status == EnrichmentAttemptStatus.OK:
                return raw, attempts  # type: ignore[return-value]

            if status not in _TRANSPORT_RETRYABLE:
                raise _ERROR_CLASS_BY_STATUS.get(status, EnrichmentInvalidResponse)(
                    f"{status.value}: {error_text or 'permanent enrichment provider failure'}", telemetry=attempts
                )

            if transport_retry_index < self.runtime.max_transport_retries:
                transport_retry_index += 1
                continue
            raise _ERROR_CLASS_BY_STATUS.get(status, EnrichmentProviderUnavailable)(
                f"transport retries exhausted: {status.value}: {error_text or ''}", telemetry=attempts
            )

    async def _issue(
        self, params: dict[str, str]
    ) -> tuple[EnrichmentAttemptStatus, dict[str, Any] | None, int | None, str | None, str | None]:
        async with self.runtime.semaphore:
            try:
                response = await asyncio.wait_for(
                    self.runtime.client.post(APOLLO_PEOPLE_MATCH_PATH, params=params),
                    timeout=self.runtime.call_deadline_s,
                )
            except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
                return EnrichmentAttemptStatus.TIMEOUT, None, None, None, str(exc) or "timeout"
            except httpx.HTTPError as exc:
                # Any other transport-class failure (connect/read/etc.) —
                # never a status code, since no response was ever received.
                return EnrichmentAttemptStatus.PROVIDER_ERROR, None, None, None, str(exc)

        status_code = response.status_code
        request_id = _request_id_from_headers(response.headers)

        if status_code in (401, 403):
            return EnrichmentAttemptStatus.AUTH_ERROR, None, status_code, request_id, _safe_error_text(response)
        if status_code in (404, 422):
            return EnrichmentAttemptStatus.INVALID_RESPONSE, None, status_code, request_id, _safe_error_text(response)
        if status_code == 429:
            return EnrichmentAttemptStatus.RATE_LIMITED, None, status_code, request_id, _safe_error_text(response)
        if 500 <= status_code < 600:
            return EnrichmentAttemptStatus.PROVIDER_ERROR, None, status_code, request_id, _safe_error_text(response)
        if status_code != 200:
            # Any other non-2xx (other 4xx, 3xx, ...) — permanent, never
            # guessed at (pinned §Part 4 error policy).
            return EnrichmentAttemptStatus.INVALID_RESPONSE, None, status_code, request_id, _safe_error_text(response)

        try:
            raw = response.json()
        except ValueError:
            return EnrichmentAttemptStatus.INVALID_RESPONSE, None, status_code, request_id, "non-JSON response body"

        if not isinstance(raw, dict):
            return EnrichmentAttemptStatus.INVALID_RESPONSE, None, status_code, request_id, "non-dict response body"

        person = raw.get("person")
        if not isinstance(person, dict) or not person.get("id"):
            # Strict envelope (§Part 4/UNVERIFIED note above): a genuine
            # no-match's real shape is unknown, so this is never silently
            # converted into `matched=False` — it fails closed as an
            # invalid/unrecognized response instead.
            return (
                EnrichmentAttemptStatus.INVALID_RESPONSE, raw, status_code, request_id,
                "response missing a valid top-level person.id",
            )

        return EnrichmentAttemptStatus.OK, raw, status_code, request_id, None
