"""FastAPI dependency providers.

Routes never import `SessionLocal` directly — they depend on
`get_session_factory`, which tests override via
`app.dependency_overrides[get_session_factory]` to point at an isolated
per-test SQLite file (see `tests/conftest.py`). `get_repos` /
`get_plays_repo` / `get_approvals_repo` all depend on `get_session_factory`
in turn, so overriding that one dependency retargets an entire request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from groundwork.api.operator_auth import COOKIE_NAME, verify_session_cookie
from groundwork.db import SessionLocal
from groundwork.engine.runner import Repos
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.repositories.plays import PlayRepository


def get_session_factory():
    return SessionLocal


SessionFactory = Annotated[object, Depends(get_session_factory)]


def get_live_runtime(request: Request):
    """The process-scoped `LiveProviderRuntime` created once in `main.py`'s
    lifespan, or `None` if no `OPENAI_API_KEY` is configured. Tests override
    this the same way they override `get_session_factory` — via
    `app.dependency_overrides` — to inject a runtime backed by
    `httpx2.MockTransport` without touching global state."""
    return getattr(request.app.state, "live_runtime", None)


LiveRuntimeDep = Annotated[object, Depends(get_live_runtime)]


def get_executor_id(request: Request) -> str:
    """The per-process executor id minted once at startup (`main.py`'s
    lifespan, `app.state.executor_id`) — identifies which running API
    process owns advancing a given run. Every run created through the real
    API threads this into `RunRepository.create()`/`launch_run()` so its
    lease, heartbeat, and terminal finalize are all ownership-guarded (see
    `repositories/runs.py`). Tests override this the same way they override
    `get_session_factory`."""
    return request.app.state.executor_id


ExecutorIdDep = Annotated[str, Depends(get_executor_id)]


def get_live_search_runtime(request: Request):
    """The process-scoped `LiveSearchRuntime` created once in `main.py`'s
    lifespan, or `None` if no `TAVILY_API_KEY` is configured — the search-
    side analogue of `get_live_runtime`. H2's Live Mode requires BOTH this
    AND `get_live_runtime` to be non-null."""
    return getattr(request.app.state, "live_search_runtime", None)


LiveSearchRuntimeDep = Annotated[object, Depends(get_live_search_runtime)]


def get_operator_session(request: Request) -> bool:
    """True iff the request carries a valid, currently-signed operator
    session cookie (Checkpoint I1 Phase 8). Read-only — never mutates
    anything, never raises; routes that need to REQUIRE an operator
    session raise `UnauthorizedError` themselves (see
    `groundwork/api/live_gate.py`), since whether a given resource needs
    gating depends on that resource's own `mode` field, not the route."""
    return verify_session_cookie(request.cookies.get(COOKIE_NAME))


IsOperatorDep = Annotated[bool, Depends(get_operator_session)]


def get_repos(session_factory: SessionFactory) -> Repos:
    return Repos.build(session_factory)


def get_plays_repo(session_factory: SessionFactory) -> PlayRepository:
    return PlayRepository(session_factory)


def get_approvals_repo(session_factory: SessionFactory) -> ApprovalRepository:
    return ApprovalRepository(session_factory)


ReposDep = Annotated[Repos, Depends(get_repos)]
PlaysRepoDep = Annotated[PlayRepository, Depends(get_plays_repo)]
ApprovalsRepoDep = Annotated[ApprovalRepository, Depends(get_approvals_repo)]
