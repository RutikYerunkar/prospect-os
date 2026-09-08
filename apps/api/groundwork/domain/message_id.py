"""Our own RFC 5322 `Message-ID` — generated and persisted BEFORE dispatch
(V2-I-b, Phase 4). Pure, no I/O.

```
local  = sha256(execution_id | idempotency_key)
domain = validated registrable domain of the connected Gmail account
```

There is deliberately no fallback host: if the connected account's domain
does not resolve to a valid registrable domain (`domain/psl.py`), generation
fails closed rather than inventing a placeholder host that would make the
header meaningless for reconciliation.

Gmail is not guaranteed to preserve our header verbatim — some deployments
echo it back under `X-Google-Original-Message-ID` instead of (or alongside)
`Message-ID`. Reconciliation (§3.3) must therefore compare a candidate
message's headers against BOTH, with only conservative normalization
(whitespace, angle-bracket presence) — never assume which one a given
message will carry.
"""

from __future__ import annotations

import hashlib

from groundwork.domain.psl import canonical_domain


class InvalidSenderDomain(ValueError):
    """The connected Gmail account's domain does not resolve to a valid
    registrable domain — generation fails closed; there is no fallback
    host."""


def generate_message_id_header(*, execution_id: str, idempotency_key: str, sender_identifier: str) -> str:
    """`sender_identifier` is the connected account's own (canonical) email
    address — only its domain part is used, and only after validation
    through the same PSL-aware normalization the rest of the codebase uses
    for a company identity (never a second, hand-rolled domain parser)."""
    if "@" not in sender_identifier:
        raise InvalidSenderDomain(f"sender_identifier has no domain part: {sender_identifier!r}")
    raw_domain = sender_identifier.rsplit("@", 1)[-1]
    domain = canonical_domain(raw_domain)
    if not domain:
        raise InvalidSenderDomain(
            f"sender domain {raw_domain!r} does not resolve to a valid registrable domain"
        )
    local = hashlib.sha256(f"{execution_id}|{idempotency_key}".encode("utf-8")).hexdigest()
    return f"<{local}@{domain}>"


def _normalize_header_value(value: str | None) -> str | None:
    """Conservative normalization only — strip surrounding whitespace,
    normalize the presence of angle brackets. Never case-folds (a
    `Message-ID` local part is a hex digest here, but this function must not
    assume that about a value read back from Gmail) and never strips
    internal content."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v.startswith("<") and v.endswith(">"):
        v = v[1:-1]
    return v.strip() or None


def message_id_matches(expected_header: str, *, message_id: str | None, x_google_original_message_id: str | None) -> bool:
    """True iff EITHER observed header, after conservative normalization,
    matches our generated header. Never assumes Gmail preserves the header
    under `Message-ID` specifically."""
    expected = _normalize_header_value(expected_header)
    if expected is None:
        return False
    for candidate in (message_id, x_google_original_message_id):
        if _normalize_header_value(candidate) == expected:
            return True
    return False
