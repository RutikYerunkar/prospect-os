"""Canonical industry classification (H1 Phase 5).

Real web text can't flow directly into equality comparisons against internal
`PlaySpec` category slugs. A research extraction (Demo or, eventually, a real
LLM) must select an industry category from a closed, server-defined set —
this Play's own `target_industries ∪ excluded_industries ∪
adjacent_industries(keys ∪ values)`, plus the `OTHER` sentinel for
"classified, but outside every category this Play cares about" — and the
server independently re-validates that selection. A category that didn't
come from the served set never reaches scoring or exclusion as free text.

`OTHER` and `UNKNOWN` are deliberately distinct states:

- `OTHER` — grounded/classified, but outside the served target/excluded set.
  Represented as the literal string `"OTHER"`. `industry_fit` is scored
  (`raw=0.0`) — the fact was established, it just isn't a fit.
- `UNKNOWN` — not adequately classified at all. Represented as `category is
  None` on `IndustryProfileFact` — never a string in the served set.
  `industry_fit` is unscoreable and exclusion policy is unevaluable.
"""

from __future__ import annotations

from groundwork.models.schemas import PlaySpec

OTHER_CATEGORY = "OTHER"


def allowed_categories(play_spec: PlaySpec) -> frozenset[str]:
    """The full served category set for one Play — everything a
    classification is allowed to select from."""
    categories: set[str] = set(play_spec.target_industries)
    categories.update(play_spec.excluded_industries)
    categories.update(play_spec.adjacent_industries.keys())
    for values in play_spec.adjacent_industries.values():
        categories.update(values)
    categories.add(OTHER_CATEGORY)
    return frozenset(categories)


def validate_category(raw: str | None, allowed: frozenset[str]) -> str | None:
    """Server-side membership check. `None` in, `None` out. A non-member
    string collapses to `None` (UNKNOWN) — it never reaches scoring or
    exclusion as free text, regardless of what produced it."""
    if raw is None:
        return None
    return raw if raw in allowed else None
