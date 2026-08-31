"""Provider Protocols (IMPLEMENTATION_PLAN.md §11), extended for Checkpoint G
with a provider-neutral telemetry/error seam (§ Phase 1) that both
`DemoLLMProvider` and `providers/live/openai_llm.py::OpenAILLMProvider`
speak identically. Demo Mode and Live Mode share the identical pipeline,
steps, retries, `ProspectContext`, DB writes, event stream, scoring
arithmetic, review checks and evaluation — only the object satisfying these
Protocols differs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from groundwork.models.schemas import CompanySeed, PlaySpec

T = TypeVar("T", bound=BaseModel)


def make_ctx_key(run_id: str, prospect_id: str, step_name: str) -> str:
    """The one key threaded through idempotency, tracing, seeded jitter and
    scripted-failure lookups: `(run_id, prospect_id, step_name)`."""
    return f"{run_id}:{prospect_id}:{step_name}"


def parse_ctx_key(ctx_key: str) -> tuple[str, str, str]:
    run_id, prospect_id, step_name = ctx_key.split(":", 2)
    return run_id, prospect_id, step_name


def stable_seed(*parts: str) -> int:
    """A reproducible replacement for `hash((...))` on strings.

    Python's builtin `hash()` is salted per-process for strings (hash
    randomization), so `random.Random(hash((run_id, prospect_id, step)))` —
    the plan's illustrative snippet — would *not* actually reproduce across
    runs or processes. This uses a stable digest instead so `--seed` really
    does make a run replayable.
    """
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def digest_of(value: object) -> str:
    """sha256 prefix of a serialized payload — used for `input_digest`/
    `output_digest` on both `agent_tasks` and `llm_calls`: enough to prove
    determinism and diff two calls without persisting (or leaking) the full
    payload."""
    return hashlib.sha256(repr(value).encode()).hexdigest()[:16]


# --- LLM operation / attempt taxonomy (Phase 1) ---------------------------


class LLMOperation(StrEnum):
    RESEARCH_EXTRACTION = "research_extraction"
    SCORE_EXPLANATION = "score_explanation"
    PERSONALIZATION = "personalization"
    OBJECTIVE_PARSE = "objective_parse"


class LLMAttemptKind(StrEnum):
    INITIAL = "initial"
    TRANSPORT_RETRY = "transport_retry"
    SCHEMA_REPAIR = "schema_repair"


class LLMAttemptStatus(StrEnum):
    OK = "OK"
    INVALID_JSON = "INVALID_JSON"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    NO_OUTPUT = "NO_OUTPUT"
    REFUSED = "REFUSED"
    TRUNCATED = "TRUNCATED"
    CONTENT_FILTERED = "CONTENT_FILTERED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    NOT_ATTEMPTED_BUDGET = "NOT_ATTEMPTED_BUDGET"


class LLMAttemptTelemetry(BaseModel):
    """One provider attempt. `engine/llm.py::call_structured` is the only
    thing that persists these (into `llm_calls`); providers only produce
    them — see the Phase 3 boundary note in `providers/live/openai_llm.py`.
    """

    attempt: int  # flat 1-based sequence across the whole logical call
    attempt_kind: LLMAttemptKind
    schema_round: int  # 0 pre-repair, 1 after the one schema-repair attempt
    transport_retry_index: int  # 0 for the first try in a round, >=1 after
    status: LLMAttemptStatus
    started_at: datetime
    finished_at: datetime
    latency_ms: float
    model: str
    reasoning_effort: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_total: int = 0
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    http_status: int | None = None
    provider_request_id: str | None = None
    incomplete_reason: str | None = None
    error_type: str | None = None
    error_message: str | None = None  # redacted before this is set
    validation_error: str | None = None  # redacted + truncated
    input_digest: str
    output_digest: str | None = None


class LLMResult(BaseModel, Generic[T]):
    """Generic over the structured-output Pydantic model. `parsed` is
    already a validated `T` instance — callers never re-validate `.data`."""

    model_config = {"arbitrary_types_allowed": True}

    parsed: T
    raw: dict[str, Any] = Field(default_factory=dict)
    operation: LLMOperation
    model: str
    provider: str
    prompt_version: str
    attempts: list[LLMAttemptTelemetry] = Field(default_factory=list)

    # Backward-compatible view for any pre-Checkpoint-G caller: the old
    # single-record shape (`.data`, `.tokens_in`, `.tokens_out`) collapsed
    # from the final attempt. New code should read `.parsed`/`.attempts`.
    @property
    def data(self) -> dict[str, Any]:
        return self.parsed.model_dump(mode="json")

    @property
    def tokens_in(self) -> int:
        return self.attempts[-1].tokens_in if self.attempts else 0

    @property
    def tokens_out(self) -> int:
        return self.attempts[-1].tokens_out if self.attempts else 0


# --- errors -----------------------------------------------------------


class ProviderError(Exception):
    """Base for exceptions provider implementations raise. Carries the
    accumulated attempt telemetry for this logical call, if any is
    available at the point of failure — see Phase 1's "provider errors must
    carry accumulated attempts on exhaustion" requirement."""

    def __init__(self, message: str, *, attempts: list[LLMAttemptTelemetry] | None = None) -> None:
        super().__init__(message)
        self.attempts: list[LLMAttemptTelemetry] = attempts or []


