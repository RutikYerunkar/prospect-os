"""Provider Protocols (IMPLEMENTATION_PLAN.md §11).

Demo Mode and Live Mode share the identical pipeline, steps, retries,
`ProspectContext`, DB writes, event stream, scoring arithmetic, review checks
and evaluation — only the object satisfying these Protocols differs. Live
implementations (`OpenAILLMProvider`, `TavilySearchProvider`) are P1 and do
not exist yet; only `providers/demo/*` is built in Checkpoint B.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

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


class ProviderError(Exception):
    """Base for exceptions provider implementations raise."""


class ProviderTimeout(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class SchemaViolation(ProviderError):
    """Raised when a provider's raw output fails Pydantic validation."""


FAILURE_TYPES: dict[str, type[ProviderError]] = {
    "ProviderTimeout": ProviderTimeout,
    "ProviderUnavailable": ProviderUnavailable,
    "SchemaViolation": SchemaViolation,
}


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


class LLMResult(BaseModel):
    data: dict[str, Any]
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0


class LLMProvider(Protocol):
    name: str

    async def structured(
        self, envelope: PromptEnvelope, schema: type[T], *, ctx_key: str
    ) -> LLMResult: ...


class SearchProvider(Protocol):
    name: str

    async def discover(self, spec: PlaySpec, limit: int) -> list[CompanySeed]: ...

    async def fetch_sources(self, company: CompanySeed, *, ctx_key: str) -> list[SourceDocument]: ...


@dataclass
class ProviderBundle:
    llm: LLMProvider
    search: SearchProvider
    provider_semaphores: dict[str, Any] = field(default_factory=dict)
