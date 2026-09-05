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
from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

from groundwork.models.schemas import CompanySeed, PlaySpec, SourceDocument

if TYPE_CHECKING:
    # `from __future__ import annotations` (below) makes every annotation in
    # this module lazy, so this one-directional import (contact_base.py
    # never imports this module) never becomes circular at runtime — see
    # `ProviderBundle.enrichment`'s docstring.
    from groundwork.providers.contact_base import EnrichmentProvider

__all__ = [
    "make_ctx_key",
    "parse_ctx_key",
    "stable_seed",
    "digest_of",
    "LLMOperation",
    "LLMAttemptKind",
    "LLMAttemptStatus",
    "LLMAttemptTelemetry",
    "LLMResult",
    "ProviderError",
    "ProviderTimeout",
    "ProviderUnavailable",
    "ProviderRateLimited",
    "SchemaViolation",
    "ProviderRefusal",
    "ProviderOutputTruncated",
    "ProviderContentFiltered",
    "ProviderAuthError",
    "ProviderNotConfigured",
    "ProviderBudgetExceeded",
    "ProviderQuotaExceeded",
    "FAILURE_TYPES",
    "STEP_RETRYABLE",
    "SourceDocument",
    "SearchOperation",
    "SearchAttemptKind",
    "SearchAttemptStatus",
    "SearchAttemptTelemetry",
    "SearchProviderError",
    "SearchTimeout",
    "SearchRateLimited",
    "SearchProviderUnavailable",
    "SearchAuthError",
    "SearchInvalidResponse",
    "SourceExtractionFailed",
    "DiscoveryResult",
    "DomainCandidate",
    "DomainCandidates",
    "RawSearchHit",
    "RawDiscoveryResult",
    "SourceBundle",
    "PromptEnvelope",
    "LLMProvider",
    "SearchProvider",
    "ProviderBundle",
]

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
    # v2 §V2-F — a SEPARATE operation from PERSONALIZATION: LinkedIn drafting
    # is its own LLM call with its own prompt/output schema and ctx_key
    # (`personalize:linkedin`). The email branch above is untouched by this.
    LINKEDIN_PERSONALIZATION = "linkedin_personalization"
    OBJECTIVE_PARSE = "objective_parse"
    # H2 Stage B: bounded search-result excerpts -> candidate company names.
    # Run-scoped (no prospect exists yet) — see `engine/discovery.py`.
    DISCOVERY_EXTRACTION = "discovery_extraction"
    # H2 Stage C ambiguous-fallback: pick one served domain-resolution
    # candidate ref, or null. Never invoked when the deterministic path
    # (exactly one acceptable domain) already resolved the candidate.
    DOMAIN_SELECTION = "domain_selection"


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
    # H2 second post-smoke fix: a real 429 whose body identifies account/
    # project quota or billing exhaustion (`type=insufficient_quota`,
    # `code=credit_balance_exhausted`, or an equivalent signal) — distinct
    # from an ordinary transient `RATE_LIMITED` 429. Permanent: retrying
    # cannot recover an exhausted balance. Never confused with
    # `NOT_ATTEMPTED_BUDGET`, which is Groundwork's own soft `RunBudget`
    # tripwire, not anything the provider itself reported.
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
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


class ProviderQuotaExceeded(ProviderError):
    """The PROVIDER's own account/project quota or billing balance is
    exhausted (`type=insufficient_quota`, `code=credit_balance_exhausted`,
    or an equivalent provider signal on a 429) — never Groundwork's own
    `RunBudget` soft threshold (that's `ProviderBudgetExceeded`, a
    completely different thing: our spending guess vs. the provider's own
    authoritative account state). Permanent — no amount of retrying
    recovers an exhausted balance, so this is raised immediately after
    exactly one attempt, never transport-retried, never schema-repaired."""


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


# --- Search provider contract / telemetry (H1 Phase 12) -------------------
#
# `SourceDocument` itself is defined in `models/schemas.py` (a pure data
# model domain/ can import, unlike this provider-boundary module) and
# re-exported here for every existing call site that imports it from
# `providers.base`.


