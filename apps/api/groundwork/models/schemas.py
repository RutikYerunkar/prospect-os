"""Pydantic domain/API schemas.

These are pure data structures — no I/O, no provider or repository imports — so
`domain/` (and everything else) can depend on them freely without violating the
domain-purity invariant in CLAUDE.md.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from groundwork.models.enums import (
    ActionExecutionOrigin,
    Channel,
    ContactVerification,
    DimensionSupport,
    EnrichmentOrigin,
    EvidenceOrigin,
    ExclusionEvaluation,
    ProspectStage,
    ProspectStatus,
    ReviewVerdict,
    SignalType,
    SourceStatus,
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


class SourceDocument(BaseModel):
    """One retrieval occurrence of one source (H1 Phase 9) — provider-neutral,
    persisted one row per occurrence to `source_documents`. Distinct from
    `Evidence`: an `Evidence` row is created only for the deterministic
    *winner* among occurrences sharing the same source identity (H1 Phase
    10, `domain/source_identity.py`) — the same URL returned by three
    different queries persists as three `SourceDocument` rows but
    contributes at most one `Evidence` row.

    A pure data model (importable by `domain/`), not a provider-boundary
    type — `providers/base.py` re-exports it for backward compatibility
    with existing call sites.
    """

    ref: str
    title: str
    claim: str = ""  # Demo Mode only: the fixture's own pre-authored claim text
    text: str
    source_provider: str
    signal_type: str | None = None
    confidence: float = 0.8

    url: str | None = None
    canonical_url: str | None = None
    domain: str | None = None
    publisher: str | None = None
    full_text_length: int | None = None
    content_sha256: str | None = None
    source_type: str = "demo_fixture"
    retrieved_at: datetime | None = None
    published_at: date | None = None
    provider_result_id: str | None = None
    rank: int | None = None
    relevance_score: float | None = None
    extraction_method: str = "fixture"
    status: SourceStatus = SourceStatus.OK
    origin: EvidenceOrigin = EvidenceOrigin.DEMO_FIXTURE
    search_call_id: str | None = None


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


class IndustryProfileFact(BaseModel):
    """An independently-grounded industry classification (H1 Phase 4/5).

    `category` must be a member of the served allowed-category set built
    from this Play's `target_industries ∪ excluded_industries ∪
    adjacent_industries(keys ∪ values) ∪ {"OTHER"}` — see
    `domain/industry.py`. Free-text categories never reach scoring: server
    validation collapses anything outside the served set to `category=None`
    (UNKNOWN). `evidence_ids` is populated ONLY after this fact's own claim
    independently passes deterministic grounding (`engine/steps/signals.py`)
    — never inherited from `EmployeeCountProfileFact`, even when both facts
    happen to cite the same `source_ref`.
    """

    category: str | None = None
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class EmployeeCountProfileFact(BaseModel):
    """An independently-grounded employee-count claim (H1 Phase 4/6).

    Exact count or nothing — H1/H2 never derive a `size_band` range from
    this. `evidence_ids` is populated ONLY after the claimed integer is
    independently verified as numerically present in the cited evidence's
    text (`domain/grounding.numeric_claim_supported`) — never inherited from
    `IndustryProfileFact`.
    """

    employee_count: int | None = None
    claim: str = ""
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class CompanyProfileFacts(BaseModel):
    """Field-level-independent company profile facts (H1 Phase 4).

    `industry` and `employee_count` are deliberately two separate fact
    objects, each with its own `evidence_ids` — a source proving one must
    never automatically prove the other, even when both happen to cite the
    same underlying source.
    """

    industry: IndustryProfileFact = Field(default_factory=IndustryProfileFact)
    employee_count: EmployeeCountProfileFact = Field(default_factory=EmployeeCountProfileFact)


class ResearchFacts(BaseModel):
    company: CompanySeed
    funding_events: list[FundingEvent] = Field(default_factory=list)
    hiring_roles: list[HiringRole] = Field(default_factory=list)
    tech_mentions: list[TechMention] = Field(default_factory=list)
    leadership: list[LeadershipCandidate] = Field(default_factory=list)
    profile: CompanyProfileFacts = Field(default_factory=CompanyProfileFacts)


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
    # `unsupported` is kept as a plain bool for backward compatibility with
    # every existing reader (the `score_support` review check, the frontend
    # `ScoreBreakdown` table) — True for both UNSUPPORTED and UNKNOWN.
    # `support` (H1 Phase 7) is the authoritative tri-state: UNSUPPORTED
    # still counts in the confidence denominator (a claim was checked for
    # and not found); UNKNOWN is excluded from the denominator entirely (the
    # fact was never independently established, so it can neither help nor
    # hurt confidence). `domain/scoring.py` keeps both fields in sync.
    unsupported: bool = False
    support: DimensionSupport = DimensionSupport.SUPPORTED


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
    # Tri-state exclusion-policy evaluation (H1 Phase 7). EXCLUDED implies
    # `disqualified=True`; UNKNOWN means the exclusion policy could not be
    # evaluated at all (industry never grounded) — never silently treated as
    # not-excluded. `engine/runner.py::_derive_final_status` forces
    # NEEDS_REVIEW for UNKNOWN rather than letting an unevaluable exclusion
    # pass silently.
    exclusion_status: ExclusionEvaluation = ExclusionEvaluation.NOT_EXCLUDED


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
    # v2 §V2-F: `channel` is now the closed `Channel` enum (still serializes
    # to the v1 string value "email" — the column type is unchanged). A
    # LinkedIn draft has no subject — `subject` dropped its NOT NULL
    # requirement (Part 5, C5) — `domain/review.py::_no_placeholders`'s
    # empty-subject clause is channel-conditional to match.
    prospect_id: str
    channel: Channel = Channel.EMAIL
    step_index: int = 0
    subject: str | None = None
    body: str
    claim_map: list[ClaimMapEntry] = Field(default_factory=list)
    version: int = 1
    # v2 §3.9/Part 5: populated once an `ActionProposal` is built from this
    # draft (V2-H). Deliberately left `None` here — V2-F never computes it.
    content_hash: str | None = None
    hash_version: str = "v1"


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


# =====================================================================
# v2 — Contact Enrichment & Governed Outbound Action
# (docs/V2_IMPLEMENTATION_PLAN.md, frozen Rev 4, Part 4 / Part 5)
# =====================================================================

# The ONLY grammar a DEMO_FIXTURE LinkedIn identifier may carry — kept here
# (not in `domain/contact_identity.py`) as the single source of truth so the
# model validators below and `domain/contact_identity.py`'s pure derivation
# can never drift apart; `domain/` imports this pattern rather than
# redeclaring it.
DEMO_LINKEDIN_URL_PATTERN = r"^demo://linkedin/[a-z0-9][a-z0-9\-]{0,119}$"
_DEMO_LINKEDIN_URL_RE = re.compile(DEMO_LINKEDIN_URL_PATTERN)

# `action_proposals`/`action_executions` DEMO_SIMULATED sender convention
# (D9, Part 5 validator 4). `.invalid` is IANA-reserved and can never
# resolve, so a demo sender is structurally incapable of being a real
# person's address.
DEMO_SENDER_DOMAIN = "groundwork.invalid"


class ProviderEmailObservation(BaseModel):
    """What the provider ASSERTED. Never a Groundwork verdict (D2) — see
    `domain/contact_identity.py::derive_email_channel` for the pure
    derivation that turns this into `EmailDiscoveryState`/
    `EmailVerificationState`."""

    address: str | None = None
    provider_status: str | None = None  # the provider's own raw word, verbatim
    provider_confidence: float | None = None
    is_catch_all: bool | None = None
    observed_at: datetime


class ProviderLinkedInObservation(BaseModel):
    """What the provider ASSERTED. Never a Groundwork verdict (D2) — see
    `domain/contact_identity.py::derive_linkedin_channel`."""

    profile_url: str | None = None
    asserted_full_name: str | None = None
    asserted_company_name: str | None = None
    asserted_company_domain: str | None = None  # only if the provider supplies it
    asserted_title: str | None = None
    observed_at: datetime


class ContactEnrichment(BaseModel):
    """One successful enrichment observation group (§3.6) — mirrors the
    `contact_enrichments` row grain (Part 5): one row per successful call,
    matched or explicit not-found, both are observations.

    Model validators 1-2 (Part 5, §H) — the `Evidence._no_fake_sources`
    precedent extended to a second identifier class: origin decides which
    LinkedIn identifier shape is structurally legal, enforced here AND
    (independently) by `domain/contact_identity.py::validate_linkedin_identifier`
    — the "secrets are scrubbed twice, not once" discipline.
    """

    prospect_id: str
    provider: str
    call_group_id: str
    matched: bool
    origin: EnrichmentOrigin
    observed_at: datetime
    raw_digest: str

    provider_person_id: str | None = None
    email_address: str | None = None
    email_provider_status: str | None = None
    email_provider_confidence: float | None = None
    email_is_catch_all: bool | None = None

    linkedin_url: str | None = None
    linkedin_asserted_full_name: str | None = None
    linkedin_asserted_company_name: str | None = None
    linkedin_asserted_company_domain: str | None = None
    linkedin_asserted_title: str | None = None

    @model_validator(mode="after")
    def _origin_bound_linkedin_grammar(self) -> "ContactEnrichment":
        if self.linkedin_url is None:
            return self
        if self.origin is EnrichmentOrigin.DEMO_FIXTURE:
            if not _DEMO_LINKEDIN_URL_RE.match(self.linkedin_url):
                raise ValueError(
                    "a DEMO_FIXTURE contact_enrichments row may only carry a "
                    "demo://linkedin/<slug> LinkedIn identifier"
                )
        elif self.origin is EnrichmentOrigin.LIVE_PROVIDER:
            if self.linkedin_url.startswith("demo://"):
                raise ValueError("a LIVE_PROVIDER contact_enrichments row may not carry a demo:// identifier")
        return self


class ContactChannelState(BaseModel):
    """v2 §V2-F — the AUTHORITATIVE post-write state of one (prospect,
    channel) pair, mirroring the `contact_channels` row grain (Part 5).

    This is what `ContactEnrichmentRepository.record_success`/
    `record_failure` return, so every downstream consumer — the
    `EnrichmentCallRecorder`, `engine/enrichment.py::call_enrichment`,
    `ctx.contact_channels`, and finally `domain/review.py::run_checks` —
    reads the same already-derived, already-last-known-good state the
    repository just persisted. Nothing downstream re-derives raw provider
    state or queries the repository again (the frozen plan's explicit
    prohibition): this model IS the hand-off.
    """

    channel: Channel
    identifier: str | None = None
    discovery_state: str | None = None
    verification_state: str | None = None
    identity_match_state: str | None = None
    derivation_version: str | None = None
    derived_from_enrichment_id: str | None = None
    observed_at: datetime | None = None


class ActionProposal(BaseModel):
    """Immutable — mirrors the `action_proposals` row grain (Part 5).
    `sender_identifier`/`recipient_identity_key` are canonical from birth
    (§3.10); `recipient_identifier` stays the display form."""

    prospect_id: str
    run_id: str
    draft_id: str
    action_type: str  # ActionType
    channel: str  # Channel
    sender_identifier: str | None = None
    recipient_identifier: str | None = None
    recipient_identity_key: str | None = None
    content_hash: str
    hash_version: str
    policy_version: str
    policy_verdict: str
    blocked_reasons: list[str] = Field(default_factory=list)
    policy_snapshot: dict = Field(default_factory=dict)
    origin: ActionExecutionOrigin
    created_at: datetime
    superseded_by: str | None = None

    @model_validator(mode="after")
    def _demo_sender_convention(self) -> "ActionProposal":
        if self.origin is ActionExecutionOrigin.DEMO_SIMULATED and self.sender_identifier is not None:
            if not self.sender_identifier.endswith(f"@{DEMO_SENDER_DOMAIN}"):
                raise ValueError(
                    f"a DEMO_SIMULATED action_proposals row's sender_identifier must end in "
                    f"@{DEMO_SENDER_DOMAIN}"
                )
        return self


class ActionExecution(BaseModel):
    """Mirrors the `action_executions` row grain (Part 5)."""

    action_proposal_id: str
    prospect_id: str
    run_id: str
    action_type: str  # ActionType
    status: str  # ActionExecutionStatus
    idempotency_key: str
    origin: ActionExecutionOrigin
    approval_id: str | None = None
    provider: str | None = None
    recipient_identity_key: str | None = None
    sender_identifier: str | None = None
    message_id_header: str | None = None
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    executor_id: str | None = None
    dispatched: bool = False
    outcome_class: str | None = None
    attempt_count: int = 0
    reconcile_attempts: int = 0
    messages_scanned: int = 0
    claimed_at: datetime | None = None
    dispatched_at: datetime | None = None
    settled_at: datetime | None = None
    reconciled_at: datetime | None = None
    last_error_type: str | None = None
    last_error_message: str | None = None

    @model_validator(mode="after")
    def _demo_provider_message_id_convention(self) -> "ActionExecution":
        if (
            self.origin is ActionExecutionOrigin.DEMO_SIMULATED
            and self.provider_message_id is not None
            and not self.provider_message_id.startswith("demo://")
        ):
            raise ValueError(
                "a DEMO_SIMULATED action_executions row's provider_message_id must start with demo://"
            )
        return self
