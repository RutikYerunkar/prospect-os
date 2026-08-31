"""Grounding — verifying a claim is actually supported by its cited evidence.

Pure string/set operations, no I/O. This is what lets the Hybrid signal
detection in §9 work: an LLM proposes `{claim, evidence_id}`, and this module
deterministically confirms the claim's tokens actually occur in the cited
evidence's snippet before it's allowed to carry weight anywhere downstream
(scoring, review's `claim_grounding` check).
"""

from __future__ import annotations

import re

from groundwork.models.schemas import Evidence

_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "for",
    "in",
    "is",
    "its",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

DEFAULT_OVERLAP_THRESHOLD = 0.5


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def token_overlap(claim: str, snippet: str) -> float:
    """Fraction of the claim's meaningful tokens that occur in the snippet."""
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return 0.0
    snippet_tokens = _tokens(snippet)
    matched = claim_tokens & snippet_tokens
    return len(matched) / len(claim_tokens)


def is_grounded(
    claim: str, evidence: Evidence, threshold: float = DEFAULT_OVERLAP_THRESHOLD
) -> bool:
    """A claim is grounded when its tokens sufficiently overlap the cited
    evidence's verbatim snippet. Below threshold, the claim must be demoted
    (never silently accepted) — see §9 "Signal detection: Hybrid"."""
    if not claim.strip():
        return False
    return token_overlap(claim, evidence.snippet) >= threshold


def verify_claim_evidence(
    claim: str,
    evidence_id: str | None,
    evidence_by_id: dict[str, Evidence],
    prospect_id: str,
    threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> bool:
    """Full grounding check used by review check #1 (`claim_grounding`):
    the evidence id must exist, belong to *this* prospect, and its snippet
    must support the claim."""
    if not evidence_id:
        return False
    evidence = evidence_by_id.get(evidence_id)
    if evidence is None:
        return False
    if evidence.prospect_id != prospect_id:
        return False
    return is_grounded(claim, evidence, threshold)
