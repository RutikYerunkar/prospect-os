"""Launches `execute_run` as a background asyncio task (§17: "`create_task`
isn't durable" — and that's a stated, honest tradeoff, not an oversight).

`POST /plays/{id}/runs` returns 202 immediately; this module is what runs
the engine after the response has already gone out. Tasks are held in a
module-level set purely so they aren't garbage-collected mid-flight (the
standard `asyncio.create_task` footgun) — this is not a `RunRegistry` for
querying status; status is read straight from the `runs` table, same as
every other reader.
"""

from __future__ import annotations

import asyncio

from groundwork.config import settings
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.models.schemas import PlaySpec
from groundwork.observability.events import EventEmitter
from groundwork.providers.registry import build_provider_bundle

_background_tasks: set[asyncio.Task] = set()


async def _run_and_finalize(run_id: str, play_spec: PlaySpec, mode: Mode, seed: int, repos: Repos) -> None:
    events = EventEmitter(run_id=run_id, events=repos.events)
    await events.emit("run.started", seed=seed, mode=mode.value)
    try:
        providers = build_provider_bundle(mode, seed=seed)
        summary = await execute_run(
            run_id=run_id,
            play_spec=play_spec,
            providers=providers,
            repos=repos,
            max_concurrent_prospects=settings.max_concurrent_prospects,
            run_wall_clock_timeout_s=settings.run_wall_clock_timeout_s,
        )
        await events.emit("run.completed", status=summary.status, counters=summary.counters)
    except Exception as exc:  # noqa: BLE001 — a run that blows up before/around
        # per-prospect fan-out (e.g. discovery itself failing) must still land
        # in a terminal DB state, not hang as RUNNING forever. Per-prospect
        # failure isolation inside execute_run is untouched; this is the
        # run-level equivalent of that same honesty.
        await repos.runs.finalize(run_id, status="PARTIAL", counters={}, error=str(exc))
        await events.emit("run.failed", error=str(exc))


def launch_run(run_id: str, play_spec: PlaySpec, mode: Mode, seed: int, repos: Repos) -> None:
    task = asyncio.create_task(_run_and_finalize(run_id, play_spec, mode, seed, repos))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
