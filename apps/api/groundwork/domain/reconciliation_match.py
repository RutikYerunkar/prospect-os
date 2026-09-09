"""Pure candidate-matching predicate for historyId-based Live `EMAIL_SEND`
reconciliation (V2-I-b correction, post-smoke).

The original design compared a Gmail message's `Message-ID`/
`X-Google-Original-Message-ID` headers against our own generated
`message_id_header` (`domain/message_id.py::message_id_matches`). The one
authorized real Gmail smoke disproved that: the actually-sent message
carried NEITHER header. `message_id_header` may still be generated and
persisted for historical/audit purposes (`api/live_send_orchestration.py`
still writes it), but reconciliation correctness no longer depends on Gmail
preserving it.

This module never contains a provider's name and never performs I/O
(CLAUDE.md's standing `domain/` purity invariant — no imports from
`providers/`/`repositories/`) — `canonicalize_subject`/`candidate_matches`
are ordinary pure functions over already-fetched header strings and
already-known expected values. The caller (`providers/live/gmail_send.py`)
fetches candidate messages via `users.history.list` +
`messages.get(format="metadata")` and calls `candidate_matches` once per
candidate; it never contains matching business logic itself.

A candidate matches only if ALL of: canonicalized Subject equality, To
normalizes to the expected recipient identity, From normalizes to the
expected sender identity, and Date parses AND falls within the caller-
supplied `[window_start, window_end]` window. Any missing, unparseable, or
unnormalizable field fails the match — never a partial match, never a
guess. "Candidate belongs to the relevant SENT history change" (the fifth
rule in the accepted design) is enforced structurally by the caller, not
here: every candidate this function is ever called with already came from
a `history.list(labelId="SENT", historyTypes=["messageAdded"])` entry.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.header import decode_header
from email.utils import parseaddr, parsedate_to_datetime

from groundwork.domain.contact_identity import InvalidEmailIdentity, normalize_email_identity

_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_subject(raw: str | None) -> str | None:
    """RFC 2047 MIME-decode (Gmail may return `Subject` as an encoded word,
    e.g. `=?UTF-8?B?...?=`, even when the approved draft subject was plain
    text) then collapse/trim whitespace. Deliberately NOT naive raw-string
    equality — an encoding or whitespace difference must never cause a
    false NOT_FOUND for a subject that is, semantically, the same string.
    `None` in, `None` out — never coerced to `""` (which would otherwise
    wrongly equal another genuinely-empty subject)."""
    if raw is None:
        return None
    try:
        decoded_parts = decode_header(raw)
    except (ValueError, UnicodeDecodeError):
        decoded_parts = [(raw, None)]
    pieces: list[str] = []
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            try:
                pieces.append(part.decode(encoding or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                pieces.append(part.decode("utf-8", errors="replace"))
        else:
            pieces.append(part)
    joined = "".join(pieces)
    return _WHITESPACE_RE.sub(" ", joined).strip()


def _identity_key_or_none(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return normalize_email_identity(raw)
    except InvalidEmailIdentity:
        return None


def candidate_matches(
    *,
    expected_subject: str | None,
    expected_recipient_identifier: str | None,
    expected_sender_identifier: str | None,
    candidate_subject: str | None,
    candidate_to: str | None,
    candidate_from: str | None,
    candidate_date: str | None,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """`candidate_to`/`candidate_from` are RFC 5322 address fields (e.g.
    `"Name" <addr@example.com>`) — the bare address is extracted via
    stdlib `email.utils.parseaddr` before normalization, on both the
    candidate AND the expected side, so a display-name difference can
    never cause a false negative or positive."""
    expected_subject_canon = canonicalize_subject(expected_subject)
    if expected_subject_canon is None:
        return False
    if canonicalize_subject(candidate_subject) != expected_subject_canon:
        return False

    expected_recipient_key = _identity_key_or_none(expected_recipient_identifier)
    expected_sender_key = _identity_key_or_none(expected_sender_identifier)
    if expected_recipient_key is None or expected_sender_key is None:
        return False

    _, to_addr = parseaddr(candidate_to or "")
    _, from_addr = parseaddr(candidate_from or "")
    if _identity_key_or_none(to_addr) != expected_recipient_key:
        return False
    if _identity_key_or_none(from_addr) != expected_sender_key:
        return False

    if not candidate_date:
        return False
    try:
        parsed_date = parsedate_to_datetime(candidate_date)
    except (TypeError, ValueError):
        return False
    if parsed_date.tzinfo is None:
        # RFC 2822 dates should carry a numeric zone offset; treat a
        # timezone-naive parse as unparseable-for-comparison rather than
        # guessing a zone.
        return False
    return window_start <= parsed_date <= window_end
