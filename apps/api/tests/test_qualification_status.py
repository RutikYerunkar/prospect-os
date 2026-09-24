"""v2.0.3 qualification semantics — `NOT_QUALIFIED` derivation and precedence.

Unit-level: `engine/runner.py::_derive_final_status` is a pure function of
`ctx.score`/`ctx.review`/`ctx.play_spec` — a lightweight stand-in object
(only those three attributes) exercises its precedence chain directly,
without constructing a full `ProspectContext` (which needs a `ProviderBundle`,
recorders, etc. unrelated to this logic). End-to-end proof that a real
below-minimum prospect actually reaches this status through the full engine
lives in `test_qualification_end_to_end.py`.

Precedence under test: disqualified -> review FAIL -> review NEEDS_REVIEW ->
exclusion UNKNOWN -> score floor -> PASS. The seven review checks themselves
are never touched by this feature.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from groundwork.engine.runner import _derive_final_status
from groundwork.models.enums import ExclusionEvaluation, ProspectStatus, ReviewVerdict
from groundwork.models.schemas import ICPScore, PlaySpec, ReviewResult


def _ctx(
    *,
    disqualified: bool = False,
    exclusion_status: ExclusionEvaluation = ExclusionEvaluation.NOT_EXCLUDED,
    overall: int = 80,
    verdict: ReviewVerdict = ReviewVerdict.PASS,
    min_score: int = 60,
) -> SimpleNamespace:
    score = ICPScore(
        prospect_id="p1", overall=overall, dimensions=[], disqualified=disqualified,
        confidence=1.0, exclusion_status=exclusion_status,
    )
    review = ReviewResult(prospect_id="p1", verdict=verdict, checks=[])
    play_spec = PlaySpec(objective_text="x", min_score=min_score)
    return SimpleNamespace(score=score, review=review, play_spec=play_spec)


def test_clean_below_minimum_prospect_is_not_qualified_with_review_pass() -> None:
    ctx = _ctx(overall=59, min_score=60, verdict=ReviewVerdict.PASS)
    assert ctx.review.verdict == ReviewVerdict.PASS  # review itself is untouched
    assert _derive_final_status(ctx) == ProspectStatus.NOT_QUALIFIED


def test_score_at_minimum_is_pass_not_not_qualified() -> None:
    # strict `<` — equal to the floor still qualifies.
    ctx = _ctx(overall=60, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.PASS


def test_score_above_minimum_is_pass() -> None:
    ctx = _ctx(overall=61, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.PASS


def test_min_score_zero_boundary_never_not_qualified() -> None:
    ctx = _ctx(overall=0, min_score=0)
    assert _derive_final_status(ctx) == ProspectStatus.PASS


def test_min_score_hundred_boundary_only_perfect_score_qualifies() -> None:
    assert _derive_final_status(_ctx(overall=100, min_score=100)) == ProspectStatus.PASS
    assert _derive_final_status(_ctx(overall=99, min_score=100)) == ProspectStatus.NOT_QUALIFIED


def test_disqualified_outranks_score_floor() -> None:
    # Disqualified AND below the floor — REJECTED wins, never NOT_QUALIFIED.
    ctx = _ctx(disqualified=True, overall=10, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.REJECTED


def test_review_fail_outranks_score_floor() -> None:
    # Review FAIL AND below the floor — REJECTED wins, never NOT_QUALIFIED.
    ctx = _ctx(verdict=ReviewVerdict.FAIL, overall=10, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.REJECTED


def test_review_needs_review_outranks_score_floor() -> None:
    # NEEDS_REVIEW AND below the floor — NEEDS_REVIEW wins, never NOT_QUALIFIED.
    ctx = _ctx(verdict=ReviewVerdict.NEEDS_REVIEW, overall=10, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.NEEDS_REVIEW


def test_exclusion_unknown_outranks_score_floor() -> None:
    # UNKNOWN exclusion AND below the floor — NEEDS_REVIEW wins (H1 Phase 7's
    # existing downgrade), never NOT_QUALIFIED.
    ctx = _ctx(exclusion_status=ExclusionEvaluation.UNKNOWN, overall=10, min_score=60)
    assert _derive_final_status(ctx) == ProspectStatus.NEEDS_REVIEW


@pytest.mark.parametrize("min_score", [-1, 101])
def test_out_of_range_min_score_rejected_by_playspec_validation(min_score: int) -> None:
    with pytest.raises(Exception):
        PlaySpec(objective_text="x", min_score=min_score)


@pytest.mark.parametrize("min_score", [0, 100])
def test_min_score_boundary_values_are_valid(min_score: int) -> None:
    spec = PlaySpec(objective_text="x", min_score=min_score)
    assert spec.min_score == min_score
