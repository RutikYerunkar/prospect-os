"""API request/response DTOs — the HTTP-facing shapes from §21.

Deliberately separate from `groundwork/models/schemas.py`: those are the
domain/engine's own Pydantic models (what `ProspectContext` and the steps
pass around), and must stay importable with zero API/web awareness. These
are the wire shapes `routers/` return — some pass a domain model through
almost unchanged, others aggregate several ORM rows into one JSON document.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --- plays ---


class PlayCreateRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    icp_overrides: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["demo"] = "demo"
    target_count: int = Field(default=6, ge=1, le=25)


class RunSummary(BaseModel):
    id: str
    status: str
    mode: str
    seed: int
    started_at: datetime
    finished_at: datetime | None
    counters: dict[str, int]


class PlayResponse(BaseModel):
    id: str
    name: str
    objective_text: str
    icp_spec: dict[str, Any]
    mode: str
    created_at: datetime
    runs: list[RunSummary] = Field(default_factory=list)


# --- runs ---


class RunCreateRequest(BaseModel):
    mode: Literal["demo"] | None = None
    seed: int | None = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunResponse(BaseModel):
    id: str
    play_id: str
    status: str
    mode: str
    seed: int
    plan: list[Any]
    counters: dict[str, int]
    started_at: datetime
    finished_at: datetime | None
    duration_ms: float | None
    error: str | None


# --- prospects ---


class ProspectSummary(BaseModel):
    id: str
    run_id: str
    company_name: str
    company_domain: str
    stage: str
    status: str
    top_signal: str | None
    contact_verification: str | None
    contact_name: str | None
    icp_score: int | None
    confidence: float | None
    had_retry: bool
    approval_state: str
    error: str | None


class ApprovalInfo(BaseModel):
    state: str
    actor: str | None = None
    reason: str | None = None
    decided_at: datetime | None = None


class ProspectAggregate(BaseModel):
    id: str
    run_id: str
    company: dict[str, Any]
    dedupe_key: str
    duplicate_of: str | None
    stage: str
    status: str
    error: str | None
    evidence: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    score: dict[str, Any] | None
    contact: dict[str, Any] | None
    drafts: list[dict[str, Any]]
    review: dict[str, Any] | None
    trace: list[dict[str, Any]]
    approval: ApprovalInfo


class ApproveRequest(BaseModel):
    actor: str = "demo_user"


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = "demo_user"


# --- settings ---


class ProviderInfo(BaseModel):
    name: str
    configured: bool


class ProviderSettingsResponse(BaseModel):
    mode: str
    llm: ProviderInfo
    search: ProviderInfo
