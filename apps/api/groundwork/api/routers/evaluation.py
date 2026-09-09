from __future__ import annotations

from fastapi import APIRouter, Request

from groundwork.api.deps import ActionsRepoDep, ApprovalsRepoDep, IsOperatorDep, ReposDep
from groundwork.api.errors import NotFoundError
from groundwork.api.live_gate import enforce_live_gate
from groundwork.evaluation.metrics import compute_run_evaluation

router = APIRouter(prefix="/api/runs", tags=["evaluation"])


@router.get("/{run_id}/evaluation")
async def get_run_evaluation(
    run_id: str,
    request: Request,
    repos: ReposDep,
    actions: ActionsRepoDep,
    approvals: ApprovalsRepoDep,
    is_operator: IsOperatorDep,
) -> dict:
    """`ActionRepository`/`ApprovalRepository` are wired in HERE, separately
    from `Repos` (V2-J §4) — the engine's `Repos` dataclass deliberately
    stays without an `ActionRepository` slot, preserving the architectural
    boundary that the pipeline engine can never write (or, previously,
    even read) governed-action state. Evaluation is a read-only API
    surface, not the engine, so it may depend on both independently."""
    run_row = await repos.runs.get(run_id)
    if run_row is None:
        raise NotFoundError(f"no run with id {run_id!r}")
    enforce_live_gate(request, run_row.mode, is_operator)
    return await compute_run_evaluation(run_id, repos, actions=actions, approvals=approvals)
