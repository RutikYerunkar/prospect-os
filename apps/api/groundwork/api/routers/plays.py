from __future__ import annotations

import uuid
from datetime import timedelta

from pydantic import ValidationError

from fastapi import APIRouter, Request

from groundwork.api.deps import ExecutorIdDep, IsOperatorDep, LiveRuntimeDep, LiveSearchRuntimeDep, PlaysRepoDep, ReposDep
from groundwork.api.errors import NotFoundError, TooManyRequestsError, UnprocessableEntityError
from groundwork.api.live_gate import enforce_live_gate
from groundwork.api.rate_limit import SlidingWindowRateLimiter
from groundwork.api.run_service import launch_run
from groundwork.api.schemas import (
    PlayCreateRequest,
    PlayPreviewRequest,
    PlayPreviewResponse,
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
from groundwork.timeutil import utcnow

router = APIRouter(prefix="/api/plays", tags=["plays"])

# Checkpoint I1 Phase 8B: in-process, per-client-IP — correct for ONE API
# instance (see `api/rate_limit.py`), not a distributed rate limit. Protects
# the public (no-operator-session-required) write/preview surface from
# casual abuse; Live spend itself is bounded separately by
# `LIVE_MAX_ACTIVE_RUNS`/`LIVE_DAILY_RUN_ALLOWANCE` below, DB-backed, not by
# request rate.
_write_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.public_write_rate_limit_attempts, window_s=settings.public_write_rate_limit_window_s
)
_preview_limiter = SlidingWindowRateLimiter(
    max_attempts=settings.preview_rate_limit_attempts, window_s=settings.preview_rate_limit_window_s
)


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


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


def _require_search_runtime(search_runtime):
    if search_runtime is None:
        raise UnprocessableEntityError(
            "Live Mode requires TAVILY_API_KEY to be configured and the API process restarted — "
            "H2 Live Mode requires BOTH a real LLM AND a real search provider, never a fixture-"
            "search fallback"
        )
    return search_runtime


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
    body: PlayCreateRequest,
    request: Request,
    plays: PlaysRepoDep,
    repos: ReposDep,
    live_runtime: LiveRuntimeDep,
    is_operator: IsOperatorDep,
) -> PlayResponse:
    """Checkpoint G Phase 9: objective parsing is a real Live LLM operation
    when `mode="live"` and `use_live_objective_parser=True` (an explicit,
    deliberate action — never fired on a debounce). Demo Mode, and Live
    Mode without that flag, keep the deterministic construction Checkpoints
    C–F have always used. `parse_objective()` itself makes zero DB writes;
    the Play row and its `objective_parse` telemetry are created together in
    one transaction below so an `llm_calls` row can never outlive/precede a
    nonexistent Play.

    Checkpoint I1 Phase 8: `mode="live"` alone grants nothing —
    `enforce_live_gate` 401s without a valid operator session (and 403s an
    unsafe-method request whose Origin isn't allowed), before any objective
    parsing or DB write happens.

    Checkpoint I1 Phase 8B: rate-limited per client IP — this is a public
    write endpoint with no operator session required for Demo Mode.
    """
    if not _write_limiter.allow(_client_key(request)):
        raise TooManyRequestsError("too many play-creation requests — try again shortly")
    enforce_live_gate(request, body.mode, is_operator)
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


@router.post("/preview", response_model=PlayPreviewResponse)
async def preview_play(body: PlayPreviewRequest, request: Request) -> PlayPreviewResponse:
    """Checkpoint I1 Phase 7 — non-persisting, deterministic play preview.

    No `Play` row, no `Run` row, no `llm_calls` row, no DB access of any
    kind: `parse_objective(..., use_llm=False, llm_provider=None)` is
    exactly the deterministic construction `create_play` below falls back
    to for a non-LLM-parsed Play — passing the same two arguments here
    guarantees preview and committed specs can never drift apart for
    identical inputs, without a second hand-maintained copy of the
    construction logic. Exists so the New Play form can show a live
    `PlaySpec` preview on every keystroke without a write (or, in Live
    Mode, a paid call) per keystroke — see `PlayPreviewRequest` for why
    `mode`/`use_live_objective_parser` aren't accepted at all.

    Checkpoint I1 Phase 8B: rate-limited per client IP, generously (see
    `_preview_limiter`) — this is the most-called endpoint in the API by
    design (every debounced keystroke), so the limit exists for abuse, not
    normal typing.
    """
    if not _preview_limiter.allow(_client_key(request)):
        raise TooManyRequestsError("too many preview requests — try again shortly")
    try:
        parsed = await parse_objective(
            objective_text=body.objective,
            icp_overrides=body.icp_overrides,
            target_count=body.target_count,
            llm_provider=None,
            use_llm=False,
        )
    except ValidationError as exc:
        raise UnprocessableEntityError(f"invalid icp_overrides: {exc}") from exc

    return PlayPreviewResponse(
        icp_spec=parsed.play_spec.model_dump(mode="json"), parse_source=parsed.parse_source
    )


