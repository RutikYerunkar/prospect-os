"""v2.0.1 Live-Quality Hardening — safe placeholder-signature stripping.

The Personalization Agent (`engine/steps/personalize.py`) sometimes closes a
draft with a boilerplate sign-off placeholder like `[Your Name]` instead of
omitting a signature entirely, having no real sender name to write. That is
harmless boilerplate, not a real defect — but `domain/review.py`'s
`no_placeholders` hard check has no safe way to distinguish it from a
genuinely broken mid-body placeholder, and must not be weakened to try (see
that module's own docstring on why the pattern set is intentionally broad).

So this module removes ONLY the one narrow, safe case: a placeholder token
that is the draft body's own trailing (last non-empty) line, standing in for
a signature, with nothing else on that line. It NEVER fabricates a sender
name to put in the placeholder's place — it only ever deletes the line. Any
other placeholder — mid-body, sharing a line with real content, or anywhere
that isn't the last line — is left completely untouched and still reaches
`domain/review.py::_no_placeholders`, which still hard-FAILs it exactly as
before this module existed.
"""

from __future__ import annotations

import re

from groundwork.domain.review import _PLACEHOLDER_PATTERNS

# Only the bracket/brace/angle-token shapes — a bare "TODO" on its own line
# is not a sender-name signature and is deliberately never stripped here.
_SIGNATURE_PATTERNS = _PLACEHOLDER_PATTERNS[:3]


def strip_trailing_placeholder_signature(body: str) -> str:
    """Delete `body`'s trailing line when, and only when, that line is
    made up entirely of one placeholder-shaped token (after stripping
    surrounding whitespace) — e.g. a body ending in `"\\n\\n[Your Name]"`.
    Returns `body` completely unchanged in every other case, including
    when the placeholder shares its line with other text."""
    stripped = body.rstrip()
    if not stripped:
        return body
    last_newline = stripped.rfind("\n")
    last_line = stripped[last_newline + 1 :]
    candidate = last_line.strip()
    if not candidate:
        return body
    lowered = candidate.lower()
    if any(re.fullmatch(pattern, lowered) for pattern in _SIGNATURE_PATTERNS):
        remainder = stripped[:last_newline] if last_newline != -1 else ""
        return remainder.rstrip()
    return body
