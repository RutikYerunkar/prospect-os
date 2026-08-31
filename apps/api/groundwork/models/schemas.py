"""Pydantic domain/API schemas.

These are pure data structures — no I/O, no provider or repository imports — so
`domain/` (and everything else) can depend on them freely without violating the
domain-purity invariant in CLAUDE.md.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from groundwork.models.enums import (
    ContactVerification,
    EvidenceOrigin,
    ProspectStage,
    ProspectStatus,
    ReviewVerdict,
    SignalType,
)


class CompanySeed(BaseModel):
    """A company as discovered, before any research has happened."""

    slug: str
    name: str
    domain: str
    industry: str
    size_band: str
    employee_count: int
    hq_country: str = "US"
    description: str = ""


class PlaySpec(BaseModel):
    """Parsed criteria for a Play. Read-only once a Run starts."""

    objective_text: str = Field(max_length=2000)
    target_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    adjacent_industries: dict[str, list[str]] = Field(default_factory=dict)
    size_band_min: int = 1
    size_band_max: int = 5000
    target_funding_stages: list[str] = Field(default_factory=list)
    target_technologies: list[str] = Field(default_factory=list)
    persona_titles: list[str] = Field(default_factory=list)
    min_score: int = 60
    min_confidence: float = 0.6
    # Default of 7 mirrors the demo fixture pack's own canonical size
    # (6 required companies + the optional Sable Compute fixture, §23) so a
    # play created with no override discovers the same set the fixture pack
    # documents — never a UI/backend count that silently disagrees with what
    # Demo Mode actually returns.
    target_count: int = Field(default=7, le=25)


class Evidence(BaseModel):
    """A single piece of evidence, scoped to exactly one prospect.

    §12 provenance invariant: only LIVE_FETCH evidence may carry an http(s)
    source_url. Synthetic (DEMO_FIXTURE) and model-asserted (LLM_INFERENCE)
    evidence must never look like a real, clickable source — enforced here,
    structurally, rather than as a UI convention.
    """

    id: str
    prospect_id: str
    source_url: str | None = None
    source_ref: str | None = None
    source_provider: str
    title: str
    claim: str
    snippet: str
    signal_type: SignalType | None = None
    retrieved_at: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    origin: EvidenceOrigin

    @model_validator(mode="after")
    def _no_fake_sources(self) -> "Evidence":
        if self.origin is not EvidenceOrigin.LIVE_FETCH and self.source_url is not None:
            raise ValueError("only LIVE_FETCH evidence may carry an http(s) source_url")
        if self.origin is EvidenceOrigin.LIVE_FETCH and not (
            self.source_url and self.source_url.startswith(("http://", "https://"))
        ):
            raise ValueError("LIVE_FETCH evidence must carry an http(s) source_url")
        return self


class FundingEvent(BaseModel):
    stage: str
    amount_usd: float | None = None
    announced_at: date | None = None
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class HiringRole(BaseModel):
    title: str
    is_gtm: bool
    posted_at: date | None = None
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TechMention(BaseModel):
    name: str
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class LeadershipCandidate(BaseModel):
    full_name: str | None = None
    title: str
    is_persona_match: bool = False
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchFacts(BaseModel):
    company: CompanySeed
    funding_events: list[FundingEvent] = Field(default_factory=list)
    hiring_roles: list[HiringRole] = Field(default_factory=list)
    tech_mentions: list[TechMention] = Field(default_factory=list)
    leadership: list[LeadershipCandidate] = Field(default_factory=list)


class Signal(BaseModel):
    id: str
    prospect_id: str
    type: SignalType
    summary: str
    occurred_at: date | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    grounded: bool = True


class DimensionScore(BaseModel):
    name: str
    raw: float
    weight: float
    contribution: float
    evidence_ids: list[str] = Field(default_factory=list)
    unsupported: bool = False


class ScoreModifier(BaseModel):
    name: str
    reason: str
    detail: str = ""


class ICPScore(BaseModel):
    prospect_id: str
    overall: int
    dimensions: list[DimensionScore]
    modifiers: list[ScoreModifier] = Field(default_factory=list)
    disqualified: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    rubric_version: str = "v1"
    explanation: str = ""


class Contact(BaseModel):
    prospect_id: str
    full_name: str | None = None
    title: str | None = None
    persona_match: bool = False
    linkedin_url: str | None = None
    email: str | None = None
    verification: ContactVerification
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimMapEntry(BaseModel):
    sentence: str
    evidence_ids: list[str] = Field(default_factory=list)


class OutreachDraft(BaseModel):
    prospect_id: str
    channel: str = "email"
    step_index: int = 0
    subject: str
    body: str
    claim_map: list[ClaimMapEntry] = Field(default_factory=list)
    version: int = 1


class ReviewCheck(BaseModel):
    id: str
    passed: bool
    severity: Literal["hard", "soft"]
    detail: str
    evidence_refs: list[str] = Field(default_factory=list)


class ReviewResult(BaseModel):
    prospect_id: str
    verdict: ReviewVerdict
    checks: list[ReviewCheck]
    reasons: list[str] = Field(default_factory=list)


class ProspectOutcome(BaseModel):
    """The final, engine-computed result for one prospect in a run."""

    prospect_id: str
    company: CompanySeed
    status: ProspectStatus
    stage: ProspectStage
    duplicate_of: str | None = None
    dedupe_key: str
    score: ICPScore | None = None
    contact: Contact | None = None
    drafts: list[OutreachDraft] = Field(default_factory=list)
    review: ReviewResult | None = None
    evidence_count: int = 0
    error: str | None = None
