"""`discover_live()` — H2 Stages A-D, the real multi-stage discovery
orchestration `engine/runner.py::discover_and_dedupe()` routes to when the
run's `SearchProvider` sets `requires_llm_discovery = True` (only
`TavilySearchProvider`). Demo Mode's single-shot `call_discover()` path is
completely untouched — this module is never imported on that path's hot
line, only referenced by the branch below.

This is engine-layer code, not provider code, specifically because Stage B
(`DISCOVERY_EXTRACTION`) and Stage C's ambiguous-fallback
(`DOMAIN_SELECTION`) are real LLM calls whose attempt telemetry must be
persisted through the same `llm_calls` repository seam every other LLM
operation uses (`engine/llm.py::call_structured` for prospect-scoped calls;
this module persists directly via `repos.llm_calls.record_attempts()` since
discovery is run-scoped, before any prospect/`ProspectContext` exists —
exactly the same run-scoped pattern `engine/objective_parser.py` established
for the Play-scoped `objective_parse` operation).

Stage order, matching CLAUDE.md's H2 task spec exactly:

  A. `TavilySearchProvider.raw_discover()` — bounded deterministic query
     plan, no LLM, no prospects created yet.
  B. `DISCOVERY_EXTRACTION` LLM call — bounded search-result excerpts (no
     URLs) -> candidate company names + supporting refs. Server-verifies
     every candidate before trusting it (served refs + textual support).
  C. Per-candidate domain resolution — one deterministic query per
     candidate; accept without an LLM call when exactly one served,
     structurally-safe candidate's domain label matches the company name;
     otherwise fall back to the bounded `DOMAIN_SELECTION` LLM call, which
     may still only choose among already structurally-safe candidates.
  D. Identity gate (`domain/discovery.py`) — already enforced inline by C's
     `resolve_candidate_domain()` (served-ref/safety/aggregator) plus this
     module's own uniqueness dedupe.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from groundwork.domain.discovery import company_name_textually_supported, domain_label_matches_company, resolve_candidate_domain
from groundwork.models.llm_io import DiscoveryExtractionOutput, DomainSelectionOutput
from groundwork.models.schemas import CompanySeed, PlaySpec
from groundwork.observability.events import EventEmitter
from groundwork.observability.search_calls import SearchCallRecorder
from groundwork.prompts import discovery_extraction, domain_selection
from groundwork.providers.base import DiscoveryResult, DomainCandidate, LLMOperation, ProviderBundle, ProviderError

if TYPE_CHECKING:
    from groundwork.engine.runner import Repos

@dataclass(frozen=True)
class DiscoveryBounds:
    """Injectable Stage A/C query caps (H2 Phase 3) — mirrors
    `engine/budget.py::PipelineBudget`'s "engine never reads `Settings`
    directly" seam. `api/run_service.py` builds this from `config.py`."""

    max_plan_queries: int = 4
    max_domain_resolution_queries: int = 8


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    base = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return base or f"company-{uuid.uuid4().hex[:8]}"


def _build_company_seed(name: str, domain: str) -> CompanySeed:
    """Only what discovery can actually know: the display name and a
    provider-URL-derived canonical domain. `industry`/`size_band`/
    `employee_count`/`hq_country` are UNKNOWN placeholders, never
    fabricated — scoring's `industry_fit`/`size_fit` read only the
    independently-grounded `IndustryProfileFact`/`EmployeeCountProfileFact`
    research later establishes (H1), never these fields directly."""
    return CompanySeed(
        slug=_slugify(name), name=name, domain=domain,
        industry="unknown", size_band="unknown", employee_count=0,
        hq_country="unknown", description="",
    )


async def _record_llm_attempts(
    repos: "Repos", *, run_id: str, operation: str, provider: str, prompt_version: str, attempts: list
) -> None:
    if not attempts:
        return
    try:
        await repos.llm_calls.record_attempts(
            call_group_id=str(uuid.uuid4()), operation=operation, provider=provider,
            prompt_version=prompt_version, attempts=attempts, run_id=run_id,
        )
    except Exception:  # noqa: BLE001 — observability must never fail discovery
        pass


async def _domain_selection_llm(
    *, run_id: str, company_name: str, candidates: list[DomainCandidate],
    providers: ProviderBundle, repos: "Repos", ctx_key: str,
) -> str | None:
    """Stage C ambiguous fallback. Returns a served candidate `ref`, or
    `None` (a legitimate "couldn't resolve" outcome, never an error) on any
    provider failure, refusal, or a genuine null selection."""
    prompt_input = domain_selection.DomainSelectionInput.from_candidates(company_name, candidates)
    envelope = domain_selection.build_envelope(ctx_key, prompt_input)
    try:
        result = await providers.llm.structured(
            envelope, DomainSelectionOutput, ctx_key=ctx_key, operation=LLMOperation.DOMAIN_SELECTION
        )
    except ProviderError as exc:
        await _record_llm_attempts(
            repos, run_id=run_id, operation=LLMOperation.DOMAIN_SELECTION.value,
            provider=providers.llm.name, prompt_version=domain_selection.PROMPT_VERSION, attempts=exc.attempts,
        )
        return None

    await _record_llm_attempts(
        repos, run_id=run_id, operation=LLMOperation.DOMAIN_SELECTION.value,
        provider=providers.llm.name, prompt_version=domain_selection.PROMPT_VERSION, attempts=result.attempts,
    )
    selected_ref = result.parsed.selected_candidate_ref
    served_refs = {c.ref for c in candidates}
    return selected_ref if selected_ref in served_refs else None


