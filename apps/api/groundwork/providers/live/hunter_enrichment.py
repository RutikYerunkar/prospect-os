"""`HunterEnrichmentProvider` — the second live `EnrichmentProvider` (V2-DH),
behind the identical `EnrichmentProvider` Protocol `ApolloEnrichmentProvider`/
`DemoEnrichmentProvider` satisfy. There is no Hunter Python SDK in this
codebase by design — every HTTP request is issued directly via
`httpx.AsyncClient` against the pinned `GET /v2/email-finder` contract (query
parameters `domain`/`full_name` only, no JSON body — `X-API-KEY` header
auth). Tests inject a scripted `httpx.MockTransport`
(`tests/live_hunter_helpers.py`); zero real Hunter calls are ever made by an
automated test.

CRITICAL BOUNDARY (mirrors `providers/live/apollo_enrichment.py`): this
module never imports a repository, SQLAlchemy, or a DB table model — it only
returns `PersonEnrichmentResult`/`EnrichmentAttemptTelemetry` or raises a
typed `EnrichmentProviderError` carrying whatever telemetry was produced
before the failure; `engine/enrichment.py::call_enrichment()` alone persists
it. This provider returns provider OBSERVATIONS only (D2) — never a
Groundwork verdict, never PASS/NEEDS_REVIEW, never action eligibility, never
a precomputed identity-match state.

UNVERIFIED UNTIL THE REAL HUNTER SMOKE (§Part 17, not run this session): the
exact HTTP-200 no-email response body shape, and whether Hunter's response
carries a request-id/correlation header. Both are handled fail-safely without
assuming either — see `_issue()` below and `data.email`'s malformed-vs-empty
distinction.
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
    EnrichmentQuotaExceeded,
    EnrichmentRateLimited,
    EnrichmentTimeout,
    PersonEnrichmentQuery,
    PersonEnrichmentResult,
    ProviderEmailObservation,
    ProviderLinkedInObservation,
)
from groundwork.providers.live.hunter_runtime import HUNTER_EMAIL_FINDER_PATH, HunterRuntime

# Adapter-owned provider-status vocabulary (§Part 4/§5: "Hunter->state
# mapping lives with the adapter, not domain/"). Keys are Hunter's own
# documented `verification.status` words, verbatim — the Email Finder
# vocabulary is EXACTLY these three. Anything else (absent/malformed/
# undocumented/future) fails closed to UNVERIFIED *inside*
# `derive_email_channel` itself, never guessed here.
HUNTER_EMAIL_STATUS_MAP: dict[str, EmailVerificationState] = {
    "valid": EmailVerificationState.VERIFIED,
    "accept_all": EmailVerificationState.RISKY,
    "unknown": EmailVerificationState.UNVERIFIED,
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
    EnrichmentAttemptStatus.QUOTA_EXHAUSTED: EnrichmentQuotaExceeded,
}


def _backoff_s(retry_index: int) -> float:
    return min(0.5 * (2 ** (retry_index - 1)), 4.0)


def _clean_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _score_to_confidence(value: Any) -> float | None:
    """`data.score` -> `[0, 1]`. Anything not a plain in-range number (a
    bool counts as "not a number" here — `isinstance(True, int)` is `True`
    in Python, and a bool score is not a documented shape) fails closed to
    `None`, never a guessed confidence."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not (0 <= value <= 100):
        return None
    return value / 100.0


def _asserted_full_name(data: dict[str, Any]) -> str | None:
    """RESPONSE `first_name`/`last_name` combined only — never the request
    name we sent (this is not request-name parsing)."""
    first = data.get("first_name")
    last = data.get("last_name")
    parts = [p.strip() for p in (first, last) if isinstance(p, str) and p.strip()]
    return " ".join(parts) if parts else None