class SearchOperation(StrEnum):
    DISCOVER = "discover"
    RESOLVE_DOMAIN = "resolve_domain"
    FETCH_SOURCES = "fetch_sources"
    # H2: the two real Tavily operations `fetch_sources()`/`raw_discover()`
    # issue internally — DemoSearchProvider never emits these, only
    # `TavilySearchProvider` does. Kept distinct from FETCH_SOURCES (which
    # stays the Demo Mode single-shot operation label) so real telemetry
    # distinguishes "searched" from "extracted content for."
    DOMAIN_SEARCH = "domain_search"
    EXTRACT = "extract"


class SearchAttemptKind(StrEnum):
    INITIAL = "initial"
    TRANSPORT_RETRY = "transport_retry"


class SearchAttemptStatus(StrEnum):
    OK = "OK"
    NO_RESULTS = "NO_RESULTS"  # legitimate zero-result outcome (Demo + H1)
    EMPTY_RESULT = "EMPTY_RESULT"  # H2 alias of the same concept for a real provider call
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    PARTIAL_EXTRACTION = "PARTIAL_EXTRACTION"
    NOT_ATTEMPTED_BUDGET = "NOT_ATTEMPTED_BUDGET"


class SearchAttemptTelemetry(BaseModel):
    """One search-provider call attempt — the search-side analogue of
    `LLMAttemptTelemetry`. `engine/search.py::call_search()` is the only
    thing that persists these (into `search_calls`); providers only produce
    them, exactly like the LLM boundary in `engine/llm.py`. Kept out of
    `run_events` (H1 Phase 12) — SSE stays the resumable *progress* log,
    not a telemetry sink.
    """

    provider: str
    operation: SearchOperation
    query_group_id: str
    template_id: str | None = None
    rendered_query: str | None = None
    query_digest: str | None = None
    call_group_id: str
    attempt: int = 1
    attempt_kind: SearchAttemptKind = SearchAttemptKind.INITIAL
    status: SearchAttemptStatus = SearchAttemptStatus.OK
    started_at: datetime
    finished_at: datetime
    latency_ms: float = 0.0
    result_count: int = 0
    selected_count: int = 0
    provider_request_id: str | None = None
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None  # redacted before this is set
    cost_usd: float | None = None
    chars_retrieved: int = 0
    # H2 Phase 16: provider-native usage/credits from Tavily's
    # `include_usage` response field, kept distinct from `cost_usd` — a
    # real credits figure can be known even when no trustworthy USD
    # conversion is configured (`cost_usd` stays null in that case, per the
    # "never fabricate cost" rule; `credits_used` still reports the truth).
    credits_used: float | None = None


class SearchProviderError(Exception):
    """Base for exceptions search-provider implementations raise — the
    search-side analogue of `ProviderError`. Carries whatever
    `SearchAttemptTelemetry` was produced before the failure, if any, so
    `engine/search.py`'s call sites can persist a FAILED/PROVIDER_ERROR
    `search_calls` row even when the logical call never returns a result
    (mirrors `ProviderError.attempts` for the LLM boundary exactly)."""

    def __init__(self, message: str, *, telemetry: list[SearchAttemptTelemetry] | None = None) -> None:
        super().__init__(message)
        self.telemetry: list[SearchAttemptTelemetry] = telemetry or []


# H2 Phase 5 — typed search error taxonomy, the search-side analogue of the
# LLM `Provider*` hierarchy above. `SourceExtractionFailed` is deliberately
# NOT step-fatal by convention (callers decide whether enough sources
# survive); the rest are raised by `TavilySearchProvider` after its own
# bounded transport-retry budget is exhausted.
class SearchTimeout(SearchProviderError):
    pass


class SearchRateLimited(SearchProviderError):
    pass


class SearchProviderUnavailable(SearchProviderError):
    pass


class SearchAuthError(SearchProviderError):
    """Permanent — invalid/missing TAVILY_API_KEY. Never retried."""


class SearchInvalidResponse(SearchProviderError):
    """The provider returned 200 with a body this adapter can't parse."""


