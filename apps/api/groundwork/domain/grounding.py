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


MIN_PLAUSIBLE_EMPLOYEE_COUNT = 1
MAX_PLAUSIBLE_EMPLOYEE_COUNT = 10_000_000

_NUMBER_RE = re.compile(r"(\d[\d,]*)\s*([kK])?")


def _numbers_in_text(text: str) -> set[int]:
    """Every plausible integer literal mentioned in `text`, honoring
    thousands separators (`"1,200"`) and `k`/`K` shorthand (`"1.2k"` is not
    handled — only integer-anchored shorthand like `"140"`/`"1,400"`/`"12k"`
    — deliberately conservative rather than guessing at fractional forms)."""
    numbers: set[int] = set()
    for match in _NUMBER_RE.finditer(text):
        digits = match.group(1).replace(",", "")
        if not digits:
            continue
        value = int(digits)
        if match.group(2):
            value *= 1000
        numbers.add(value)
    return numbers


def numeric_claim_supported(snippet: str, claimed_count: int) -> bool:
    """H1 Phase 6 — NUMERIC PROVENANCE. An employee-count claim survives
    only when the exact claimed integer is actually present as a number in
    the cited evidence's text — never inferred from vague prose like
    `"large team"` or `"hundreds of employees"` (neither contains a parsable
    number, so both correctly fail this check). Also rejects nonsensical or
    out-of-range counts outright, independent of what the text says.
    """
    if claimed_count < MIN_PLAUSIBLE_EMPLOYEE_COUNT or claimed_count > MAX_PLAUSIBLE_EMPLOYEE_COUNT:
        return False
    return claimed_count in _numbers_in_text(snippet)


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
