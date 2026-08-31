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
from groundwork.engine.context import ProspectContext
from groundwork.engine.pipeline import build_prospect_pipeline
from groundwork.models.enums import ProspectStage, ProspectStatus, ReviewVerdict, RunStatus
from groundwork.models.schemas import CompanySeed, PlaySpec, ProspectOutcome
from groundwork.observability.events import EventEmitter
from groundwork.observability.trace import TraceRecorder
from groundwork.providers.base import ProviderBundle
from groundwork.repositories.events import EventRepository
from groundwork.repositories.prospect_data import ProspectDataRepository
from groundwork.repositories.prospects import CompanyRepository, ProspectRepository
from groundwork.repositories.runs import RunRepository
from groundwork.repositories.tasks import TaskRepository


@dataclass
class Repos:
    runs: RunRepository
    companies: CompanyRepository
    prospects: ProspectRepository
    prospect_data: ProspectDataRepository
    tasks: TaskRepository
    events: EventRepository

    @classmethod
    def build(cls, session_factory) -> "Repos":
        return cls(
            runs=RunRepository(session_factory),
            companies=CompanyRepository(session_factory),
            prospects=ProspectRepository(session_factory),
            prospect_data=ProspectDataRepository(session_factory),
            tasks=TaskRepository(session_factory),
            events=EventRepository(session_factory),
        )


class RunSummary(BaseModel):
    run_id: str
    status: str
    counters: dict[str, int]
    outcomes: list[ProspectOutcome]


def _derive_final_status(ctx: ProspectContext) -> ProspectStatus:
    """Combine the score's hard-disqualifier modifier with the review
    verdict into the single status the board shows."""
    if ctx.score is not None and ctx.score.disqualified:
        return ProspectStatus.REJECTED
    if ctx.review is not None:
        if ctx.review.verdict == ReviewVerdict.FAIL:
            return ProspectStatus.REJECTED
        if ctx.review.verdict == ReviewVerdict.NEEDS_REVIEW:
            return ProspectStatus.NEEDS_REVIEW
    return ProspectStatus.PASS


async def discover_and_dedupe(
    run_id: str, play_spec: PlaySpec, providers: ProviderBundle, repos: Repos
) -> tuple[list[tuple[str, CompanySeed, str, str | None]], set[str]]:
    """Sequential, cheap (§7 diagram). Returns per-prospect seeds plus the
    full set of company identifiers in this run (for the cross-prospect-leak
    guardrail)."""
    company_seeds = await providers.search.discover(play_spec, play_spec.target_count)

    seen_keys: dict[str, str] = {}
    prospect_seeds: list[tuple[str, CompanySeed, str, str | None]] = []
    all_identifiers: set[str] = set()

    for company in company_seeds:
        key = compute_dedupe_key(company.domain, company.name)
        canonical_domain = normalize_domain(company.domain) or normalize_name(company.name)
        company_id = await repos.companies.get_or_create(company, canonical_domain, normalize_name(company.name))

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
) -> RunSummary:
    prospect_seeds, all_identifiers = await discover_and_dedupe(run_id, play_spec, providers, repos)
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
            other_dedupe_keys=frozenset(other_dedupe_keys_by_prospect[prospect_id]),
            other_company_identifiers=frozenset(all_identifiers - own_identifiers),
        )

        async with gate:
            try:
                await build_prospect_pipeline().execute(ctx)
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
