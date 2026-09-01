"""Launches `execute_run` as a background asyncio task (§17: "`create_task`
isn't durable" — and that's a stated, honest tradeoff, not an oversight).

`POST /plays/{id}/runs` returns 202 immediately; this module is what runs
the engine after the response has already gone out. Tasks are held in a
module-level set purely so they aren't garbage-collected mid-flight (the
standard `asyncio.create_task` footgun) — this is not a `RunRegistry` for
querying status; status is read straight from the `runs` table, same as
every other reader.

Checkpoint G: Live Mode threads through a process-scoped `LiveProviderRuntime`
(never constructed here — see `main.py`'s lifespan), a fresh per-run
`RunBudget`, and `LIVE_BUDGET`/`DEMO_BUDGET` (`engine/budget.py`) instead of
Demo Mode's hardcoded step timeouts/retries.
"""

from __future__ import annotations

import asyncio

from groundwork.config import settings
from groundwork.engine.budget import DEMO_BUDGET, PipelineBudget
from groundwork.engine.discovery import DiscoveryBounds
from groundwork.engine.run_budget import RunBudget
from groundwork.engine.runner import Repos, execute_run
from groundwork.engine.search_budget import SearchCallBudget
from groundwork.models.enums import Mode
from groundwork.models.schemas import PlaySpec
from groundwork.observability.events import EventEmitter
from groundwork.providers.registry import build_provider_bundle

_background_tasks: set[asyncio.Task] = set()


def live_budget_from_settings() -> PipelineBudget:
    return PipelineBudget(
        default_step_timeout_s=settings.live_step_timeout_s,
        research_timeout_s=settings.live_step_timeout_s,
        research_max_retries=2,
        personalize_timeout_s=settings.live_step_timeout_s,
        personalize_max_retries=1,
        backoffs_s=(0.4, 0.8, 1.6),
        max_concurrent_prospects=settings.max_concurrent_prospects,
        run_wall_clock_timeout_s=settings.live_run_wall_clock_timeout_s,
    )


def live_discovery_bounds_from_settings() -> DiscoveryBounds:
    return DiscoveryBounds(
        max_plan_queries=settings.live_max_plan_queries_per_run,
        max_domain_resolution_queries=settings.live_max_domain_resolution_queries_per_run,
    )


def live_search_bounds_from_settings() -> dict:
    return {
        "max_results_per_query": settings.live_max_search_results_per_query,
        "max_source_queries_per_prospect": settings.live_max_source_queries_per_prospect,
        "max_result_occurrences_per_prospect": settings.live_max_result_occurrences_per_prospect,
        "max_sources_per_prospect": settings.live_max_sources_per_prospect,
        "max_source_excerpt_chars": settings.live_max_source_excerpt_chars,
    }


async def _run_and_finalize(
    run_id: str,
    play_spec: PlaySpec,
    mode: Mode,
    seed: int,
    repos: Repos,
    *,
    live_runtime,
    run_budget,
    search_runtime=None,
) -> None:
    events = EventEmitter(run_id=run_id, events=repos.events)
    await events.emit("run.started", seed=seed, mode=mode.value)
    try:
        search_budget = (
            SearchCallBudget(
                max_search_calls=settings.live_max_search_calls_per_run,
                max_extract_calls=settings.live_max_extract_calls_per_run,
            )
            if mode is Mode.LIVE
            else None
        )
        providers = build_provider_bundle(
            mode, seed=seed, live_runtime=live_runtime, run_budget=run_budget,
            search_runtime=search_runtime, search_budget=search_budget,
            search_bounds=live_search_bounds_from_settings() if mode is Mode.LIVE else None,
        )
        budget = DEMO_BUDGET if mode is Mode.DEMO else live_budget_from_settings()
        discovery_bounds = DiscoveryBounds() if mode is Mode.DEMO else live_discovery_bounds_from_settings()
        summary = await execute_run(
            run_id=run_id,
            play_spec=play_spec,
            providers=providers,
            repos=repos,
            max_concurrent_prospects=budget.max_concurrent_prospects,
            run_wall_clock_timeout_s=budget.run_wall_clock_timeout_s,
            budget=budget,
            discovery_bounds=discovery_bounds,
        )
        await events.emit("run.completed", status=summary.status, counters=summary.counters)
    except Exception as exc:  # noqa: BLE001 — a run that blows up before/around
        # per-prospect fan-out (e.g. discovery itself failing) must still land
        # in a terminal DB state, not hang as RUNNING forever. Per-prospect
        # failure isolation inside execute_run is untouched; this is the
        # run-level equivalent of that same honesty.
        await repos.runs.finalize(run_id, status="PARTIAL", counters={}, error=str(exc))
        await events.emit("run.failed", error=str(exc))


def launch_run(
    run_id: str,
    play_spec: PlaySpec,
    mode: Mode,
    seed: int,
    repos: Repos,
    *,
    live_runtime=None,
    run_budget=None,
    search_runtime=None,
) -> None:
    task = asyncio.create_task(
        _run_and_finalize(
            run_id, play_spec, mode, seed, repos,
            live_runtime=live_runtime, run_budget=run_budget, search_runtime=search_runtime,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
