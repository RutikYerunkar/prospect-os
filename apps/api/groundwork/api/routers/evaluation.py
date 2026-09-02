from __future__ import annotations

from fastapi import APIRouter, Request

from groundwork.api.deps import IsOperatorDep, ReposDep
from groundwork.api.errors import NotFoundError
from groundwork.api.live_gate import enforce_live_gate
from groundwork.evaluation.metrics import compute_run_evaluation

router = APIRouter(prefix="/api/runs", tags=["evaluation"])


@router.get("/{run_id}/evaluation")
async def get_run_evaluation(run_id: str, request: Request, repos: ReposDep, is_operator: IsOperatorDep) -> dict:
    run_row = await repos.runs.get(run_id)
    if run_row is None:
        raise NotFoundError(f"no run with id {run_id!r}")
    enforce_live_gate(request, run_row.mode, is_operator)
    return await compute_run_evaluation(run_id, repos)
