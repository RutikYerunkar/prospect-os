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


class ScoreExplanationOutput(BaseModel):
    """The LLM writes this prose *from* the already-computed score numbers.

    It cannot change `overall` or any dimension value — those are pure
    arithmetic from `domain/scoring.py`. This call only produces the
    sentence underneath the table.
    """

    explanation: str
