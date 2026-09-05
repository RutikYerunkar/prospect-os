"""Structured-output schemas for LLM-driven steps.

Every LLM call in the pipeline (demo or live) returns one of these, validated
by Pydantic. Unparseable output is a retryable step failure — it never reaches
scoring or review, per the §26 "output schema validation" security control.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.models.schemas import ClaimMapEntry, ResearchFacts


class ResearchExtractionOutput(BaseModel):
    """Research Agent output: unstructured source documents -> ResearchFacts.

    This is also where the "LLM proposes" half of the §9 Hybrid signal
    detection lives: each fact carries the claim text the model extracted
    plus a `source_ref`. `engine/steps/signals.py` is the deterministic
    verifier — it confirms each claim's tokens actually occur in the cited
    source before the fact is allowed to carry any evidence_ids forward.
    """

    facts: ResearchFacts


class PersonalizationOutput(BaseModel):
    """Personalization Agent output: outreach copy grounded in claim_map."""

    subject: str
    body: str
    claim_map: list[ClaimMapEntry] = Field(default_factory=list)


class LinkedInOutreachOutput(BaseModel):
    """v2 §V2-F — LinkedIn Personalization Agent output. Deliberately has no
    `subject` (a LinkedIn message has none) — this is a distinct schema from
    `PersonalizationOutput`, not a variant of it, produced by a distinct LLM
    operation (`LLMOperation.LINKEDIN_PERSONALIZATION`)."""

    body: str
    claim_map: list[ClaimMapEntry] = Field(default_factory=list)


class ScoreExplanationOutput(BaseModel):
    """The LLM writes this prose *from* the already-computed score numbers.

    It cannot change `overall` or any dimension value — those are pure
    arithmetic from `domain/scoring.py`. This call only produces the
    sentence underneath the table.
    """

    explanation: str


class ObjectiveParseOutput(BaseModel):
    """Objective Parser output (Checkpoint G Phase 9): NL objective ->
    inferred `PlaySpec` *criteria only*. Deliberately does not ask the model
    to echo `objective_text` or `target_count` — those are never ambiguous,
    so there is nothing for a model to add by restating them, and echoing
    them would just be another surface for drift between what the user
    typed and what a model claims they typed.

    Every field is optional/defaultable: a field the model omits keeps the
    caller's own default or explicit user override — `parse_objective()`
    layers this output *under* user overrides, never on top of them.
    """

    target_industries: list[str] = Field(default_factory=list)
    excluded_industries: list[str] = Field(default_factory=list)
    target_funding_stages: list[str] = Field(default_factory=list)
    target_technologies: list[str] = Field(default_factory=list)
    persona_titles: list[str] = Field(default_factory=list)
    size_band_min: int | None = None
    size_band_max: int | None = None
    min_score: int | None = None
    min_confidence: float | None = None


class DiscoveryCandidate(BaseModel):
    """One candidate company the model believes the served search-result
    excerpts describe. `company_name` is a DISPLAY LABEL ONLY — never a
    domain, URL, or provider id (the model is never shown any of those).
    `supporting_result_refs` must be a subset of the refs actually served
    this call; the server drops any candidate that cites a ref it wasn't
    served, or whose name isn't textually supported by the cited
    excerpt(s) — see `domain/discovery.py`.
    """

    company_name: str
    supporting_result_refs: list[str] = Field(default_factory=list)


class DiscoveryExtractionOutput(BaseModel):
    """H2 Stage B (LLMOperation.DISCOVERY_EXTRACTION): bounded search-result
    excerpts -> candidate company names. The model never sees a URL,
    domain, provider result id, or search query — only opaque refs and
    excerpt text."""

    candidates: list[DiscoveryCandidate] = Field(default_factory=list)


class DomainSelectionOutput(BaseModel):
    """H2 Stage C ambiguous-fallback (LLMOperation.DOMAIN_SELECTION): pick
    the one served domain-resolution candidate ref that is this company's
    own official site, or `null` if none of them plausibly is. The model
    may select ONLY from refs it was actually served — never author a URL
    or domain itself; `null` is a legitimate, expected answer, not an
    error."""

    selected_candidate_ref: str | None = None
