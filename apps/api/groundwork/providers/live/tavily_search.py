"""`TavilySearchProvider` — the real live `SearchProvider` (H2 Phase 4),
behind the same provider-neutral contract `DemoSearchProvider` satisfies.
Uses `tavily-python==0.8.0`'s `AsyncTavilyClient` (verified by reading the
installed package, not remembered field names — see docs/PROGRESS.md for
the full verified-facts record).

CRITICAL BOUNDARY (mirrors `providers/live/openai_llm.py`): this module
never imports a repository, SQLAlchemy, or a DB table model. It only
returns provider-neutral result/telemetry shapes (`RawDiscoveryResult`,
`DomainCandidates`, `SourceBundle`, `SearchAttemptTelemetry`) or raises a
typed `SearchProviderError` carrying whatever telemetry was produced before
the failure — `engine/search.py`/`engine/discovery.py` alone persist it.

`AsyncTavilyClient` has NO SDK-side retry logic in this pinned version
(confirmed by reading `tavily/async_tavily.py`: `search()`/`extract()` each
issue exactly one HTTP POST, no loop) — every retry/backoff/error-
classification decision below belongs to this adapter, not the SDK.

`discover()` (the shared `SearchProvider` Protocol method) is intentionally
NOT implemented as a single-shot call here: H2's multi-stage discovery
(Stage A raw search -> Stage B LLM extraction -> Stage C domain resolution
-> Stage D identity gate) needs an LLM call and its telemetry persisted via
`engine/llm.py`'s repository seam, which this provider must never touch
directly. That orchestration lives in `engine/discovery.py`, which calls
this provider's `raw_discover()`/`resolve_domain()` directly instead of the
shared `discover()` method — see `requires_llm_discovery` below, which
`engine/runner.py::discover_and_dedupe()` checks to route accordingly.

Real per-company source retrieval (Phase 10/11) follows "search -> rank/
select bounded winners -> extract selected URLs only": `fetch_sources()`
runs domain-scoped category searches (`include_domains=[company.domain]`),
locally computes the same deterministic winners `engine/steps/research.py`
would derive anyway (`domain/source_identity.py::select_winners`), and
issues exactly ONE batched Tavily `extract()` call per prospect for those
winners (not one call per URL) — bounded, and countable as a single
`LIVE_MAX_EXTRACT_CALLS_PER_RUN` unit. A failed URL in that batch (`
failed_results`) degrades that one source, never the whole prospect.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timezone
from typing import Any

import httpx
from tavily import BadRequestError, InvalidAPIKeyError, UsageLimitExceededError
from tavily.errors import ForbiddenError
from tavily.errors import TimeoutError as TavilyTimeoutError

from groundwork.domain.psl import canonical_domain
from groundwork.domain.query_plan import build_domain_resolution_query, build_query_plan, build_source_queries
from groundwork.domain.source_identity import compute_content_sha256, select_winners
from groundwork.domain.url_safety import canonicalize_url
from groundwork.models.enums import EvidenceOrigin, SourceStatus
from groundwork.models.schemas import CompanySeed, PlaySpec, SourceDocument
from groundwork.providers.base import (
    DiscoveryResult,
    DomainCandidate,
    DomainCandidates,
    RawDiscoveryResult,
    RawSearchHit,
    SearchAttemptKind,
    SearchAttemptStatus,
    SearchAttemptTelemetry,
    SearchAuthError,
    SearchInvalidResponse,
    SearchOperation,
    SearchProviderError,
    SearchProviderUnavailable,
    SearchRateLimited,
    SearchTimeout,
    SourceBundle,
)
from groundwork.providers.live.search_runtime import LiveSearchRuntime

_TRANSPORT_STATUSES = {
    SearchAttemptStatus.TIMEOUT,
    SearchAttemptStatus.RATE_LIMITED,
    SearchAttemptStatus.PROVIDER_ERROR,
}
_PERMANENT_STATUSES = {SearchAttemptStatus.AUTH_ERROR, SearchAttemptStatus.INVALID_RESPONSE}
_ERROR_CLASS_BY_STATUS: dict[SearchAttemptStatus, type[SearchProviderError]] = {
    SearchAttemptStatus.TIMEOUT: SearchTimeout,
    SearchAttemptStatus.RATE_LIMITED: SearchRateLimited,
    SearchAttemptStatus.PROVIDER_ERROR: SearchProviderUnavailable,
    SearchAttemptStatus.AUTH_ERROR: SearchAuthError,
    SearchAttemptStatus.INVALID_RESPONSE: SearchInvalidResponse,
}


def _backoff_s(retry_index: int) -> float:
    return min(0.5 * (2 ** (retry_index - 1)), 4.0)


def _parse_published_date(raw: Any) -> date | None:
    """Only trusted when it parses cleanly as an ISO date — never guessed,
    never inferred from anything else (H2 Phase 12)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


