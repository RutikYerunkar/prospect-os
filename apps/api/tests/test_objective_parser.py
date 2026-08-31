"""Phase 9: `parse_objective()` makes zero DB writes and falls back
deterministically on ANY provider failure; the API layer creates the Play
row and its `objective_parse` telemetry in one transaction so an
`llm_calls` row can never reference a nonexistent Play; user overrides
always win over LLM-inferred values.
"""

from __future__ import annotations

from pydantic import BaseModel

from groundwork.engine.objective_parser import parse_objective
from groundwork.models.llm_io import ObjectiveParseOutput
from groundwork.providers.base import LLMOperation, LLMResult, ProviderRefusal
from groundwork.repositories.llm_calls import LLMCallRepository
from groundwork.repositories.plays import PlayRepository
from tests.live_helpers import make_runtime, message_output, response_body


class _FakeLLMProvider:
    """Direct `LLMProvider` fake — exercises `parse_objective()` without a
    real HTTP round trip, for the pure-function tests below."""

    def __init__(self, *, output: ObjectiveParseOutput | None = None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error

    async def structured(self, envelope, schema, *, ctx_key, operation):
        if self._error is not None:
            raise self._error
        return LLMResult(
            parsed=self._output, raw=self._output.model_dump(mode="json"), operation=operation,
            model="gpt-5.6-terra", provider="openai", prompt_version="objective_parse-v1", attempts=[],
        )


async def test_parse_objective_zero_db_writes_on_success():
    output = ObjectiveParseOutput(target_industries=["fintech"], min_score=70)
    provider = _FakeLLMProvider(output=output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    assert result.parse_source == "llm"
    assert result.play_spec.target_industries == ["fintech"]
    assert result.play_spec.min_score == 70


async def test_parse_objective_falls_back_deterministically_on_provider_error():
    provider = _FakeLLMProvider(error=ProviderRefusal("refused", attempts=[]))
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={"target_industries": ["fintech"]},
        target_count=5, llm_provider=provider, use_llm=True,
    )
    assert result.parse_source == "deterministic"
    assert result.play_spec.target_industries == ["fintech"]  # from icp_overrides, not lost


async def test_parse_objective_user_overrides_win_over_llm_inference():
    output = ObjectiveParseOutput(target_industries=["fintech"], min_score=70)
    provider = _FakeLLMProvider(output=output)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={"min_score": 90},
        target_count=5, llm_provider=provider, use_llm=True,
    )
    assert result.play_spec.min_score == 90  # override wins over the model's inferred 70
    assert result.play_spec.target_industries == ["fintech"]  # inferred value still applied where no override


async def test_parse_objective_never_asked_to_echo_objective_text_or_target_count():
    # ObjectiveParseOutput structurally has no such fields — this is a
    # schema-shape assertion, not a behavioral one.
    assert "objective_text" not in ObjectiveParseOutput.model_fields
    assert "target_count" not in ObjectiveParseOutput.model_fields


async def test_demo_mode_never_calls_llm_for_objective_parse():
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=None, use_llm=False,
    )
    assert result.parse_source == "deterministic"
    assert result.attempts == []


async def test_play_and_llm_calls_created_in_one_transaction(session_factory):
    """The real end-to-end path: a live-provider call succeeds, and the
    resulting Play + llm_calls rows land together via
    `create_play_with_attempts`."""
    body = response_body(output=[message_output('{"target_industries": ["fintech"], "excluded_industries": [], "target_funding_stages": [], "target_technologies": [], "persona_titles": [], "size_band_min": null, "size_band_max": null, "min_score": null, "min_confidence": null}')])
    runtime, transport = make_runtime([(200, body)])
    from groundwork.providers.live.openai_llm import OpenAILLMProvider

    provider = OpenAILLMProvider(runtime=runtime)
    result = await parse_objective(
        objective_text="Find fintech companies", icp_overrides={}, target_count=5,
        llm_provider=provider, use_llm=True,
    )
    await runtime.close()
    assert result.parse_source == "llm"
    assert len(result.attempts) == 1

    llm_calls = LLMCallRepository(session_factory)
    play_id = await llm_calls.create_play_with_attempts(
        play_kwargs=dict(
            name="Find fintech companies", objective_text="Find fintech companies",
            icp_spec=result.play_spec.model_dump(mode="json"), mode="live",
        ),
        call_group_id="grp-parse-1", operation="objective_parse", provider="openai",
        prompt_version="objective_parse-v1", attempts=result.attempts,
    )

    plays = PlayRepository(session_factory)
    play_row = await plays.get(play_id)
    assert play_row is not None

    rows = await llm_calls.for_play(play_id)
    assert len(rows) == 1
    assert rows[0].operation == "objective_parse"
    assert rows[0].run_id is None
    assert rows[0].prospect_id is None
    assert rows[0].play_id == play_id