class SourceExtractionFailed(SearchProviderError):
    """One source's Extract call failed — a per-source degradation, not
    necessarily a failed prospect. Never raised for the whole logical call;
    callers persist a PARTIAL_EXTRACTION telemetry row and keep going with
    whatever sources did extract."""


class DiscoveryResult(BaseModel):
    """`SearchProvider.discover()` return shape — a roster of candidate
    companies plus the telemetry of however many provider calls it took."""

    companies: list[CompanySeed]
    telemetry: list[SearchAttemptTelemetry] = Field(default_factory=list)


class DomainCandidate(BaseModel):
    """One served domain-resolution candidate (H2 Stage C) — an opaque
    `ref` the model may cite (never a URL/domain), plus the provider-
    returned `url`/`title` the engine (never the model) uses to compute a
    canonical domain via `domain/discovery.py::resolve_candidate_domain`."""

    ref: str
    url: str
    title: str = ""


class DomainCandidates(BaseModel):
    """`SearchProvider.resolve_domain()` return shape. `domains` is the
    original H1-era plain list (still what `DemoSearchProvider` returns —
    a resolved domain per fixture lookup, no candidate/ref structure
    needed). `candidates` is the H2 real-provider shape: every domain-
    resolution query result served this round, so the engine's identity
    gate can pick among them (deterministically, or via the bounded
    `DOMAIN_SELECTION` LLM fallback) without ever trusting a model-typed
    domain string."""

    domains: list[str] = Field(default_factory=list)
    candidates: list[DomainCandidate] = Field(default_factory=list)
    telemetry: list[SearchAttemptTelemetry] = Field(default_factory=list)


class RawSearchHit(BaseModel):
    """One Stage-A discovery search result, reduced to exactly what the
    DISCOVERY_EXTRACTION LLM call is allowed to see: an opaque `ref` and a
    bounded text `excerpt`. `url`/`domain` are carried here for the
    engine's own `source_documents` persistence and are never read by any
    prompt-building code — see `prompts/discovery_extraction.py`."""

    ref: str
    title: str
    excerpt: str
    url: str | None = None


class RawDiscoveryResult(BaseModel):
    """`TavilySearchProvider.raw_discover()` return shape — Stage A only,
    never a resolved company roster. `hits` is what Stage B's LLM call
    sees; `documents` is the same results reduced to `SourceDocument`
    retrieval-occurrence rows for persistence."""

    hits: list[RawSearchHit] = Field(default_factory=list)
    documents: list[SourceDocument] = Field(default_factory=list)
    telemetry: list[SearchAttemptTelemetry] = Field(default_factory=list)


class SourceBundle(BaseModel):
    """`SearchProvider.fetch_sources()` return shape — every retrieval
    *occurrence* this call produced (duplicates across queries included;
    winner selection happens later, in `domain/source_identity.py`) plus
    the telemetry of the call(s) that produced them."""

    documents: list[SourceDocument]
    telemetry: list[SearchAttemptTelemetry] = Field(default_factory=list)


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
    """Provider-neutral search contract (H1 Phase 12). No concrete Tavily/Exa
    adapter exists yet — `DemoSearchProvider` (Phase 13) is the only
    implementation in this codebase. Every method returns its documents/
    domains alongside the telemetry of however many provider calls it took;
    `engine/search.py::call_search()` is the only thing that persists that
    telemetry."""

    name: str

    async def discover(self, spec: PlaySpec, limit: int) -> DiscoveryResult: ...

    async def resolve_domain(self, company_name: str, *, ctx_key: str) -> DomainCandidates: ...

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> SourceBundle: ...


@dataclass
class ProviderBundle:
    llm: LLMProvider
    search: SearchProvider
    # v2 §Part 4 — `None` (the default) means no enrichment provider is
    # wired for this run (Live Mode until V2-D lands Apollo, or
    # `ENRICHMENT_ENABLED=false`): `engine/enrichment.py::call_enrichment()`
    # treats that as NOT_ATTEMPTED, zero provider calls — never a silent
    # fallback to `DemoEnrichmentProvider` (the same "no Live -> fixture
    # fallback" invariant `llm`/`search` already honor).
    enrichment: "EnrichmentProvider | None" = None
