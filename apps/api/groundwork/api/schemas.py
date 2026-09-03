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

from pydantic import BaseModel, ConfigDict, Field


# --- plays ---


class PlayCreateRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=2000)
    icp_overrides: dict[str, Any] = Field(default_factory=dict)
    mode: Literal["demo", "live"] = "demo"
    # 7 matches the demo fixture pack's own company count (§23) — see
    # groundwork/models/schemas.py::PlaySpec.target_count for why.
    target_count: int = Field(default=7, ge=1, le=25)
    # Checkpoint G Phase 9: explicit, deliberate action only — never fired on
    # the New Play form's debounce. Ignored (and free) in Demo Mode, where
    # objective parsing has always been deterministic.
    use_live_objective_parser: bool = False


class PlayPreviewRequest(BaseModel):
    """Checkpoint I1 Phase 7 — deliberately NOT `PlayCreateRequest`: no
    `mode`, no `use_live_objective_parser`. Preview is unconditionally
    deterministic (never an LLM call, in Demo or Live), so there is nothing
    for those fields to mean here. `extra="forbid"` turns a client sending
    `use_live_objective_parser` into a 422 rather than a silently ignored
    field — this endpoint should never look like it might have honored it.
    """

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=2000)
    icp_overrides: dict[str, Any] = Field(default_factory=dict)
    target_count: int = Field(default=7, ge=1, le=25)


class PlayPreviewResponse(BaseModel):
    icp_spec: dict[str, Any]
    parse_source: Literal["deterministic"] = "deterministic"


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
    parse_source: Literal["llm", "deterministic"] = "deterministic"
    runs: list[RunSummary] = Field(default_factory=list)


# --- runs ---


class RunCreateRequest(BaseModel):
    mode: Literal["demo", "live"] | None = None
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
    provider_profile: dict[str, Any] = Field(default_factory=dict)


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
    # v2 §Part 4/§L — additive only. One entry per (prospect, channel) with a
    # provider-backed state; a channel that was never attempted simply has no
    # entry (NOT_ATTEMPTED by omission — see
    # `engine/steps/contact_enrichment.py`). No V2-E UI reads this yet.
    contact_channels: list[dict[str, Any]] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    actor: str = "demo_user"


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
    actor: str = "demo_user"


# --- settings ---


class ProviderInfo(BaseModel):
    name: str
    configured: bool


class LiveAvailability(BaseModel):
    """§21/Phase 8/H2 Phase 17: enough truth for the New Play screen to
    disable Live and explain why, or show real bounds — never a secret
    value. `available` requires BOTH `llm_available` AND `search_available`
    (H2: no Live -> fixture-search fallback).

    Checkpoint I1 Phase 8/9: `operator_login_configured` and `is_operator`
    let the frontend tell apart "Live isn't configured on this deployment at
    all" from "Live is configured but this browser hasn't unlocked it yet"
    from "already unlocked" — three different states, three different
    messages, never conflated.
    """

    available: bool
    llm_available: bool = False
    search_available: bool = False
    operator_login_configured: bool = False
    is_operator: bool = False
    model: str
    reasoning_effort: str | None
    prompt_versions: dict[str, str]
    search_provider: str = "tavily"
    synthetic_search: bool = True
    query_plan_version: str = ""
    live_max_prospects_per_run: int
    llm_max_output_tokens: int
    llm_max_transport_retries: int
    llm_max_schema_retries: int
    llm_call_deadline_s: float
    live_step_timeout_s: float
    search_hard_bounds: dict[str, int] = Field(default_factory=dict)
    search_usage_capable: bool = False
    search_pricing_configured: bool = False
    pricing_configured: bool
    soft_budget_usd: float | None
    soft_budget_enforceable: bool


class ProviderSettingsResponse(BaseModel):
    mode: str
    llm: ProviderInfo
    search: ProviderInfo
    live: LiveAvailability
    # Checkpoint I1 Phase 9: sourced from the API rather than a duplicated
    # frontend constant — see apps/web/lib/constants.ts's old
    # MAX_CONCURRENT_PROSPECTS.
    max_concurrent_prospects: int
