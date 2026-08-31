from __future__ import annotations

from fastapi import APIRouter

from groundwork.api.deps import ReposDep
from groundwork.api.errors import NotFoundError
from groundwork.evaluation.metrics import compute_run_evaluation

router = APIRouter(prefix="/api/runs", tags=["evaluation"])


@router.get("/{run_id}/evaluation")
async def get_run_evaluation(run_id: str, repos: ReposDep) -> dict:
    run_row = await repos.runs.get(run_id)
    if run_row is None:
        raise NotFoundError(f"no run with id {run_id!r}")
    return await compute_run_evaluation(run_id, repos)
