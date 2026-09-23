"""v2.0.1 Live-Quality Hardening — objective-parser funding-stage contract.

`parse_objective()` must canonicalize the LLM's free-text
`target_funding_stages` spellings server-side before they reach a
`PlaySpec`, and must never override the documented empty-list behavior when
the model infers no funding stage at all.
"""

from __future__ import annotations

from groundwork.engine.objective_parser import parse_objective
from groundwork.models.llm_io import ObjectiveParseOutput
from groundwork.providers.base import LLMResult


class _FakeLLMProvider:
    def __init__(self, output: ObjectiveParseOutput) -> None:
        self._output = output

    async def structured(self, envelope, schema, *, ctx_key, operation):
        return LLMResult(
            parsed=self._output, raw=self._output.model_dump(mode="json"), operation=operation,
            model="gpt-5.6-terra", provider="openai", prompt_version="objective_parse-v1", attempts=[],
        )


async def test_canonicalizes_llm_funding_stage_spelling_variants() -> None:
    output = ObjectiveParseOutput(target_funding_stages=["Series A", "series-b Round"])
    provider = _FakeLLMProvider(output)
    result = await parse_objective(
        objective_text="Find Series A or B companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.target_funding_stages == ["series_a", "series_b"]


async def test_does_not_bucket_a_later_series_onto_growth() -> None:
    output = ObjectiveParseOutput(target_funding_stages=["Series D"])
    provider = _FakeLLMProvider(output)
    result = await parse_objective(
        objective_text="Find Series D companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.target_funding_stages == ["series_d"]
    assert "growth" not in result.play_spec.target_funding_stages


async def test_empty_list_behavior_preserved_when_model_infers_no_stage() -> None:
    output = ObjectiveParseOutput(target_industries=["fintech"])
    provider = _FakeLLMProvider(output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.target_funding_stages == []


async def test_deterministic_fallback_path_unaffected() -> None:
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=None, use_llm=False,
    )
    assert result.parse_source == "deterministic"
    assert result.play_spec.target_funding_stages == []
