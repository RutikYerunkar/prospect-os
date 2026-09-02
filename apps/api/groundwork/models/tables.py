"""SQLAlchemy ORM tables — the relational schema from IMPLEMENTATION_PLAN.md §20.

Tables where things are queried/joined/counted; JSON where a substructure is
always read whole for one row. `create_all()` only — no Alembic yet (P2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import DateTime as _SADateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from groundwork.timeutil import utcnow


class Base(DeclarativeBase):
    pass


# Every absolute-time column in this schema uses this module-level instance
# instead of the bare `sqlalchemy.DateTime` class — `timezone=True` is
# required for Postgres's `timestamptz` to round-trip aware datetimes
# correctly. SQLite silently drops the tzinfo on read regardless (see
# `groundwork/timeutil.py`), so this is a no-op for SQLite's on-disk format,
# not a wire-compatibility break there. A single shared instance (not a type)
# is fine — SQLAlchemy column types are stateless/immutable once constructed.
DateTime = _SADateTime(timezone=True)


def _now() -> datetime:
    return utcnow()


class PlayRow(Base):
    __tablename__ = "plays"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    objective_text: Mapped[str] = mapped_column(Text)
    icp_spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    mode: Mapped[str] = mapped_column(String, default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    play_id: Mapped[str] = mapped_column(ForeignKey("plays.id"))
    status: Mapped[str] = mapped_column(String)
    mode: Mapped[str] = mapped_column(String, default="demo")
    seed: Mapped[int] = mapped_column(Integer)
    plan: Mapped[list[Any]] = mapped_column(JSON, default=list)
    counters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Truthful, no-secrets snapshot of what actually ran this run — see
    # `providers/profile.py::build_provider_profile` (Checkpoint G Phase 7).
    provider_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Checkpoint I1 Phase 3: database-correct per-run SSE sequencing ---
    # The per-run event counter. `EventRepository.append()` is the only
    # writer, via `UPDATE runs SET last_event_seq = last_event_seq + 1
    # WHERE id = :run_id RETURNING last_event_seq` in the same transaction
    # as the `run_events` insert it guards — see `repositories/events.py`.
    # This IS the correctness mechanism (row-level lock serializes
    # concurrent same-run appends; different runs never contend); there is
    # deliberately no `asyncio.Lock`/process-local lock anywhere near it.
    last_event_seq: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # --- Checkpoint I1 Phase 4: ownership-safe execution lease ---
    # Minted once per process at startup (`main.py`'s lifespan) — identifies
    # which running API process currently owns advancing this run. Every
    # lifecycle transition (heartbeat, finalize, reap) is a guarded
    # `UPDATE ... WHERE id=:run_id AND executor_id=:executor_id` so a stale
    # process that has lost ownership can never resurrect or finalize a run
    # another process (or the reaper) has already taken over.
    executor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        # Play-history listing (`GET /plays/{id}` -> runs for a play, newest
        # first) and the reaper/dashboard access pattern (status + recency).
        Index("ix_runs_play_id_started_at", "play_id", "started_at"),
        Index("ix_runs_status_heartbeat_at", "status", "heartbeat_at"),
    )


class CompanyRow(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    canonical_domain: Mapped[str] = mapped_column(String, unique=True)
    normalized_name: Mapped[str] = mapped_column(String)
    display_name: Mapped[str] = mapped_column(String)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    origin: Mapped[str] = mapped_column(String, default="demo_fixture")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProspectRow(Base):
    __tablename__ = "prospects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"))
    status: Mapped[str] = mapped_column(String, default="PENDING")
    current_stage: Mapped[str] = mapped_column(String, default="DISCOVERED")
    dedupe_key: Mapped[str] = mapped_column(String)
    duplicate_of: Mapped[str | None] = mapped_column(
        ForeignKey("prospects.id"), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    source_provider: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    claim: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str] = mapped_column(Text)
    signal_type: Mapped[str | None] = mapped_column(String, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    origin: Mapped[str] = mapped_column(String)


class SignalRow(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    type: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    # H1 Phase 9: this column and `occurred_at` above existed on the pydantic
    # `Signal` model since Checkpoint B but were never actually written by
    # `insert_signals` — a real gap this checkpoint closes, not a new field.
    grounded: Mapped[bool] = mapped_column(default=True)


class ICPScoreRow(Base):
    __tablename__ = "icp_scores"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), unique=True)
    overall: Mapped[int] = mapped_column(Integer)
    dimensions: Mapped[list[Any]] = mapped_column(JSON)
    modifiers: Mapped[list[Any]] = mapped_column(JSON, default=list)
    disqualified: Mapped[bool] = mapped_column(default=False)
    explanation: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float)
    rubric_version: Mapped[str] = mapped_column(String, default="v1")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ContactRow(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"))
    full_name: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    persona: Mapped[bool] = mapped_column(default=False)
    linkedin_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    verification: Mapped[str] = mapped_column(String)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class OutreachDraftRow(Base):
    __tablename__ = "outreach_drafts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"))
    channel: Mapped[str] = mapped_column(String, default="email")
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    claim_map: Mapped[list[Any]] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String, default="DRAFT")


class ReviewResultRow(Base):
    __tablename__ = "review_results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"))
    verdict: Mapped[str] = mapped_column(String)
    checks: Mapped[list[Any]] = mapped_column(JSON)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"))
    decision: Mapped[str] = mapped_column(String)
    actor: Mapped[str] = mapped_column(String, default="demo_user")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AgentTaskRow(Base):
    """The trace: one row per step *attempt* — see IMPLEMENTATION_PLAN.md §15."""

    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"))
    step_name: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_agent_tasks_run_prospect", "run_id", "prospect_id"),
        Index("ix_agent_tasks_prospect_step", "prospect_id", "step_name"),
    )


class LLMCallRow(Base):
    """One row per provider *attempt* (Checkpoint G Phase 3) — never
    overloaded onto `run_events` (that stays the resumable SSE progress
    log) or collapsed onto `agent_tasks` (that stays one row per *step*
    attempt; a step attempt may make zero, one, or in principle several
    logical LLM calls, each with its own retry/repair attempts here).

    `call_group_id` ties every attempt of one logical call together;
    `UNIQUE(call_group_id, attempt)` is the flat-retry-loop invariant made a
    DB constraint. `objective_parse` rows set `play_id` and leave
    `run_id`/`prospect_id` null (the call happens before any Run/Prospect
    exists); every pipeline-operation row sets `run_id`/`prospect_id`/
    `step_name` and leaves `play_id` null.
    """

    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    call_group_id: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer)
    attempt_kind: Mapped[str] = mapped_column(String)
    schema_round: Mapped[int] = mapped_column(Integer, default=0)
    transport_retry_index: Mapped[int] = mapped_column(Integer, default=0)
    operation: Mapped[str] = mapped_column(String)

    play_id: Mapped[str | None] = mapped_column(ForeignKey("plays.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    prospect_id: Mapped[str | None] = mapped_column(ForeignKey("prospects.id"), nullable=True)
    step_name: Mapped[str | None] = mapped_column(String, nullable=True)

    provider: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)

    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tokens_total: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    incomplete_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (UniqueConstraint("call_group_id", "attempt", name="uq_llm_calls_group_attempt"),)


class SearchCallRow(Base):
    """One row per search-provider call *attempt* (H1 Phase 9/12) — the
    search-side analogue of `llm_calls`. Never overloaded onto `run_events`
    (SSE stays the resumable progress log, not a telemetry sink)."""

    __tablename__ = "search_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    call_group_id: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    attempt_kind: Mapped[str] = mapped_column(String, default="initial")
    operation: Mapped[str] = mapped_column(String)

    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True, index=True)
    prospect_id: Mapped[str | None] = mapped_column(ForeignKey("prospects.id"), nullable=True)
    play_id: Mapped[str | None] = mapped_column(ForeignKey("plays.id"), nullable=True)

    provider: Mapped[str] = mapped_column(String)
    query_group_id: Mapped[str] = mapped_column(String, default="")
    template_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rendered_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    query_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OK")

    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)

    result_count: Mapped[int] = mapped_column(Integer, default=0)
    selected_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    chars_retrieved: Mapped[int] = mapped_column(Integer, default=0)
    credits_used: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("call_group_id", "attempt", name="uq_search_calls_group_attempt"),)


class SourceDocumentRow(Base):
    """One row per retrieval *occurrence* (H1 Phase 9/10) — every provider
    result from every search call, before dedupe. `winner_of_group_id` self-
    references the row this occurrence's group collapsed onto (null on the
    winner itself); `Evidence` is created only from winners
    (`domain/source_identity.py::select_winners`)."""

    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    search_call_id: Mapped[str | None] = mapped_column(ForeignKey("search_calls.id"), nullable=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    # Nullable (H2): a Stage A discovery-time occurrence is run-scoped, not
    # prospect-scoped — no `ProspectContext`/prospect exists yet when
    # `engine/discovery.py` persists it, exactly like `search_calls.
    # prospect_id` already allows `None` for the run-level `discover()` call.
    prospect_id: Mapped[str | None] = mapped_column(ForeignKey("prospects.id"), nullable=True, index=True)

    ref: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String, nullable=True)
    domain: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String, nullable=True)
    excerpt: Mapped[str] = mapped_column(Text)
    full_text_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(String, default="demo_fixture")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    provider: Mapped[str] = mapped_column(String)
    provider_result_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String, default="fixture")
    status: Mapped[str] = mapped_column(String, default="ok")
    origin: Mapped[str] = mapped_column(String, default="DEMO_FIXTURE")

    identity_key: Mapped[str] = mapped_column(String)
    is_winner: Mapped[bool] = mapped_column(default=True)
    canonical_source_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_documents.id"), nullable=True
    )
    # Deliberately NOT a physical FK to `evidence.id`: it is set (best-
    # effort, from `engine/runner.py`) only *after* Evidence is actually
    # committed at the end of a successful prospect run, using the same
    # deterministic uuid5 id `engine/steps/research.py` already computed —
    # a winner occurrence whose prospect never reached a successful
    # extraction legitimately has no Evidence row to point at, and that
    # must not be a constraint violation.
    evidence_id: Mapped[str | None] = mapped_column(String, nullable=True)


class RunEventRow(Base):
    """Append-only event log, replayed over SSE (Checkpoint C). Never updated.

    Checkpoint I1 Phase 3: `seq` is no longer a global autoincrement — it's
    monotonically increasing PER RUN, minted by `EventRepository.append()`
    from `runs.last_event_seq` in the same transaction as this insert.
    `PRIMARY KEY (run_id, seq)` makes a duplicate/out-of-order seq for one
    run a constraint violation, not just a convention; different runs'
    sequences never share a namespace and never contend with each other.
    """

    __tablename__ = "run_events"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    type: Mapped[str] = mapped_column(String)
    prospect_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
