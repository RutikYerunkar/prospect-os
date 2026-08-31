"""`ProspectContext` — the isolation boundary (docs/ARCHITECTURE.md).

Every prospect's mutable state — facts, evidence, signals, score, contact,
drafts — lives only here. The executor holds no shared dict of accumulating
results for a step to reach into; steps receive `ctx` and return a
`StepResult`. Prompt envelopes passed to providers are built only from a
single `ctx`, so there is no path for one prospect's request to carry
another's data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from groundwork.models.enums import ProspectStage, ProspectStatus
from groundwork.models.schemas import (
    CompanySeed,
    Contact,
    Evidence,
    ICPScore,
    OutreachDraft,
    PlaySpec,
    ResearchFacts,
    ReviewResult,
    Signal,
)
from groundwork.observability.events import EventEmitter
from groundwork.observability.llm_calls import LLMCallRecorder
from groundwork.observability.trace import TraceRecorder
from groundwork.providers.base import LLMResult, ProviderBundle, make_ctx_key


@dataclass
class ProspectContext:
    run_id: str
    prospect_id: str
    company: CompanySeed
    dedupe_key: str
    play_spec: PlaySpec
    providers: ProviderBundle
    reference_date: date
    trace: TraceRecorder
    events: EventEmitter
    llm_calls: LLMCallRecorder

    # Read-only awareness of the *other* prospects in this run — company
    # names/domains and dedupe keys only, never their evidence, facts, score
    # or drafts. This is what lets the `cross_prospect_leak` runtime
    # guardrail (mechanism #4 of the isolation model) actually check
    # something, without breaking isolation: nothing here is mutable state
    # another prospect's steps could reach into.
    other_dedupe_keys: frozenset[str] = frozenset()
    other_company_identifiers: frozenset[str] = frozenset()

    facts: ResearchFacts | None = None
    evidence: list[Evidence] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    score: ICPScore | None = None
    contact: Contact | None = None
    drafts: list[OutreachDraft] = field(default_factory=list)
    review: ReviewResult | None = None

    stage: ProspectStage = ProspectStage.DISCOVERED
    status: ProspectStatus = ProspectStatus.RUNNING
    error: str | None = None

    # step_name -> {model, provider, tokens_in, tokens_out} for the most
    # recent LLM call that step made this attempt. `engine/step.py` folds
    # this onto the step's `agent_tasks` OK row, then clears it — see
    # `engine/llm.py::call_structured`.
    llm_rollup: dict[str, dict] = field(default_factory=dict)

    def step_key(self, step_name: str) -> str:
        return make_ctx_key(self.run_id, self.prospect_id, step_name)

    def evidence_by_id(self, evidence_id: str) -> Evidence | None:
        return next((e for e in self.evidence if e.id == evidence_id), None)

    def note_llm_call(self, step_name: str, result: LLMResult) -> None:
        self.llm_rollup[step_name] = {
            "model": result.model,
            "provider": result.provider,
            "tokens_in": sum(a.tokens_in for a in result.attempts),
            "tokens_out": sum(a.tokens_out for a in result.attempts),
        }