def _best_effort_error_id(raw: dict[str, Any] | None) -> str | None:
    """`errors[0].id`, best-effort, for telemetry only — exception
    classification stays HTTP-status-driven regardless of what (if
    anything) this extracts."""
    if not isinstance(raw, dict):
        return None
    errors = raw.get("errors")
    if not isinstance(errors, list) or not errors:
        return None
    first = errors[0]
    if not isinstance(first, dict):
        return None
    err_id = first.get("id")
    return err_id if isinstance(err_id, str) else None


def _safe_error_text(response: httpx.Response) -> str:
    try:
        return response.text[:500]
    except Exception:  # noqa: BLE001 — a body-decode failure must never crash telemetry construction
        return f"HTTP {response.status_code}"


def _request_id_from_headers(headers: httpx.Headers) -> str | None:
    # Best-effort only — whether Hunter's response carries a request-id/
    # correlation header, and its exact name, is unverified as of V2-DH
    # (§Part 12/17); this reads a conventional header name and is simply
    # `None` when absent. Never fabricated.
    return headers.get("x-request-id")


class HunterEnrichmentProvider:
    name = "hunter"
    origin = EnrichmentOrigin.LIVE_PROVIDER
    email_status_map = HUNTER_EMAIL_STATUS_MAP

    def __init__(self, *, runtime: HunterRuntime, budget: EnrichmentCallBudget | None = None) -> None:
        self.runtime = runtime
        self.budget = budget

    async def enrich_person(self, q: PersonEnrichmentQuery, *, ctx_key: str) -> PersonEnrichmentResult:
        input_digest = digest_of((q.full_name, q.title, q.company_name, q.company_domain))

        # Defense-in-depth (§Part 4): the pipeline already stops when
        # `ctx.contact.full_name is None`, but a blank/whitespace-only name
        # must never reach the network either — zero budget consumption,
        # zero network, a clean NOT_FOUND-shaped observation.
        if not (q.full_name or "").strip():
            now = datetime.now(timezone.utc)
            return PersonEnrichmentResult(
                matched=False, provider_person_id=None, email=None, linkedin=None,
                origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest=digest_of(None),
                telemetry=[
                    EnrichmentAttemptTelemetry(
                        provider=self.name, operation=EnrichmentOperation.PERSON_ENRICHMENT,
                        call_group_id=str(uuid.uuid4()), attempt=1, attempt_kind=EnrichmentAttemptKind.INITIAL,
                        status=EnrichmentAttemptStatus.NOT_FOUND, started_at=now, finished_at=now,
                        latency_ms=0.0, input_digest=input_digest,
                    )
                ],
            )

        # Budget reserved ONCE per logical call, before any DNS/socket/
        # semaphore work — a denial makes zero network activity.
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

        # Pinned contract (§Part 3): query parameters EXACTLY `domain`/
        # `full_name`, the full name sent whole (never split, never
        # stripped of honorifics/suffixes), no JSON body, no `api_key` query
        # param — auth is the `X-API-KEY` header only.
        params = {"domain": q.company_domain, "full_name": q.full_name}

        raw, telemetry = await self._call_hunter(params, input_digest=input_digest)
        data_raw = raw.get("data")
        data = data_raw if isinstance(data_raw, dict) else {}
        observed_at = telemetry[-1].finished_at

        email_value = data.get("email")
        address = email_value.strip() if isinstance(email_value, str) and email_value.strip() else None

        verification = data.get("verification")
        provider_status = None
        if isinstance(verification, dict):
            status_value = verification.get("status")
            provider_status = status_value if isinstance(status_value, str) else None

        accept_all = data.get("accept_all")
        email_obs = ProviderEmailObservation(
            address=address,
            provider_status=provider_status,
            provider_confidence=_score_to_confidence(data.get("score")),
            is_catch_all=accept_all if isinstance(accept_all, bool) else None,
            observed_at=observed_at,
        )

        linkedin_obs = ProviderLinkedInObservation(
            profile_url=_clean_str(data.get("linkedin_url")),
            asserted_full_name=_asserted_full_name(data),
            asserted_company_name=_clean_str(data.get("company")),
            # NEVER mapped from the query's `domain` echoed back in
            # `data.domain` — that would let the query self-confirm company
            # identity (§Part 5's pinned rule). Always None by construction.
            asserted_company_domain=None,
            asserted_title=_clean_str(data.get("position")),
            observed_at=observed_at,
        )

        return PersonEnrichmentResult(
            matched=address is not None,
            provider_person_id=None,
            email=email_obs,
            linkedin=linkedin_obs,
            origin=EnrichmentOrigin.LIVE_PROVIDER,
            raw_digest=digest_of(raw),  # digest only — the raw payload is never persisted
            telemetry=telemetry,
        )

    # -- internals -----------------------------------------------------------

    async def _call_hunter(
        self, params: dict[str, str], *, input_digest: str
    ) -> tuple[dict[str, Any], list[EnrichmentAttemptTelemetry]]:
        """One logical enrichment-provider call — a single, flat transport-
        retry loop (never nested), bounded at `1 + HUNTER_MAX_TRANSPORT_
        RETRIES` attempts. Mirrors `ApolloEnrichmentProvider._call_apollo`
        exactly."""
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
                # Never inferred (§Part 12) — no numeric usage field is
                # populated unless a future session confirms a real,
                # directly-observed one.
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
                    self.runtime.client.get(HUNTER_EMAIL_FINDER_PATH, params=params),
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

        raw_body: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            parsed = response.json()
        except ValueError:
            parse_error = "non-JSON response body"
        else:
            if isinstance(parsed, dict):
                raw_body = parsed
            else:
                parse_error = "non-dict response body"

        error_id = _best_effort_error_id(raw_body)
        base_text = _safe_error_text(response)
        error_text = f"{base_text} (errors[0].id={error_id})" if error_id else base_text

        # Hunter's documented error semantics (§Part 8) — HTTP-status-driven
        # classification only, never body-string guessing. Note 403 -> RATE_
        # LIMITED (bounded retryable) is Hunter-specific and deliberately
        # different from Apollo's 401/403 -> AUTH_ERROR mapping.
        if status_code == 401:
            return EnrichmentAttemptStatus.AUTH_ERROR, raw_body, status_code, request_id, error_text
        if status_code == 403:
            return EnrichmentAttemptStatus.RATE_LIMITED, raw_body, status_code, request_id, error_text
        if status_code in (404, 422, 451):
            return EnrichmentAttemptStatus.INVALID_RESPONSE, raw_body, status_code, request_id, error_text
        if status_code == 429:
            return EnrichmentAttemptStatus.QUOTA_EXHAUSTED, raw_body, status_code, request_id, error_text
        if 500 <= status_code < 600:
            return EnrichmentAttemptStatus.PROVIDER_ERROR, raw_body, status_code, request_id, error_text
        if status_code != 200:
            # Any other non-2xx (other 4xx, 3xx, ...) — permanent, never
            # guessed at.
            return EnrichmentAttemptStatus.INVALID_RESPONSE, raw_body, status_code, request_id, error_text

        if raw_body is None:
            return EnrichmentAttemptStatus.INVALID_RESPONSE, None, status_code, request_id, parse_error

        # The exact HTTP-200 no-email body shape is unverified (§Part 12) —
        # this stays lenient at the envelope level (a missing/null `data`
        # object is treated as a legitimate empty result, not a failure) and
        # fails closed ONLY on a field whose type is unambiguously wrong.
        data = raw_body.get("data")
        if data is not None and not isinstance(data, dict):
            return (
                EnrichmentAttemptStatus.INVALID_RESPONSE, raw_body, status_code, request_id,
                "malformed 'data' field (not an object)",
            )

        data_dict = data if isinstance(data, dict) else {}
        email_value = data_dict.get("email")
        if email_value is not None and not isinstance(email_value, str):
            return (
                EnrichmentAttemptStatus.INVALID_RESPONSE, raw_body, status_code, request_id,
                "malformed non-string 'data.email'",
            )

        return EnrichmentAttemptStatus.OK, raw_body, status_code, request_id, None
