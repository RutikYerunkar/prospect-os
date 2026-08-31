from __future__ import annotations

from fastapi import APIRouter
from pydantic import ValidationError

from groundwork.api.deps import PlaysRepoDep, ReposDep
from groundwork.api.errors import NotFoundError, UnprocessableEntityError
from groundwork.api.run_service import launch_run
from groundwork.api.schemas import (
    PlayCreateRequest,
    PlayResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunSummary,
)
from groundwork.models.enums import Mode
from groundwork.models.schemas import PlaySpec

router = APIRouter(prefix="/api/plays", tags=["plays"])


def _play_name(objective: str) -> str:
    objective = " ".join(objective.split())
    return objective if len(objective) <= 80 else objective[:77] + "..."


async def _to_response(play_row, repos: ReposDep) -> PlayResponse:
    runs = await repos.runs.for_play(play_row.id)
    return PlayResponse(
        id=play_row.id,
        name=play_row.name,
        objective_text=play_row.objective_text,
        icp_spec=play_row.icp_spec,
        mode=play_row.mode,
        created_at=play_row.created_at,
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
async def create_play(body: PlayCreateRequest, plays: PlaysRepoDep, repos: ReposDep) -> PlayResponse:
    # No Objective Parser LLM agent yet (§8 — that's Checkpoint D's New Play
    # screen). Deterministically: the objective becomes `objective_text`
    # verbatim, and `icp_overrides` fills in the rest of `PlaySpec` — same
    # shape a real parser would eventually hand this endpoint.
    spec_data = {**body.icp_overrides, "objective_text": body.objective, "target_count": body.target_count}
    try:
        play_spec = PlaySpec.model_validate(spec_data)
    except ValidationError as exc:
        raise UnprocessableEntityError(f"invalid icp_overrides: {exc}") from exc

    play_id = await plays.create(
        name=_play_name(body.objective),
        objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"),
        mode=body.mode,
    )
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
async def start_run(play_id: str, body: RunCreateRequest, plays: PlaysRepoDep, repos: ReposDep) -> RunCreateResponse:
    """Persists the Run and returns 202 immediately — `execute_run` is
    launched as a background task and does not block this response (§17)."""
    play_row = await plays.get(play_id)
    if play_row is None:
        raise NotFoundError(f"no play with id {play_id!r}")

    mode_value = body.mode or play_row.mode
    if mode_value != Mode.DEMO.value:
        raise UnprocessableEntityError("only Mode.DEMO is implemented — Live Mode is P1")

    play_spec = PlaySpec.model_validate(play_row.icp_spec)
    seed = body.seed if body.seed is not None else 42

    run_id = await repos.runs.create(play_id=play_id, mode=mode_value, seed=seed)
    launch_run(run_id, play_spec, Mode.DEMO, seed, repos)

    return RunCreateResponse(run_id=run_id, status="RUNNING")