# Step-level retryable (transient — retrying the whole step, and thus a
# fresh logical call, might succeed).
class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


# Permanent — never step-retried, never schema-repaired.
class SchemaViolation(ProviderError):
    """Raised when a provider's raw output fails Pydantic validation even
    after the one schema-repair attempt."""


class ProviderRefusal(ProviderError):
    pass


class ProviderOutputTruncated(ProviderError):
    pass


class ProviderContentFiltered(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    pass


class ProviderNotConfigured(ProviderError):
    """Live Mode requested without the credentials/runtime it needs. Never
    triggers a silent fallback to `DemoLLMProvider` — see Phase 7."""


class ProviderBudgetExceeded(ProviderError):
    """The run's soft spending threshold had already tripped when this call
    would have started — see `engine/run_budget.py`. Not a hard ceiling:
    already-in-flight calls are unaffected, only *new* calls are blocked."""


# Legacy name kept for the demo fixture pack's `failure_script.error` string
# lookup (`ProviderTimeout` / `ProviderUnavailable` / `SchemaViolation`).
FAILURE_TYPES: dict[str, type[ProviderError]] = {
    "ProviderTimeout": ProviderTimeout,
    "ProviderUnavailable": ProviderUnavailable,
    "ProviderRateLimited": ProviderRateLimited,
    "SchemaViolation": SchemaViolation,
}

# Exactly the three types the plan names as step-level retryable.
STEP_RETRYABLE: tuple[type[Exception], ...] = (ProviderTimeout, ProviderUnavailable, ProviderRateLimited)


class SourceDocument(BaseModel):
    """A single fetched (or, in Demo Mode, fixture-authored) source."""

    ref: str
    title: str
    claim: str
    text: str
    source_provider: str
    signal_type: str | None = None
    confidence: float = 0.8


class PromptEnvelope(BaseModel):
    """Built only from a `ProspectContext` — see the isolation model in
    docs/ARCHITECTURE.md. Never accumulates history across prospects."""

    ctx_key: str
    system: str
    user: str
    metadata: dict[str, Any] = {}


class LLMProvider(Protocol):
    name: str

    async def structured(
        self, envelope: PromptEnvelope, schema: type[T], *, ctx_key: str, operation: LLMOperation
    ) -> LLMResult[T]: ...


class SearchProvider(Protocol):
    name: str

    async def discover(self, spec: PlaySpec, limit: int) -> list[CompanySeed]: ...

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> list[SourceDocument]: ...


@dataclass
class ProviderBundle:
    llm: LLMProvider
    search: SearchProvider