async def discover_live(
    *,
    run_id: str,
    play_spec: PlaySpec,
    providers: ProviderBundle,
    repos: "Repos",
    events: EventEmitter,
    limit: int,
    max_plan_queries: int,
    max_domain_resolution_queries: int,
) -> DiscoveryResult:
    search = providers.search  # TavilySearchProvider — has raw_discover()
    ctx_key = f"{run_id}:discovery"
    search_calls = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)

    # -- Stage A --------------------------------------------------------
    raw = await search.raw_discover(play_spec, ctx_key=ctx_key, max_queries=max_plan_queries)
    await search_calls.record(telemetry=raw.telemetry, documents=raw.documents)

    if not raw.hits:
        # Legitimate zero-result outcome (SearchEmptyResult), not a crash —
        # the run simply discovers nothing this time.
        return DiscoveryResult(companies=[], telemetry=[])

    # -- Stage B ----------------------------------------------------------
    industry_hint = play_spec.target_industries[0] if play_spec.target_industries else ""
    extraction_input = discovery_extraction.DiscoveryExtractionInput.from_hits(raw.hits, industry_hint=industry_hint)
    envelope = discovery_extraction.build_envelope(ctx_key, extraction_input)
    try:
        result = await providers.llm.structured(
            envelope, DiscoveryExtractionOutput, ctx_key=ctx_key, operation=LLMOperation.DISCOVERY_EXTRACTION
        )
    except ProviderError as exc:
        await _record_llm_attempts(
            repos, run_id=run_id, operation=LLMOperation.DISCOVERY_EXTRACTION.value,
            provider=providers.llm.name, prompt_version=discovery_extraction.PROMPT_VERSION, attempts=exc.attempts,
        )
        await events.emit("discovery.candidate_rejected", reason="discovery_extraction_unavailable")
        return DiscoveryResult(companies=[], telemetry=[])

    await _record_llm_attempts(
        repos, run_id=run_id, operation=LLMOperation.DISCOVERY_EXTRACTION.value,
        provider=providers.llm.name, prompt_version=discovery_extraction.PROMPT_VERSION, attempts=result.attempts,
    )

    # -- Server post-filter (still Stage B) --------------------------------
    served_refs = {h.ref for h in raw.hits}
    excerpt_by_ref = {h.ref: h.excerpt for h in raw.hits}
    seen_names: set[str] = set()
    valid_candidates: list[str] = []
    for candidate in result.parsed.candidates:
        cited_refs = [r for r in candidate.supporting_result_refs if r in served_refs]
        if not cited_refs:
            await events.emit("discovery.candidate_rejected", reason="unsupported_refs", company=candidate.company_name)
            continue
        cited_excerpts = [excerpt_by_ref[r] for r in cited_refs]
        if not company_name_textually_supported(candidate.company_name, cited_excerpts):
            await events.emit("discovery.candidate_rejected", reason="name_not_supported", company=candidate.company_name)
            continue
        name_key = candidate.company_name.strip().lower()
        if not name_key or name_key in seen_names:
            continue
        seen_names.add(name_key)
        valid_candidates.append(candidate.company_name.strip())

    # -- Stage C + D --------------------------------------------------------
    company_seeds: list[CompanySeed] = []
    seen_domains: set[str] = set()
    domain_queries_used = 0

    for name in valid_candidates:
        if len(company_seeds) >= limit:
            break
        if domain_queries_used >= max_domain_resolution_queries:
            await events.emit("discovery.candidate_rejected", reason="domain_resolution_budget_exhausted", company=name)
            break
        domain_queries_used += 1

        domain_ctx_key = f"{ctx_key}:domain:{uuid.uuid4().hex[:8]}"
        candidates_result = await search.resolve_domain(name, ctx_key=domain_ctx_key)
        await search_calls.record(telemetry=candidates_result.telemetry, documents=[])

        served_domains = frozenset(d for d in candidates_result.domains if d)
        safe_candidates: list[tuple[DomainCandidate, str]] = []
        for candidate in candidates_result.candidates:
            resolved = resolve_candidate_domain(candidate.url, served_domains)
            if resolved:
                safe_candidates.append((candidate, resolved))

        selected_domain: str | None = None
        method: str | None = None
        label_matched = [(c, d) for c, d in safe_candidates if domain_label_matches_company(d, name)]
        if len(label_matched) == 1:
            selected_domain = label_matched[0][1]
            method = "deterministic"
        elif safe_candidates:
            selected_ref = await _domain_selection_llm(
                run_id=run_id, company_name=name, candidates=[c for c, _ in safe_candidates],
                providers=providers, repos=repos, ctx_key=domain_ctx_key,
            )
            if selected_ref:
                match = next(((c, d) for c, d in safe_candidates if c.ref == selected_ref), None)
                if match:
                    selected_domain, method = match[1], "llm"

        if not selected_domain:
            await events.emit("discovery.candidate_rejected", reason="unresolved_domain", company=name)
            continue
        if selected_domain in seen_domains:
            await events.emit("discovery.candidate_rejected", reason="duplicate_domain", company=name)
            continue
        seen_domains.add(selected_domain)
        await events.emit("discovery.domain_resolved", company=name, method=method)
        company_seeds.append(_build_company_seed(name, selected_domain))

    return DiscoveryResult(companies=company_seeds, telemetry=[])
