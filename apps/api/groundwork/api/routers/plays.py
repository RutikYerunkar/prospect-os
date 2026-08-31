from __future__ import annotations

import uuid

from pydantic import ValidationError

from fastapi import APIRouter

from groundwork.api.deps import LiveRuntimeDep, PlaysRepoDep, ReposDep
from groundwork.api.errors import NotFoundError, UnprocessableEntityError
from groundwork.api.run_service import launch_run
from groundwork.api.schemas import (
    PlayCreateRequest,
    PlayResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunSummary,
)
from groundwork.config import settings
from groundwork.engine.objective_parser import parse_objective
from groundwork.engine.run_budget import RunBudget
from groundwork.models.enums import Mode
from groundwork.models.schemas import PlaySpec
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from groundwork.providers.profile import build_provider_profile

router = APIRouter(prefix="/api/plays", tags=["plays"])


def _play_name(objective: str) -> str:
    objective = " ".join(objective.split())
    return objective if len(objective) <= 80 else objective[:77] + "..."


def _require_live_runtime(live_runtime):
    if live_runtime is None:
        raise UnprocessableEntityError(
            "Live Mode requires OPENAI_API_KEY to be configured and the API process restarted — "
            "Demo Mode never falls back silently"
        )
    return live_runtime


async def _to_response(play_row, repos: ReposDep) -> PlayResponse:
    runs = await repos.runs.for_play(play_row.id)
    return PlayResponse(
        id=play_row.id,
        name=play_row.name,
        objective_text=play_row.objective_text,
        icp_spec=play_row.icp_spec,
        mode=play_row.mode,
        created_at=play_row.created_at,
        parse_source=play_row.icp_spec.get("_parse_source", "deterministic") if isinstance(play_row.icp_spec, dict) else "deterministic",
        runs=[
            RunSummary(
                id=r.id,
                status=r.status,
                mode=r.mode,
                seed=r.seed,
                started_at=r.started_at,
                finished_at=r.finished_at,
                counters=r.counters,
            )
            for r in runs
        ],
    )


@router.post("", response_model=PlayResponse, status_code=201)
async def create_play(
    body: PlayCreateRequest, plays: PlaysRepoDep, repos: ReposDep, live_runtime: LiveRuntimeDep
) -> PlayResponse:
    """Checkpoint G Phase 9: objective parsing is a real Live LLM operation
    when `mode="live"` and `use_live_objective_parser=True` (an explicit,
    deliberate action — never fired on a debounce). Demo Mode, and Live
    Mode without that flag, keep the deterministic construction Checkpoints
    C–F have always used. `parse_objective()` itself makes zero DB writes;
    the Play row and its `objective_parse` telemetry are created together in
    one transaction below so an `llm_calls` row can never outlive/precede a
    nonexistent Play.
    """
    mode = Mode(body.mode)
    use_llm = mode is Mode.LIVE and body.use_live_objective_parser
    llm_provider = None
    if use_llm:
        runtime = _require_live_runtime(live_runtime)
        llm_provider = OpenAILLMProvider(runtime=runtime)

    try:
        parsed = await parse_objective(
            objective_text=body.objective,
            icp_overrides=body.icp_overrides,
            target_count=body.target_count,
            llm_provider=llm_provider,
            use_llm=use_llm,
        )
    except ValidationError as exc:
        raise UnprocessableEntityError(f"invalid icp_overrides: {exc}") from exc

    play_spec: PlaySpec = parsed.play_spec
    icp_spec_dict = {**play_spec.model_dump(mode="json"), "_parse_source": parsed.parse_source}
    play_kwargs = dict(
        name=_play_name(body.objective),
        objective_text=play_spec.objective_text,
        icp_spec=icp_spec_dict,
        mode=body.mode,
    )

    if parsed.attempts:
        play_id = await repos.llm_calls.create_play_with_attempts(
            play_kwargs=play_kwargs,
            call_group_id=str(uuid.uuid4()),
            operation="objective_parse",
            provider=parsed.provider or "openai",
            prompt_version="objective_parse-v1",
            attempts=parsed.attempts,
        )
    else:
        play_id = await plays.create(**play_kwargs)

    play_row = await plays.get(play_id)
    return await _to_response(play_row, repos)


@router.get("", response_model=list[PlayResponse])
async def list_plays(plays: PlaysRepoDep, repos: ReposDep) -> list[PlayResponse]:
    rows = await plays.list()
    return [await _to_response(row, repos) for row in rows]


@router.get("/{play_id}", response_model=PlayResponse)
async def get_play(play_id: str, plays: PlaysRepoDep, repos: ReposDep) -> PlayResponse:
    row = await plays.get(play_id)
    if row is None:
        raise NotFoundError(f"no play with id {play_id!r}")
    return await _to_response(row, repos)


@router.post("/{play_id}/runs", response_model=RunCreateResponse, status_code=202)
async def start_run(
    play_id: str, body: RunCreateRequest, plays: PlaysRepoDep, repos: ReposDep, live_runtime: LiveRuntimeDep
) -> RunCreateResponse:
    """Persists the Run and returns 202 immediately — `execute_run` is
    launched as a background task and does not block this response (§17).

    Live Mode requires a configured, running `LiveProviderRuntime` — never a
    silent fallback to Demo Mode (Checkpoint G §7). Prospect count is
    clamped to `LIVE_MAX_PROSPECTS_PER_RUN` for cost control.
    """
    play_row = await plays.get(play_id)
    if play_row is None:
        raise NotFoundError(f"no play with id {play_id!r}")

    mode_value = body.mode or play_row.mode
    mode = Mode(mode_value)

    play_spec = PlaySpec.model_validate(play_row.icp_spec)
    seed = body.seed if body.seed is not None else 42

    run_budget = None
    if mode is Mode.LIVE:
        _require_live_runtime(live_runtime)
        if play_spec.target_count > settings.live_max_prospects_per_run:
            play_spec = play_spec.model_copy(update={"target_count": settings.live_max_prospects_per_run})
        run_budget = RunBudget(settings.live_run_soft_budget_usd)

    provider_profile = build_provider_profile(mode, settings, run_budget=run_budget)
    run_id = await repos.runs.create(play_id=play_id, mode=mode_value, seed=seed, provider_profile=provider_profile)
    launch_run(run_id, play_spec, mode, seed, repos, live_runtime=live_runtime, run_budget=run_budget)

    return RunCreateResponse(run_id=run_id, status="RUNNING")
