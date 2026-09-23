"""v2.0.1 Live-Quality Hardening — safe placeholder-signature stripping.

`strip_trailing_placeholder_signature()` may delete ONLY a trailing,
placeholder-only sender-signature line. Anything else — mid-body, sharing a
line with real content, or not the very last line — must be left completely
untouched so it still reaches `domain/review.py::_no_placeholders` and still
hard-FAILs there.
"""

from __future__ import annotations

import re

from groundwork.domain.outreach_sanitize import strip_trailing_placeholder_signature
from groundwork.domain.review import _PLACEHOLDER_PATTERNS


def test_strips_trailing_bracket_placeholder_signature() -> None:
    body = "Hi there,\n\nGreat to connect.\n\nBest,\n[Your Name]"
    assert strip_trailing_placeholder_signature(body) == "Hi there,\n\nGreat to connect.\n\nBest,"


def test_never_fabricates_a_sender_name() -> None:
    body = "Hi there,\n\nGreat to connect.\n\nBest,\n[Your Name]"
    result = strip_trailing_placeholder_signature(body)
    assert "Your Name" not in result
    # Nothing invented in its place — the sign-off line above is untouched,
    # and nothing new was appended.
    assert result == "Hi there,\n\nGreat to connect.\n\nBest,"


def test_leaves_mid_body_placeholder_untouched() -> None:
    body = "Hi [First Name],\n\nGreat to connect.\n\nBest,\nJordan Lee"
    assert strip_trailing_placeholder_signature(body) == body


def test_leaves_placeholder_sharing_a_line_with_real_content_untouched() -> None:
    body = "Hi there,\n\nSee you at [Event Name] next week.\n\nBest,\nJordan Lee"
    assert strip_trailing_placeholder_signature(body) == body


def test_leaves_body_with_a_real_signature_untouched() -> None:
    body = "Hi there,\n\nGreat to connect.\n\nBest,\nJordan Lee"
    assert strip_trailing_placeholder_signature(body) == body


def test_body_that_is_only_a_placeholder_becomes_empty() -> None:
    assert strip_trailing_placeholder_signature("[Your Name]") == ""


def test_still_reaches_the_hard_review_guard_when_unsafe() -> None:
    # Mirrors domain/review.py::_no_placeholders' own detection so the two
    # stay consistent: what this module refuses to strip must still be
    # exactly what the hard guard would flag.
    body = "Hi [First Name],\n\nGreat to connect.\n\nBest,\nJordan Lee"
    sanitized = strip_trailing_placeholder_signature(body)
    assert sanitized == body
    text = sanitized.lower()
    assert any(re.search(pattern, text) for pattern in _PLACEHOLDER_PATTERNS)
