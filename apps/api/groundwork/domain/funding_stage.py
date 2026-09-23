"""Lexical-only funding-stage canonicalization (v2.0.1 Live-Quality Hardening).

Normalizes the *spelling/punctuation/case* of a funding-stage string onto the
canonical `snake_case` tokens `domain/scoring.py::_FUNDING_STAGE_ORDER` and
the fixture pack already use (`pre_seed`, `seed`, `series_a`, `series_b`,
`series_c`, `growth`). This is a pure formatting transform, never a semantic
one: a stage this module doesn't recognize as a spelling variant of one of
those six (e.g. "Series D", "Series E", a future round label, or arbitrary
free text) is normalized for punctuation/case only and returned as its own
`snake_case` form — it is NEVER remapped onto a *different* canonical stage.
In particular, "Series D"/"Series E"/"Series F"/etc. must never become
"growth" here; "growth" is only produced when the input text itself lexically
says "growth" (optionally with a purely descriptive suffix like "stage" or
"round", stripped the same way "seed round" -> "seed").

`SUPPORTED_FUNDING_STAGES` is the single, repository-backed enumeration of
the six stages `domain/scoring.py` actually understands (mirrors
`_FUNDING_STAGE_ORDER` without importing that module's private name) — the
one place other code (e.g. the objective-parse prompt) should point to
instead of hand-rolling its own list.
"""

from __future__ import annotations

import re

# Repository-backed canonical enumeration — mirrors
# `domain/scoring.py::_FUNDING_STAGE_ORDER` exactly. Duplicated (not
# imported) because that name is private to `scoring.py` and this module
# must stay import-independent of it; `tests/test_funding_stage.py` guards
# the two staying in sync.
SUPPORTED_FUNDING_STAGES: tuple[str, ...] = (
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c",
    "growth",
)

# Purely descriptive suffix words that carry no stage identity of their own
# ("seed round" and "seed" name the same stage; stripping the suffix is
# formatting, not inference) — stripped only when at least one token remains.
_FILLER_SUFFIXES = frozenset({"round", "funding", "financing", "stage"})

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def canonicalize_funding_stage(raw: str) -> str:
    """Normalize `raw`'s spelling/punctuation/case only.

    Returns the canonical `snake_case` token when `raw` is a recognized
    spelling variant of one of the six known stages; otherwise returns
    `raw`'s own case/punctuation-normalized `snake_case` form, unchanged in
    meaning (e.g. "Series D" -> "series_d", "Growth Equity" ->
    "growth_equity") — never bucketed onto a different recognized stage.
    Idempotent: canonicalizing an already-canonical token returns it
    unchanged.
    """
    if not raw or not raw.strip():
        return raw
    lowered = raw.strip().lower()
    # "preseed" is the same word as "pre-seed"/"pre seed" spelled without a
    # separator — a narrow, literal spelling merge, not a semantic one.
    lowered = lowered.replace("preseed", "pre seed")
    normalized = _NON_ALNUM_RE.sub(" ", lowered).strip()
    if not normalized:
        return raw
    tokens = normalized.split(" ")
    while len(tokens) > 1 and tokens[-1] in _FILLER_SUFFIXES:
        tokens = tokens[:-1]
    return "_".join(tokens)


def canonicalize_funding_stages(raw_stages: list[str]) -> list[str]:
    """Canonicalize each entry of `raw_stages`, preserving order and
    dropping duplicates that canonicalize to the same token."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in raw_stages:
        canonical = canonicalize_funding_stage(raw)
        if canonical and canonical not in seen:
            seen.add(canonical)
            result.append(canonical)
    return result
