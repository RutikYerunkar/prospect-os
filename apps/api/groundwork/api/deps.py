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

from fastapi import Depends

from groundwork.db import SessionLocal
from groundwork.engine.runner import Repos
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.repositories.plays import PlayRepository


def get_session_factory():
    return SessionLocal


SessionFactory = Annotated[object, Depends(get_session_factory)]


def get_repos(session_factory: SessionFactory) -> Repos:
    return Repos.build(session_factory)


def get_plays_repo(session_factory: SessionFactory) -> PlayRepository:
    return PlayRepository(session_factory)


def get_approvals_repo(session_factory: SessionFactory) -> ApprovalRepository:
    return ApprovalRepository(session_factory)


ReposDep = Annotated[Repos, Depends(get_repos)]
PlaysRepoDep = Annotated[PlayRepository, Depends(get_plays_repo)]
ApprovalsRepoDep = Annotated[ApprovalRepository, Depends(get_approvals_repo)]
