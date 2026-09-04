"""The seven deterministic review checks (IMPLEMENTATION_PLAN.md §14).

No LLM anywhere in this path — the model that wrote a draft is the worst
possible grader of that draft. Every check is a join or a string operation
over already-validated structured data.
"""

from __future__ import annotations

import re

from groundwork.domain.contact_identity import (
    InvalidEmailIdentity,
    linkedin_identifier_key,
    normalize_email_identity,
)
from groundwork.domain.grounding import DEFAULT_OVERLAP_THRESHOLD, verify_claim_evidence
from groundwork.models.enums import Channel, LinkedInIdentityState, ReviewVerdict
from groundwork.models.schemas import ContactChannelState, Evidence, ICPScore, OutreachDraft, ReviewCheck, ReviewResult

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


#: v2 §V2-F — tokens shaped like the two identifier classes a draft could
#: leak. Deliberately permissive shapes (over-matching is safe: every match
#: is then normalized/validated through the same `domain/contact_identity.py`
#: helpers the identifiers themselves were validated with, so a shape-only
#: false positive on a non-identifier string cannot occur here).
_EMAIL_TOKEN_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_LINKEDIN_TOKEN_RE = re.compile(r"https?://(?:[a-z0-9\-]+\.)*linkedin\.com/in/[A-Za-z0-9\-_%]{1,120}/?", re.IGNORECASE)


def _no_fabricated_contact(contact_channels: list[ContactChannelState], drafts: list[OutreachDraft]) -> ReviewCheck:
    """v2 §V2-F rewrite (Part 6). Replaces the v1 `contact.verification`-based
    check — that enum is the person-identity axis and says nothing about
    reachability (§3.1) — with three provenance-based clauses, none of which
    ever reads `contact.verification`:

    1. every identifier on `contact_channels` must resolve to a real provider
       observation (`derived_from_enrichment_id is not None`);
    2. a LinkedIn identifier with `identity_match_state == MISMATCH` hard-fails
       even though it may have produced a draft (personalize's eligibility
       gate is RESOLVED-only — MISMATCH is a policy question, not a drafting
       one, and is caught here instead);
    3. no email- or `linkedin.com/in/`-shaped token may appear in any draft
       unless it normalizes/canonicalizes to one of this prospect's own
       provider-observed identifiers — the deterministic backstop for D3.
    """
    bad: list[str] = []

    for ch in contact_channels:
        if ch.identifier is not None and ch.derived_from_enrichment_id is None:
            bad.append(f"{ch.channel.value} identifier present with no provider observation behind it")

    for ch in contact_channels:
        if ch.channel is Channel.LINKEDIN and ch.identity_match_state == LinkedInIdentityState.MISMATCH.value:
            bad.append("linkedin identity_match_state is MISMATCH")

    own_emails: set[str] = set()
    own_linkedin_keys: set[str] = set()
    for ch in contact_channels:
        if ch.identifier is None:
            continue
        if ch.channel is Channel.EMAIL:
            try:
                own_emails.add(normalize_email_identity(ch.identifier))
            except InvalidEmailIdentity:
                continue
        elif ch.channel is Channel.LINKEDIN:
            key = linkedin_identifier_key(ch.identifier)
            if key is not None:
                own_linkedin_keys.add(key)

    for draft in drafts:
        text = f"{draft.subject or ''}\n{draft.body}"
        for token in _EMAIL_TOKEN_RE.findall(text):
            try:
                normalized = normalize_email_identity(token)
            except InvalidEmailIdentity:
                bad.append(f"malformed email-shaped token in draft: {token}")
                continue
            if normalized not in own_emails:
                bad.append(f"unbacked email identifier in draft: {token}")
        for token in _LINKEDIN_TOKEN_RE.findall(text):
            key = linkedin_identifier_key(token)
            if key is None or key not in own_linkedin_keys:
                bad.append(f"unbacked linkedin identifier in draft: {token}")

    passed = not bad
    detail = (
        "every identifier in contact_channels and every draft is backed by this "
        "prospect's own provider-observed identifiers"
        if passed
        else f"{len(bad)} fabricated/unbacked/mismatched identifier issue(s): {bad[:3]}"
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
        # v2 §V2-F bug fix: a LinkedIn draft's `subject` is `None` — an
        # f-string interpolates that as the literal text "None", which would
        # then be scanned (and could coincidentally match an identifier).
        # `draft.subject or ""` keeps a null subject contributing nothing to
        # the scanned text, exactly like an empty one always has.
        text = f"{draft.subject or ''}\n{draft.body}"
        for identifier in other_identifiers:
            if identifier and _identifier_pattern(identifier).search(text):
                leaked.append(identifier)
    passed = not leaked
    detail = "no other prospect's name or domain found in outreach" if passed else (
        f"draft references another prospect in this run: {leaked[:3]}"
    )
    return ReviewCheck(id="cross_prospect_leak", passed=passed, severity="hard", detail=detail)


def _no_placeholders(drafts: list[OutreachDraft]) -> ReviewCheck:
    """v2 §V2-F: the empty-subject clause applies only to channels carrying a
    subject (EMAIL) — a LinkedIn draft's `subject is None` is its normal,
    complete shape, not a placeholder-style omission. Body-empty stays
    universal. The bracket/angle-bracket placeholder patterns are unchanged."""
    hits: list[str] = []
    for draft in drafts:
        if not draft.body.strip():
            hits.append("empty body")
            continue
        if draft.channel is Channel.EMAIL and not (draft.subject or "").strip():
            hits.append("empty subject")
            continue
        text = f"{draft.subject or ''}\n{draft.body}".lower()
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
    contact_channels: list[ContactChannelState],
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
        _no_fabricated_contact(contact_channels, drafts),
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
