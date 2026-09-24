"""v2.0.3 — `engine/objective_parser.py::parse_objective()` must discard an
out-of-range model-INFERRED `min_score` before it ever reaches
`PlaySpec.model_validate()` (now `Field(ge=0, le=100)` — see
`test_qualification_status.py`/`test_qualification_api.py`), so a bad
inference degrades gracefully instead of raising — the same deterministic-
degradation contract `test_objective_parser.py` already covers for provider
failures. Kept as its own file rather than extending `test_objective_parser.py`
(left byte-identical, per the Rev 2 plan's "unmodified except
`test_action_policy.py`'s parametrize list" constraint).
"""

from __future__ import annotations

import pytest

from groundwork.engine.objective_parser import parse_objective
from groundwork.models.llm_io import ObjectiveParseOutput
from groundwork.providers.base import LLMResult


class _FakeLLMProvider:
    """Minimal `LLMProvider` fake — mirrors `test_objective_parser.py`'s own
    (kept local rather than imported, so this file has no cross-test-module
    dependency)."""

    def __init__(self, *, output: ObjectiveParseOutput) -> None:
        self._output = output

    async def structured(self, envelope, schema, *, ctx_key, operation):
        return LLMResult(
            parsed=self._output, raw=self._output.model_dump(mode="json"), operation=operation,
            model="gpt-5.6-terra", provider="openai", prompt_version="objective_parse-v1", attempts=[],
        )


@pytest.mark.parametrize("bad_min_score", [-1, 101, -50, 1000])
async def test_parse_objective_discards_invalid_inferred_min_score(bad_min_score: int):
    output = ObjectiveParseOutput(target_industries=["fintech"], min_score=bad_min_score)
    provider = _FakeLLMProvider(output=output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.parse_source == "llm"  # only the one bad field was discarded, not the whole parse
    assert result.play_spec.min_score == 60  # PlaySpec's own default, never the invalid inferred value
    assert result.play_spec.target_industries == ["fintech"]  # the rest of the inference still applies


@pytest.mark.parametrize("boundary", [0, 100])
async def test_parse_objective_valid_inferred_min_score_boundaries_survive(boundary: int):
    output = ObjectiveParseOutput(min_score=boundary)
    provider = _FakeLLMProvider(output=output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.min_score == boundary


async def test_parse_objective_valid_user_override_wins_over_invalid_inferred_min_score():
    output = ObjectiveParseOutput(min_score=150)  # invalid — would be discarded anyway
    provider = _FakeLLMProvider(output=output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={"min_score": 85},
        target_count=5, llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.min_score == 85
