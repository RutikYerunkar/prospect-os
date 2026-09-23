"""v2.0.1 Live-Quality Hardening — lexical-only funding-stage canonicalization.

`canonicalize_funding_stage()` must normalize spelling/case/punctuation only,
never bucket a stage it doesn't recognize onto a different, already-known
stage. In particular Series D/E/F/... must never become "growth".
"""

from __future__ import annotations

from groundwork.domain.funding_stage import (
    SUPPORTED_FUNDING_STAGES,
    canonicalize_funding_stage,
    canonicalize_funding_stages,
)
from groundwork.domain.scoring import _FUNDING_STAGE_ORDER


def test_supported_stages_mirror_scoring_module_exactly() -> None:
    assert list(SUPPORTED_FUNDING_STAGES) == _FUNDING_STAGE_ORDER


def test_canonicalizes_common_spelling_variants() -> None:
    assert canonicalize_funding_stage("Series A") == "series_a"
    assert canonicalize_funding_stage("series-a") == "series_a"
    assert canonicalize_funding_stage("SERIES A ROUND") == "series_a"
    assert canonicalize_funding_stage("Series B Round") == "series_b"
    assert canonicalize_funding_stage("Pre-Seed") == "pre_seed"
    assert canonicalize_funding_stage("PRESEED") == "pre_seed"
    assert canonicalize_funding_stage("Seed Round") == "seed"
    assert canonicalize_funding_stage("Growth Stage") == "growth"


def test_is_idempotent_on_already_canonical_tokens() -> None:
    for stage in SUPPORTED_FUNDING_STAGES:
        assert canonicalize_funding_stage(stage) == stage


def test_never_buckets_a_later_series_onto_growth() -> None:
    assert canonicalize_funding_stage("Series D") == "series_d"
    assert canonicalize_funding_stage("Series E") == "series_e"
    assert canonicalize_funding_stage("series f round") == "series_f"
    for stage in ("series_d", "series_e", "series_f"):
        assert stage != "growth"


def test_never_invents_semantics_for_unrecognized_free_text() -> None:
    assert canonicalize_funding_stage("Growth Equity") == "growth_equity"
    assert canonicalize_funding_stage("Growth Equity") != "growth"


def test_canonicalize_funding_stages_dedupes_preserving_order() -> None:
    assert canonicalize_funding_stages(["Series A", "series-a", "Series B"]) == ["series_a", "series_b"]


def test_empty_and_blank_input_pass_through() -> None:
    assert canonicalize_funding_stage("") == ""
    assert canonicalize_funding_stage("   ") == "   "
