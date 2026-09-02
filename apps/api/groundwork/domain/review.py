"""The seven deterministic review checks (IMPLEMENTATION_PLAN.md §14).

No LLM anywhere in this path — the model that wrote a draft is the worst
possible grader of that draft. Every check is a join or a string operation
over already-validated structured data.
"""

from __future__ import annotations

import re

from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, verify_claim_evidence
from groundwork.models.enums import ContactVerification, ReviewVerdict
from groundwork.models.schemas import Contact, Evidence, ICPScore, OutreachDraft, ReviewCheck, ReviewResult

#: Production bug (post-Checkpoint-real-Live-Mode-run): the prior pattern set only
#: matched the single literal string `[company]`, so any other bracket placeholder —
#: `[Your Name]`, `[First Name]`, `[Company Name]` — sailed through as PASS. These
#: patterns are matched against the already-lowercased subject+body text (see
#: `_no_placeholders`), so every character class here is lowercase-only; they cover
#: the general *shapes* a template placeholder takes rather than one literal token:
#: `{{...}}` / `{{ ... }}` double-brace tokens, `<...>` angle-bracket tokens, and
#: `[...]` square-bracket tokens whose contents start with a letter (so `[1]`- or
#: `[2024]`-style numeric citation/footnote brackets are not flagged).
_PLACEHOLDER_PATTERNS = (
    r"\{\{\s*[a-z0-9_. -]+?\s*\}\}",
    r"<[a-z][a-z0-9_ ]{0,40}>",
    r"\[[a-z][a-z0-9_' -]{0,40}\]",
    r"\btodo\b",
)

SCORE_SUPPORT_UNSUPPORTED_THRESHOLD = 2


def _claim_grounding(
    prospect_id: str, drafts: list[OutreachDraft], evidence_by_id: dict[str, Evidence], threshold: float
) -> ReviewCheck:
    bad: list[str] = []
    refs: list[str] = []
    for draft in drafts:
        for entry in draft.claim_map:
            if not entry.evidence_ids:
                bad.append(entry.sentence)
                continue
            for eid in entry.evidence_ids:
                refs.append(eid)
                if not verify_claim_evidence(entry.sentence, eid, evidence_by_id, prospect_id, threshold):
                    bad.append(entry.sentence)
    passed = not bad
    detail = "all outreach claims resolve to grounded, in-scope evidence" if passed else (
        f"{len(bad)} claim(s) cite missing, foreign, or unsupported evidence: {bad[:3]}"
    )
    return ReviewCheck(id="claim_grounding", passed=passed, severity="hard", detail=detail, evidence_refs=refs)


def _no_fabricated_contact(contact: Contact | None) -> ReviewCheck:
    has_contact_detail = bool(contact and (contact.email or contact.linkedin_url))
    verified = bool(contact and contact.verification == ContactVerification.VERIFIED)
    passed = not (has_contact_detail and not verified)
    detail = (
        "no contact detail present without verification"
        if passed
        else "an email or LinkedIn URL is present but the contact is not VERIFIED"
    )
    return ReviewCheck(id="no_fabricated_contact", passed=passed, severity="hard", detail=detail)


def _identifier_pattern(identifier: str) -> re.Pattern[str]:
    """Word/token-boundary-aware match for one cross-prospect identifier.

    A plain `identifier.lower() in text` substring check (the pre-H1
    behavior) hard-fails on real short company names — `"Ramp"` inside
    `"...the momentum is really cRAMPing..."`, `"Box"` inside `"mailbox"`,
    `"Arc"` inside `"March"` — purely because the character sequence occurs,
    with no actual reference to that prospect. `(?<!\\w)...(?!\\w)` requires
    a non-word (or string-boundary) character on both sides, so the
    identifier must appear as its own token/phrase, not embedded inside a
    longer word. Domain identifiers (`acme.com`) are unaffected — `.`/`-`
    are already non-word characters, so a real domain reference still
    matches. Real cross-prospect leaks — the identifier used as itself, at a
    word boundary — are still caught; see `tests/test_review.py`'s
    regression cases for both directions.
    """
    return re.compile(rf"(?<!\w){re.escape(identifier)}(?!\w)", re.IGNORECASE)