class TavilySearchProvider:
    name = "tavily"

    # `engine/runner.py::discover_and_dedupe()` checks this to route to
    # `engine/discovery.py::discover_live()` instead of the single-shot
    # `call_discover()`/`SearchProvider.discover()` path Demo Mode uses.
    requires_llm_discovery = True

    def __init__(
        self,
        *,
        runtime: LiveSearchRuntime,
        search_budget: Any = None,
        max_results_per_query: int,
        max_source_queries_per_prospect: int,
        max_result_occurrences_per_prospect: int,
        max_sources_per_prospect: int,
        max_source_excerpt_chars: int,
        max_domain_candidates: int = 5,
    ) -> None:
        self.runtime = runtime
        # Duck-typed (mirrors `OpenAILLMProvider(run_budget=...)`): only
        # `.reserve_search_call()`/`.reserve_extract_call()` are required,
        # so this module never imports `engine/search_budget.py` and stays
        # free of any engine-layer dependency.
        self.search_budget = search_budget
        self.max_results_per_query = max_results_per_query
        self.max_source_queries_per_prospect = max_source_queries_per_prospect
        self.max_result_occurrences_per_prospect = max_result_occurrences_per_prospect
        self.max_sources_per_prospect = max_sources_per_prospect
        self.max_source_excerpt_chars = max_source_excerpt_chars
        self.max_domain_candidates = max_domain_candidates

    # -- SearchProvider Protocol -------------------------------------------

    async def discover(self, spec: PlaySpec, limit: int) -> DiscoveryResult:
        raise NotImplementedError(
            "TavilySearchProvider.discover() is never called directly for Live "
            "Mode — see engine/discovery.py::discover_live(), which drives "
            "raw_discover()/resolve_domain() itself so the Stage B/C LLM calls "
            "and their telemetry persistence can happen outside this provider."
        )

    async def resolve_domain(self, company_name: str, *, ctx_key: str) -> DomainCandidates:
        query = build_domain_resolution_query(company_name)
        telemetry: list[SearchAttemptTelemetry] = []
        if self.search_budget is not None and not await self.search_budget.reserve_search_call():
            telemetry.append(
                self._budget_blocked_telemetry(
                    operation=SearchOperation.RESOLVE_DOMAIN, query_group_id=ctx_key,
                    template_id=query.template_id.value, rendered_query=query.query,
                    query_digest=query.query_digest,
                )
            )
            return DomainCandidates(domains=[], candidates=[], telemetry=telemetry)

        try:
            raw, attempt_telemetry = await self._call_tavily(
                lambda: self.runtime.client.search(
                    query.query, search_depth=self.runtime.search_depth,
                    max_results=self.max_domain_candidates, include_usage=True,
                    timeout=self.runtime.call_deadline_s,
                ),
                operation=SearchOperation.RESOLVE_DOMAIN, query_group_id=ctx_key,
                template_id=query.template_id.value, rendered_query=query.query,
                query_digest=query.query_digest,
            )
        except SearchProviderError as exc:
            return DomainCandidates(domains=[], candidates=[], telemetry=list(exc.telemetry))

        telemetry.extend(attempt_telemetry)
        results = (raw or {}).get("results", [])[: self.max_domain_candidates]
        candidates: list[DomainCandidate] = []
        for r in results:
            url = r.get("url") if isinstance(r, dict) else None
            if not url:
                continue
            candidates.append(
                DomainCandidate(ref=f"dom:{uuid.uuid4().hex[:12]}", url=url, title=(r.get("title") or ""))
            )
        domains = [d for d in (canonical_domain(c.url) for c in candidates) if d]
        return DomainCandidates(domains=domains, candidates=candidates, telemetry=telemetry)

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> SourceBundle:
        queries = build_source_queries(company.name, max_queries=self.max_source_queries_per_prospect)
        occurrences: list[SourceDocument] = []
        telemetry: list[SearchAttemptTelemetry] = []

        for query in queries:
            if len(occurrences) >= self.max_result_occurrences_per_prospect:
                break
            if self.search_budget is not None and not await self.search_budget.reserve_search_call():
                telemetry.append(
                    self._budget_blocked_telemetry(
                        operation=SearchOperation.DOMAIN_SEARCH, query_group_id=ctx_key,
                        template_id=query.template_id.value, rendered_query=query.query,
                        query_digest=query.query_digest,
                    )
                )
                continue
            try:
                raw, attempt_telemetry = await self._call_tavily(
                    lambda query=query: self.runtime.client.search(
                        query.query, search_depth=self.runtime.search_depth,
                        max_results=self.max_results_per_query, include_domains=[company.domain],
                        include_usage=True, timeout=self.runtime.call_deadline_s,
                    ),
                    operation=SearchOperation.DOMAIN_SEARCH, query_group_id=ctx_key,
                    template_id=query.template_id.value, rendered_query=query.query,
                    query_digest=query.query_digest,
                )
            except SearchProviderError as exc:
                telemetry.extend(exc.telemetry)
                continue
            telemetry.extend(attempt_telemetry)

            results = (raw or {}).get("results", [])
            retrieved_at = datetime.now(timezone.utc)
            remaining = max(self.max_result_occurrences_per_prospect - len(occurrences), 0)
            for i, r in enumerate(results[:remaining]):
                occurrences.append(
                    self._to_source_document(
                        r, ref=f"src:{uuid.uuid4().hex[:12]}", rank=i, retrieved_at=retrieved_at,
                        extraction_method="tavily_search",
                    )
                )

        # Winners are the same object references held in `occurrences` —
        # mutating extraction results onto them below updates `occurrences`
        # in place; `engine/steps/research.py` re-derives this identical
        # winner set deterministically, so no separate merge is needed.
        winners = select_winners(occurrences)[: self.max_sources_per_prospect]
        winner_urls = [w.url for w in winners if w.url]
        if winner_urls:
            can_extract = self.search_budget is None or await self.search_budget.reserve_extract_call()
            if not can_extract:
                telemetry.append(
                    self._budget_blocked_telemetry(
                        operation=SearchOperation.EXTRACT, query_group_id=ctx_key,
                        template_id=None, rendered_query=None, query_digest=None,
                    )
                )
            else:
                try:
                    extract_raw, extract_telemetry = await self._call_tavily(
                        lambda: self.runtime.client.extract(
                            winner_urls, extract_depth="basic", include_usage=True,
                            timeout=self.runtime.call_deadline_s,
                        ),
                        operation=SearchOperation.EXTRACT, query_group_id=ctx_key,
                        template_id=None, rendered_query=None, query_digest=None,
                    )
                except SearchProviderError as exc:
                    telemetry.extend(exc.telemetry)
                else:
                    telemetry.extend(extract_telemetry)
                    self._apply_extraction(winners, extract_raw)

        return SourceBundle(documents=occurrences, telemetry=telemetry)

    # -- H2 discovery-only extension (not part of the shared Protocol) -----

    async def raw_discover(
        self, play_spec: PlaySpec, *, ctx_key: str, max_queries: int, max_results_per_query: int | None = None
    ) -> RawDiscoveryResult:
        """Stage A: bounded discovery queries from the deterministic query
        plan. Never produces a `CompanySeed` — only raw hits (for the Stage
        B LLM call) and retrieval-occurrence documents (for persistence)."""
        results_cap = max_results_per_query or self.max_results_per_query
        queries = build_query_plan(play_spec, max_queries=max_queries)
        hits: list[RawSearchHit] = []
        documents: list[SourceDocument] = []
        telemetry: list[SearchAttemptTelemetry] = []

        for query in queries:
            if self.search_budget is not None and not await self.search_budget.reserve_search_call():
                telemetry.append(
                    self._budget_blocked_telemetry(
                        operation=SearchOperation.DISCOVER, query_group_id=ctx_key,
                        template_id=query.template_id.value, rendered_query=query.query,
                        query_digest=query.query_digest,
                    )
                )
                continue
            try:
                raw, attempt_telemetry = await self._call_tavily(
                    lambda query=query: self.runtime.client.search(
                        query.query, search_depth=self.runtime.search_depth,
                        max_results=results_cap, include_usage=True, timeout=self.runtime.call_deadline_s,
                    ),
                    operation=SearchOperation.DISCOVER, query_group_id=ctx_key,
                    template_id=query.template_id.value, rendered_query=query.query,
                    query_digest=query.query_digest,
                )
            except SearchProviderError as exc:
                # A single discovery query timing out/failing degrades the
                # candidate pool, not the whole run (Phase 21) — keep going.
                telemetry.extend(exc.telemetry)
                continue
            telemetry.extend(attempt_telemetry)

            results = (raw or {}).get("results", [])[:results_cap]
            retrieved_at = datetime.now(timezone.utc)
            for i, r in enumerate(results):
                ref = f"disc:{uuid.uuid4().hex[:12]}"
                doc = self._to_source_document(
                    r, ref=ref, rank=i, retrieved_at=retrieved_at, extraction_method="tavily_search"
                )
                documents.append(doc)
                hits.append(RawSearchHit(ref=ref, title=doc.title, excerpt=doc.text, url=doc.url))

        return RawDiscoveryResult(hits=hits, documents=documents, telemetry=telemetry)

    # -- internals -----------------------------------------------------------

    def _to_source_document(
        self, result: dict[str, Any], *, ref: str, rank: int, retrieved_at: datetime, extraction_method: str
    ) -> SourceDocument:
        url = result.get("url") if isinstance(result, dict) else None
        canonical = canonicalize_url(url) if url else None
        domain = canonical_domain(url) if url else None
        content = (result.get("raw_content") or result.get("content") or "") if isinstance(result, dict) else ""
        bounded = content[: self.max_source_excerpt_chars]
        score = result.get("score") if isinstance(result, dict) else None
        confidence = min(max(float(score), 0.0), 1.0) if isinstance(score, (int, float)) else 0.5
        return SourceDocument(
            ref=ref,
            title=(result.get("title") or "") if isinstance(result, dict) else "",
            claim="",
            text=bounded,
            source_provider=self.name,
            signal_type=None,
            confidence=confidence,
            url=url,
            canonical_url=canonical,
            domain=domain,
            publisher=domain,
            full_text_length=len(content) if content else None,
            content_sha256=compute_content_sha256(content) if content else None,
            source_type="live_web",
            retrieved_at=retrieved_at,
            # H2 Phase 12: no reliably-populated published_date field was
            # observed on ordinary Tavily search results in the verification
            # spike — never inferred, only trusted if actually present.
            published_at=_parse_published_date(result.get("published_date")) if isinstance(result, dict) else None,
            provider_result_id=(result.get("id") if isinstance(result, dict) else None),
            rank=rank,
            relevance_score=score if isinstance(score, (int, float)) else None,
            extraction_method=extraction_method,
            status=SourceStatus.OK,
            origin=EvidenceOrigin.LIVE_FETCH,
        )

    def _apply_extraction(self, winners: list[SourceDocument], extract_raw: dict[str, Any] | None) -> None:
        if not extract_raw:
            return
        results_by_url = {
            r.get("url"): r for r in extract_raw.get("results", []) if isinstance(r, dict) and r.get("url")
        }
        failed_urls = {
            f.get("url") for f in extract_raw.get("failed_results", []) if isinstance(f, dict) and f.get("url")
        }
        for winner in winners:
            if not winner.url:
                continue
            result = results_by_url.get(winner.url)
            if result is not None:
                content = result.get("raw_content") or ""
                bounded = content[: self.max_source_excerpt_chars]
                if bounded:
                    winner.text = bounded
                if content:
                    winner.full_text_length = len(content)
                    winner.content_sha256 = compute_content_sha256(content)
                winner.extraction_method = "tavily_extract"
                winner.status = SourceStatus.OK
            elif winner.url in failed_urls:
                # Per-source degradation (Phase 4/21): keep the search
                # snippet already on `.text`, mark PARTIAL rather than
                # dropping the source — enough others may still extract.
                winner.status = SourceStatus.PARTIAL

    def _budget_blocked_telemetry(
        self, *, operation: SearchOperation, query_group_id: str, template_id: str | None,
        rendered_query: str | None, query_digest: str | None,
    ) -> SearchAttemptTelemetry:
        now = datetime.now(timezone.utc)
        return SearchAttemptTelemetry(
            provider=self.name, operation=operation, query_group_id=query_group_id,
            template_id=template_id, rendered_query=rendered_query, query_digest=query_digest,
            call_group_id=str(uuid.uuid4()), status=SearchAttemptStatus.NOT_ATTEMPTED_BUDGET,
            started_at=now, finished_at=now, latency_ms=0.0,
        )

    async def _call_tavily(
        self,
        issue: Callable[[], Awaitable[dict[str, Any]]],
        *,
        operation: SearchOperation,
        query_group_id: str,
        template_id: str | None,
        rendered_query: str | None,
        query_digest: str | None,
    ) -> tuple[dict[str, Any] | None, list[SearchAttemptTelemetry]]:
        """One logical search-provider call — a single, flat transport-retry
        loop (never nested), bounded at `1 + SEARCH_MAX_TRANSPORT_RETRIES`
        attempts. Every attempt (success or failure) is appended to the
        returned telemetry list; on exhaustion this raises the matching
        typed `SearchProviderError` carrying every attempt made so far, so
        the caller never loses a failed call from `search_calls`."""
        call_group_id = str(uuid.uuid4())
        attempts: list[SearchAttemptTelemetry] = []
        transport_retry_index = 0
        flat_attempt = 0

        while True:
            flat_attempt += 1
            kind = SearchAttemptKind.INITIAL if transport_retry_index == 0 else SearchAttemptKind.TRANSPORT_RETRY
            if transport_retry_index > 0:
                await asyncio.sleep(_backoff_s(transport_retry_index))

            started = datetime.now(timezone.utc)
            status, raw, http_status, request_id, error_text, result_count, chars = await self._issue(issue)
            finished = datetime.now(timezone.utc)

            # EXTRACT-specific truthful telemetry: a 200 OK batch extract
            # response can still carry `failed_results` for some URLs
            # (Phase 4/5) — that's a real, non-fatal degradation, distinct
            # from a clean OK. `_apply_extraction()` handles the actual
            # per-source fallback regardless of this label.
            if (
                operation == SearchOperation.EXTRACT
                and status == SearchAttemptStatus.OK
                and isinstance(raw, dict)
                and raw.get("failed_results")
            ):
                status = SearchAttemptStatus.PARTIAL_EXTRACTION

            credits_used = None
            usage = raw.get("usage") if isinstance(raw, dict) else None
            if isinstance(usage, dict):
                credits_used = usage.get("credits")
            credits_used = credits_used if isinstance(credits_used, (int, float)) else None
            cost = self.runtime.estimate_cost_usd(credits_used)

            attempt_telemetry = SearchAttemptTelemetry(
                provider=self.name, operation=operation, query_group_id=query_group_id,
                template_id=template_id, rendered_query=rendered_query, query_digest=query_digest,
                call_group_id=call_group_id, attempt=flat_attempt, attempt_kind=kind, status=status,
                started_at=started, finished_at=finished,
                latency_ms=(finished - started).total_seconds() * 1000,
                result_count=result_count, selected_count=result_count, provider_request_id=request_id,
                http_status=http_status, error_type=(status.value if status != SearchAttemptStatus.OK else None),
                error_message=error_text, cost_usd=cost, chars_retrieved=chars, credits_used=credits_used,
            )
            attempts.append(attempt_telemetry)

            if status in (
                SearchAttemptStatus.OK,
                SearchAttemptStatus.EMPTY_RESULT,
                SearchAttemptStatus.PARTIAL_EXTRACTION,
            ):
                return raw, attempts

            if status in _PERMANENT_STATUSES:
                raise _ERROR_CLASS_BY_STATUS[status](
                    f"{status.value}: {error_text or 'permanent search provider failure'}", telemetry=attempts
                )

            # Transport-class failure (TIMEOUT/RATE_LIMITED/PROVIDER_ERROR).
            # tavily-python 0.8.0 does not expose a structured Retry-After
            # for authenticated 429s (only its keyless-mode envelope does,
            # which never applies here) — bounded exponential backoff is
            # the honest fallback; see docs/PROGRESS.md.
            if transport_retry_index < self.runtime.max_transport_retries:
                transport_retry_index += 1
                continue
            raise _ERROR_CLASS_BY_STATUS.get(status, SearchProviderUnavailable)(
                f"transport retries exhausted: {status.value}: {error_text or ''}", telemetry=attempts
            )

    async def _issue(
        self, issue: Callable[[], Awaitable[dict[str, Any]]]
    ) -> tuple[SearchAttemptStatus, dict[str, Any] | None, int | None, str | None, str | None, int, int]:
        async with self.runtime.semaphore:
            try:
                raw = await asyncio.wait_for(issue(), timeout=self.runtime.call_deadline_s)
            except (asyncio.TimeoutError, TavilyTimeoutError) as exc:
                return SearchAttemptStatus.TIMEOUT, None, None, None, str(exc) or "timeout", 0, 0
            except InvalidAPIKeyError as exc:
                return SearchAttemptStatus.AUTH_ERROR, None, 401, None, str(exc), 0, 0
            except ForbiddenError as exc:
                return SearchAttemptStatus.AUTH_ERROR, None, 403, None, str(exc), 0, 0
            except BadRequestError as exc:
                return SearchAttemptStatus.INVALID_RESPONSE, None, 400, None, str(exc), 0, 0
            except UsageLimitExceededError as exc:
                return SearchAttemptStatus.RATE_LIMITED, None, 429, None, str(exc), 0, 0
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                return SearchAttemptStatus.PROVIDER_ERROR, None, status_code, None, str(exc), 0, 0
            except httpx.HTTPError as exc:
                return SearchAttemptStatus.PROVIDER_ERROR, None, None, None, str(exc), 0, 0

        if not isinstance(raw, dict):
            return SearchAttemptStatus.INVALID_RESPONSE, None, 200, None, "non-dict response body", 0, 0

        request_id = raw.get("request_id")
        results = raw.get("results")
        if results is None:
            return SearchAttemptStatus.INVALID_RESPONSE, raw, 200, request_id, "response missing results", 0, 0

        chars = sum(
            len(r.get("content") or "") + len(r.get("raw_content") or "")
            for r in results
            if isinstance(r, dict)
        )
        if not results:
            return SearchAttemptStatus.EMPTY_RESULT, raw, 200, request_id, None, 0, chars
        return SearchAttemptStatus.OK, raw, 200, request_id, None, len(results), chars