@router.get("", response_model=list[PlayResponse])
async def list_plays(plays: PlaysRepoDep, repos: ReposDep, is_operator: IsOperatorDep) -> list[PlayResponse]:
    """Checkpoint I1 Phase 8: unauthenticated callers see Demo plays only;
    an operator session additionally sees Live ones. Filtered here, not
    left to the frontend — a caller without an operator session gets no
    Live rows in the payload at all, not merely a UI that hides them."""
    rows = await plays.list()
    if not is_operator:
        rows = [r for r in rows if r.mode != "live"]
    return [await _to_response(row, repos) for row in rows]


@router.get("/{play_id}", response_model=PlayResponse)
async def get_play(play_id: str, request: Request, plays: PlaysRepoDep, repos: ReposDep, is_operator: IsOperatorDep) -> PlayResponse:
    row = await plays.get(play_id)
    if row is None:
        raise NotFoundError(f"no play with id {play_id!r}")
    enforce_live_gate(request, row.mode, is_operator)
    return await _to_response(row, repos)


@router.post("/{play_id}/runs", response_model=RunCreateResponse, status_code=202)
async def start_run(
    play_id: str,
    body: RunCreateRequest,
    request: Request,
    plays: PlaysRepoDep,
    repos: ReposDep,
    live_runtime: LiveRuntimeDep,
    search_runtime: LiveSearchRuntimeDep,
    executor_id: ExecutorIdDep,
    is_operator: IsOperatorDep,
) -> RunCreateResponse:
    """Persists the Run and returns 202 immediately — `execute_run` is
    launched as a background task and does not block this response (§17).

    H2: Live Mode requires BOTH a configured, running `LiveProviderRuntime`
    (OpenAI) AND `LiveSearchRuntime` (Tavily) — never a silent fallback to
    Demo Mode, and never real LLM + fixture search for a NEW run (that
    combination only exists in historical Checkpoint G runs, whose
    `provider_profile` stays truthfully unchanged). Prospect count is
    clamped to `LIVE_MAX_PROSPECTS_PER_RUN` for cost control.

    Checkpoint I1 Phase 8: a Live run additionally requires a valid
    operator session — checked before touching the runtime/budget or
    creating the Run row.

    Checkpoint I1 Phase 8B: a Live run is additionally bounded by
    `LIVE_MAX_ACTIVE_RUNS` (concurrently RUNNING) and
    `LIVE_DAILY_RUN_ALLOWANCE` (started in the last 24h) — both counted
    straight from the `runs` table, never an in-process counter that could
    drift or reset on restart.
    """
    play_row = await plays.get(play_id)
    if play_row is None:
        raise NotFoundError(f"no play with id {play_id!r}")

    mode_value = body.mode or play_row.mode
    enforce_live_gate(request, mode_value, is_operator)
    mode = Mode(mode_value)

    play_spec = PlaySpec.model_validate(play_row.icp_spec)
    seed = body.seed if body.seed is not None else 42

    run_budget = None
    if mode is Mode.LIVE:
        active = await repos.runs.count_active_by_mode(Mode.LIVE.value)
        if active >= settings.live_max_active_runs:
            raise TooManyRequestsError(
                f"LIVE_MAX_ACTIVE_RUNS ({settings.live_max_active_runs}) already running — "
                "wait for the current Live run to finish before starting another"
            )
        started_recently = await repos.runs.count_started_since(Mode.LIVE.value, utcnow() - timedelta(hours=24))
        if started_recently >= settings.live_daily_run_allowance:
            raise TooManyRequestsError(
                f"LIVE_DAILY_RUN_ALLOWANCE ({settings.live_daily_run_allowance}) reached for the last 24h"
            )
        _require_live_runtime(live_runtime)
        _require_search_runtime(search_runtime)
        if play_spec.target_count > settings.live_max_prospects_per_run:
            play_spec = play_spec.model_copy(update={"target_count": settings.live_max_prospects_per_run})
        run_budget = RunBudget(settings.live_run_soft_budget_usd)

    provider_profile = build_provider_profile(mode, settings, run_budget=run_budget)
    run_id = await repos.runs.create(
        play_id=play_id, mode=mode_value, seed=seed, provider_profile=provider_profile, executor_id=executor_id,
    )
    launch_run(
        run_id, play_spec, mode, seed, repos,
        live_runtime=live_runtime, run_budget=run_budget, search_runtime=search_runtime,
        executor_id=executor_id,
    )

    return RunCreateResponse(run_id=run_id, status="RUNNING")