def _cross_prospect_leak(drafts: list[OutreachDraft], other_identifiers: set[str]) -> ReviewCheck:
    leaked: list[str] = []
    for draft in drafts:
        text = f"{draft.subject}\n{draft.body}"
        for identifier in other_identifiers:
            if identifier and _identifier_pattern(identifier).search(text):
                leaked.append(identifier)
    passed = not leaked
    detail = "no other prospect's name or domain found in outreach" if passed else (
        f"draft references another prospect in this run: {leaked[:3]}"
    )
    return ReviewCheck(id="cross_prospect_leak", passed=passed, severity="hard", detail=detail)


def _no_placeholders(drafts: list[OutreachDraft]) -> ReviewCheck:
    hits: list[str] = []
    for draft in drafts:
        if not draft.subject.strip() or not draft.body.strip():
            hits.append("empty subject/body")
            continue
        text = f"{draft.subject}\n{draft.body}".lower()
        for pattern in _PLACEHOLDER_PATTERNS:
            match = re.search(pattern, text)
            if match:
                hits.append(match.group(0))
    passed = not hits
    detail = "no placeholder tokens or empty fields" if passed else f"placeholder(s) found: {hits[:3]}"
    return ReviewCheck(id="no_placeholders", passed=passed, severity="hard", detail=detail)


def _duplicate_account(dedupe_key: str, other_dedupe_keys: set[str]) -> ReviewCheck:
    passed = dedupe_key not in other_dedupe_keys
    detail = "dedupe key is unique in this run" if passed else "dedupe key collides with an earlier prospect"
    return ReviewCheck(id="duplicate_account", passed=passed, severity="hard", detail=detail)


def _score_support(score: ICPScore) -> ReviewCheck:
    unsupported = [d.name for d in score.dimensions if d.unsupported]
    passed = len(unsupported) <= SCORE_SUPPORT_UNSUPPORTED_THRESHOLD
    detail = (
        f"{len(unsupported)} dimension(s) unsupported (threshold {SCORE_SUPPORT_UNSUPPORTED_THRESHOLD})"
        if not passed
        else f"{len(unsupported)} dimension(s) unsupported, within threshold"
    )
    return ReviewCheck(id="score_support", passed=passed, severity="soft", detail=detail)


def _confidence_floor(score: ICPScore, min_confidence: float) -> ReviewCheck:
    passed = score.confidence >= min_confidence
    detail = f"confidence {score.confidence:.2f} vs floor {min_confidence:.2f}"
    return ReviewCheck(id="confidence_floor", passed=passed, severity="soft", detail=detail)


def run_checks(
    *,
    prospect_id: str,
    evidence: list[Evidence],
    drafts: list[OutreachDraft],
    contact: Contact | None,
    score: ICPScore,
    dedupe_key: str,
    other_dedupe_keys: set[str],
    other_company_identifiers: set[str],
    min_confidence: float,
    grounding_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
) -> ReviewResult:
    evidence_by_id = {e.id: e for e in evidence}

    checks = [
        _claim_grounding(prospect_id, drafts, evidence_by_id, grounding_threshold),
        _no_fabricated_contact(contact),
        _cross_prospect_leak(drafts, other_company_identifiers),
        _no_placeholders(drafts),
        _duplicate_account(dedupe_key, other_dedupe_keys),
        _score_support(score),
        _confidence_floor(score, min_confidence),
    ]

    hard_failures = [c.detail for c in checks if c.severity == "hard" and not c.passed]
    soft_failures = [c.detail for c in checks if c.severity == "soft" and not c.passed]

    if hard_failures:
        verdict = ReviewVerdict.FAIL
        reasons = hard_failures
    elif soft_failures:
        verdict = ReviewVerdict.NEEDS_REVIEW
        reasons = soft_failures
    else:
        verdict = ReviewVerdict.PASS
        reasons = []

    return ReviewResult(prospect_id=prospect_id, verdict=verdict, checks=checks, reasons=reasons)
