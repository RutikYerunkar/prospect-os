"""RunExecutor — fan-out, semaphores, failure isolation.

After discovery + dedupe produces N prospects, this fans out one coroutine
per prospect, each running its own `Pipeline` against its own
`ProspectContext`, bounded by a global semaphore. `asyncio.gather(...,
return_exceptions=True)` — not `asyncio.TaskGroup` — is the load-bearing
detail: one prospect raising must never cancel the others (§10).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from groundwork.domain.dedupe import dedupe_key as compute_dedupe_key
from groundwork.domain.dedupe import find_duplicate, normalize_domain, normalize_name
from groundwork.engine.budget import DEMO_BUDGET, PipelineBudget
from groundwork.engine.context import ProspectContext
from groundwork.engine.discovery import DiscoveryBounds, discover_live
from groundwork.engine.pipeline import build_prospect_pipeline
from groundwork.engine.search import call_discover
from groundwork.models.enums import ExclusionEvaluation, ProspectStage, ProspectStatus, ReviewVerdict, RunStatus
from groundwork.models.schemas import CompanySeed, PlaySpec, ProspectOutcome
from groundwork.observability.events import EventEmitter
from groundwork.observability.llm_calls import LLMCallRecorder
from groundwork.observability.search_calls import SearchCallRecorder
from groundwork.observability.trace import TraceRecorder
from groundwork.providers.base import ProviderBundle
from groundwork.repositories.events import EventRepository
from groundwork.repositories.llm_calls import LLMCallRepository
from groundwork.repositories.prospect_data import ProspectDataRepository
from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
from groundwork.repositories.runs import RunRepository
from groundwork.repositories.search import SearchRepository
from groundwork.repositories.tasks import TaskRepository


@dataclass
class Repos:
    runs: RunRepository
    companies: CompanyRepository
    prospects: ProspectRepository
    prospect_data: ProspectDataRepository
    tasks: TaskRepository
    events: EventRepository
    llm_calls: LLMCallRepository
    search: SearchRepository

    @classmethod
    def build(cls, session_factory) -> "Repos":
        return cls(
            runs=RunRepository(session_factory),
            companies=CompanyRepository(session_factory),
            prospects=ProspectRepository(session_factory),
            prospect_data=ProspectDataRepository(session_factory),
            tasks=TaskRepository(session_factory),
            events=EventRepository(session_factory),
            llm_calls=LLMCallRepository(session_factory),
            search=SearchRepository(session_factory),
        )


class RunSummary(BaseModel):
    run_id: str
    status: str
    counters: dict[str, int]
    outcomes: list[ProspectOutcome]


def _derive_final_status(ctx: ProspectContext) -> ProspectStatus:
    """Combine the score's hard-disqualifier modifier with the review
    verdict into the single status the board shows.

    H1 Phase 7 — tri-state exclusion: an UNKNOWN exclusion evaluation (the
    industry was never independently grounded) must never silently pass.
    It doesn't outrank an existing REJECTED/NEEDS_REVIEW (a review FAIL or
    a hard disqualifier is already the worse outcome), but a status that
    would otherwise be PASS is downgraded to NEEDS_REVIEW — this is
    deliberately NOT an eighth review guardrail; the seven deterministic
    checks stay exactly seven.
    """
    if ctx.score is not None and ctx.score.disqualified:
        return ProspectStatus.REJECTED
    if ctx.review is not None:
        if ctx.review.verdict == ReviewVerdict.FAIL:
            return ProspectStatus.REJECTED
        if ctx.review.verdict == ReviewVerdict.NEEDS_REVIEW:
            return ProspectStatus.NEEDS_REVIEW
    if ctx.score is not None and ctx.score.exclusion_status == ExclusionEvaluation.UNKNOWN:
        return ProspectStatus.NEEDS_REVIEW
    return ProspectStatus.PASS


async def discover_and_dedupe(
    run_id: str,
    play_spec: PlaySpec,
    providers: ProviderBundle,
    repos: Repos,
    *,
    events: EventEmitter | None = None,
    discovery_bounds: DiscoveryBounds = DiscoveryBounds(),
) -> tuple[list[tuple[str, CompanySeed, str, str | None]], set[str]]:
    """Sequential, cheap (§7 diagram). Returns per-prospect seeds plus the
    full set of company identifiers in this run (for the cross-prospect-leak
    guardrail).

    Demo Mode (and any future single-shot provider): discovery telemetry is
    persisted through the same `engine/search.py` seam `fetch_sources()`
    already uses — never a duplicated persistence path here (H1 Phase 1
    deviation closure). `prospect_id=None` on the recorder is correct, not a
    placeholder: `discover()` runs once, before any prospect exists to
    attribute it to.

    Live Mode (H2): a provider whose `requires_llm_discovery` is truthy
    (only `TavilySearchProvider`) is routed instead to
    `engine/discovery.py::discover_live()` — real multi-stage discovery
    (search -> LLM extraction -> domain resolution -> identity gate). This
    is the ONLY branch point between the two paths; everything below (dedupe,
    `CompanyRepository`/`ProspectRepository` writes) is shared byte-for-byte.
    """
    if getattr(providers.search, "requires_llm_discovery", False):
        discovery = await discover_live(
            run_id=run_id, play_spec=play_spec, providers=providers, repos=repos,
            events=events or EventEmitter(run_id=run_id, events=repos.events),
            limit=play_spec.target_count,
            max_plan_queries=discovery_bounds.max_plan_queries,
            max_domain_resolution_queries=discovery_bounds.max_domain_resolution_queries,
        )
    else:
        discovery_search_calls = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)
        discovery = await call_discover(
            providers=providers, play_spec=play_spec, limit=play_spec.target_count,
            search_calls=discovery_search_calls,
        )
    company_seeds = discovery.companies
    company_origin = "live_fetch" if getattr(providers.search, "requires_llm_discovery", False) else "demo_fixture"

    seen_keys: dict[str, str] = {}
    prospect_seeds: list[tuple[str, CompanySeed, str, str | None]] = []
    all_identifiers: set[str] = set()

    for company in company_seeds:
        key = compute_dedupe_key(company.domain, company.name)
        canonical_domain = normalize_domain(company.domain) or normalize_name(company.name)
        company_id = await repos.companies.get_or_create(
            company, canonical_domain, normalize_name(company.name), origin=company_origin
        )

        duplicate_of = find_duplicate(key, seen_keys)
        status = ProspectStatus.DUPLICATE if duplicate_of else ProspectStatus.RUNNING
        prospect_id = await repos.prospects.create(
            run_id=run_id, company_id=company_id, dedupe_key=key, duplicate_of=duplicate_of, status=status.value
        )
        if not duplicate_of:
            seen_keys[key] = prospect_id

        prospect_seeds.append((prospect_id, company, key, duplicate_of))
        all_identifiers.add(company.name.lower())
        all_identifiers.add(normalize_domain(company.domain))

    return prospect_seeds, all_identifiers


async def execute_run(
    *,
    run_id: str,
    play_spec: PlaySpec,
    providers: ProviderBundle,
    repos: Repos,
    max_concurrent_prospects: int,
    run_wall_clock_timeout_s: float,
    budget: PipelineBudget = DEMO_BUDGET,
    discovery_bounds: DiscoveryBounds = DiscoveryBounds(),
) -> RunSummary:
    prospect_seeds, all_identifiers = await discover_and_dedupe(
        run_id, play_spec, providers, repos, discovery_bounds=discovery_bounds
    )
    other_dedupe_keys_by_prospect = {
        pid: {k for pid2, _, k, dup in prospect_seeds if pid2 != pid and dup is None}
        for pid, _, _, _ in prospect_seeds
    }
    reference_date = date.today()
    gate = asyncio.Semaphore(max_concurrent_prospects)

    async def one(prospect_id: str, company: CompanySeed, key: str, duplicate_of: str | None) -> ProspectOutcome:
        events = EventEmitter(run_id=run_id, events=repos.events)
        await events.emit("prospect.discovered", prospect_id=prospect_id, company=company.name)

        if duplicate_of:
            await repos.prospects.finalize(prospect_id, status=ProspectStatus.DUPLICATE.value)
            await events.emit("prospect.completed", prospect_id=prospect_id, status=ProspectStatus.DUPLICATE.value)
            return ProspectOutcome(
                prospect_id=prospect_id, company=company, status=ProspectStatus.DUPLICATE,
                stage=ProspectStage.DONE, duplicate_of=duplicate_of, dedupe_key=key,
            )

        own_identifiers = {company.name.lower(), normalize_domain(company.domain)}
        ctx = ProspectContext(
            run_id=run_id,
            prospect_id=prospect_id,
            company=company,
            dedupe_key=key,
            play_spec=play_spec,
            providers=providers,
            reference_date=reference_date,
            trace=TraceRecorder(run_id=run_id, prospect_id=prospect_id, tasks=repos.tasks),
            events=events,
            llm_calls=LLMCallRecorder(
                run_id=run_id, prospect_id=prospect_id, provider=providers.llm.name, repo=repos.llm_calls
            ),
            search_calls=SearchCallRecorder(run_id=run_id, prospect_id=prospect_id, repo=repos.search),
            other_dedupe_keys=frozenset(other_dedupe_keys_by_prospect[prospect_id]),
            other_company_identifiers=frozenset(all_identifiers - own_identifiers),
        )

        async with gate:
            try:
                await build_prospect_pipeline(budget).execute(ctx)
            except Exception as exc:  # noqa: BLE001 — isolate this prospect's failure only
                await repos.prospects.finalize(prospect_id, status=ProspectStatus.FAILED.value, error=str(exc))
                await events.emit("prospect.completed", prospect_id=prospect_id, status=ProspectStatus.FAILED.value, error=str(exc))
                return ProspectOutcome(
                    prospect_id=prospect_id, company=company, status=ProspectStatus.FAILED,
                    stage=ctx.stage, dedupe_key=key, evidence_count=len(ctx.evidence), error=str(exc),
                )

        final_status = _derive_final_status(ctx)
        await repos.prospect_data.insert_evidence(ctx.evidence)
        await repos.prospect_data.insert_signals(ctx.signals)
        if ctx.score is not None:
            await repos.prospect_data.upsert_score(ctx.score)
        if ctx.contact is not None:
            await repos.prospect_data.upsert_contact(ctx.contact)
        await repos.prospect_data.insert_drafts(ctx.drafts)
        if ctx.review is not None:
            await repos.prospect_data.insert_review_result(ctx.review)
        await repos.prospects.finalize(prospect_id, status=final_status.value)
        await events.emit("prospect.completed", prospect_id=prospect_id, status=final_status.value)

        return ProspectOutcome(
            prospect_id=prospect_id, company=company, status=final_status, stage=ProspectStage.DONE,
            dedupe_key=key, score=ctx.score, contact=ctx.contact, drafts=ctx.drafts, review=ctx.review,
            evidence_count=len(ctx.evidence),
        )

    tasks = [asyncio.ensure_future(one(pid, company, key, dup)) for pid, company, key, dup in prospect_seeds]

    outcomes: list[ProspectOutcome] = []
    try:
        gathered = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=run_wall_clock_timeout_s
        )
        for (prospect_id, company, key, _dup), result in zip(prospect_seeds, gathered, strict=True):
            if isinstance(result, BaseException):
                await repos.prospects.finalize(prospect_id, status=ProspectStatus.FAILED.value, error=str(result))
                outcomes.append(
                    ProspectOutcome(
                        prospect_id=prospect_id, company=company, status=ProspectStatus.FAILED,
                        stage=ProspectStage.DONE, dedupe_key=key, error=str(result),
                    )
                )
            else:
                outcomes.append(result)
    except TimeoutError:
        for (prospect_id, company, key, _dup), task in zip(prospect_seeds, tasks, strict=True):
            if task.done() and not task.cancelled() and task.exception() is None:
                outcomes.append(task.result())
                continue
            task.cancel()
            await repos.prospects.finalize(prospect_id, status=ProspectStatus.TIMED_OUT.value, error="run wall-clock exceeded")
            outcomes.append(
                ProspectOutcome(
                    prospect_id=prospect_id, company=company, status=ProspectStatus.TIMED_OUT,
                    stage=ProspectStage.DONE, dedupe_key=key, error="run wall-clock exceeded",
                )
            )

    counters: dict[str, int] = {}
    for outcome in outcomes:
        counters[outcome.status.value] = counters.get(outcome.status.value, 0) + 1

    run_status = RunStatus.COMPLETED
    if any(o.status in (ProspectStatus.FAILED, ProspectStatus.TIMED_OUT) for o in outcomes):
        run_status = RunStatus.PARTIAL

    await repos.runs.finalize(run_id, status=run_status.value, counters=counters)

    return RunSummary(run_id=run_id, status=run_status.value, counters=counters, outcomes=outcomes)
