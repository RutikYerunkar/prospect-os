"""Grounding — verifying a claim is actually supported by its cited evidence.

Pure string/set operations, no I/O. This is what lets the Hybrid signal
detection in §9 work: an LLM proposes `{claim, evidence_id}`, and this module
deterministically confirms the claim's tokens actually occur in the cited
evidence's snippet before it's allowed to carry weight anywhere downstream
(scoring, review's `claim_grounding` check).

v2.0.1 note (`date_claim_supported()`): `SourceDocument.published_at` (the
provider's metadata timestamp for when a source was published) must never be
read by, or passed into, this module's date-grounding check. It is a
different fact from a claimed *event* date (e.g. when a funding round was
announced) and is never served to the extraction LLM in the first place
(`prompts/research_extraction.py::ResearchSourceInput` carries only
`ref`/`title`/`text`) — licensing an event date from it here would silently
reopen exactly the "infer, don't verify" gap this check exists to close.
"""

from __future__ import annotations

import re
from datetime import date

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


def tokenize(text: str) -> set[str]:
    """Public alias of `_tokens()` (H2 post-smoke) — lets callers outside
    this module (e.g. `domain/discovery.py`'s company-name support check)
    build their own token-overlap-style comparisons from the exact same
    normalization this module's own `token_overlap()` uses, rather than
    re-implementing tokenization a second time."""
    return _tokens(text)


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


_MONTH_NAMES: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_NAME_ALT = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True))

# Fully-specified (year + month + day) textual date forms only — a bare
# year or a bare "March 2024" is deliberately never enough (see
# `date_claim_supported()`'s docstring).
_DATE_PATTERNS = (
    # "March 15, 2024" / "March 15th 2024" / "Mar. 15 2024"
    re.compile(
        rf"\b({_MONTH_NAME_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.IGNORECASE
    ),
    # "15 March 2024" / "15th of March, 2024"
    re.compile(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAME_ALT})\.?,?\s+(\d{{4}})\b", re.IGNORECASE
    ),
    # ISO "2024-03-15"
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    # "03/15/2024" (US month/day/year)
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
)


def _dates_in_text(text: str) -> set[date]:
    """Every fully-specified calendar date literally spelled out in `text`,
    in any of a few common written forms. Deliberately conservative: a
    match that fails `date()` construction (e.g. "February 30") is simply
    not a date, not an error."""
    found: set[date] = set()

    for match in _DATE_PATTERNS[0].finditer(text):
        month_name, day, year = match.groups()
        month = _MONTH_NAMES[month_name.lower()]
        try:
            found.add(date(int(year), month, int(day)))
        except ValueError:
            continue

    for match in _DATE_PATTERNS[1].finditer(text):
        day, month_name, year = match.groups()
        month = _MONTH_NAMES[month_name.lower()]
        try:
            found.add(date(int(year), month, int(day)))
        except ValueError:
            continue

    for match in _DATE_PATTERNS[2].finditer(text):
        year, month, day = match.groups()
        try:
            found.add(date(int(year), int(month), int(day)))
        except ValueError:
            continue

    for match in _DATE_PATTERNS[3].finditer(text):
        month, day, year = match.groups()
        try:
            found.add(date(int(year), int(month), int(day)))
        except ValueError:
            continue

    return found


def date_claim_supported(snippet: str, claimed_date: date, reference_date: date) -> bool:
    """v2.0.1 Live-Quality Hardening — DATE PROVENANCE. An event date
    (e.g. a funding announcement's `announced_at`) survives only when:

    1. It is not in the future relative to `reference_date` — a claimed
       date the pipeline itself hasn't reached yet can never be real; and
    2. The exact claimed date is present, fully spelled out (year + month
       + day, in one of a few common written forms), in the cited
       evidence's own snippet text.

    A snippet that only implies a date loosely — a bare year, a
    month-and-year with no day, relative language like "last month" or
    "recently," or `SourceDocument.published_at` (deliberately never
    consulted here — see `domain/grounding.py`'s v2.0.1 module note) —
    never satisfies this check. The caller (`engine/steps/signals.py`) is
    responsible for setting the field to `None` rather than inferring or
    repairing it when this returns `False`.
    """
    if claimed_date > reference_date:
        return False
    return claimed_date in _dates_in_text(snippet)


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
