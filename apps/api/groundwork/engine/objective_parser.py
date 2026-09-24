"""Objective Parser — the fourth Live LLM operation (Checkpoint G Phase 9).

Runs BEFORE any `Play` row exists: `parse_objective()` makes zero DB writes,
executes (at most) one LLM call, and holds its attempt telemetry in memory.
On ANY LLM/provider/schema/refusal/truncation/budget failure it
deterministically falls back to the same objective->PlaySpec construction
Demo Mode has always used — never a raised exception, never a 500.

The caller (`api/routers/plays.py`) is responsible for creating the `Play`
row and its `objective_parse` `llm_calls` rows in ONE DB transaction — see
`repositories/llm_calls.py::create_play_with_attempts` — so an `llm_calls`
row can never reference a `Play` that doesn't exist, and a failed
transaction rolls both back together.

User overrides (`icp_overrides`) ALWAYS win over LLM-inferred values —
applied last, on top of whatever the model inferred.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from groundwork.domain.funding_stage import canonicalize_funding_stages
from groundwork.models.llm_io import ObjectiveParseOutput
from groundwork.models.schemas import PlaySpec
from groundwork.prompts import objective_parse as prompt
from groundwork.providers.base import LLMAttemptTelemetry, LLMOperation, ProviderError


@dataclass
class ObjectiveParseResult:
    play_spec: PlaySpec
    parse_source: str  # "llm" | "deterministic"
    attempts: list[LLMAttemptTelemetry]
    model: str | None
    provider: str | None


async def parse_objective(
    *,
    objective_text: str,
    icp_overrides: dict[str, Any],
    target_count: int,
    llm_provider: Any | None,
    use_llm: bool,
) -> ObjectiveParseResult:
    inferred: dict[str, Any] = {}
    attempts: list[LLMAttemptTelemetry] = []
    parse_source = "deterministic"
    model: str | None = None
    provider: str | None = None

    if use_llm and llm_provider is not None:
        ctx_key = f"objective_parse:{uuid.uuid4()}"
        envelope = prompt.build_envelope(ctx_key, prompt.ObjectiveParseInput(objective_text=objective_text))
        try:
            result = await llm_provider.structured(
                envelope, ObjectiveParseOutput, ctx_key=ctx_key, operation=LLMOperation.OBJECTIVE_PARSE
            )
        except ProviderError as exc:
            # Deterministic fallback on ANY provider/schema/refusal/
            # truncation/budget failure — never surfaced as an error.
            attempts = exc.attempts
            parse_source = "deterministic"
        else:
            attempts = result.attempts
            model, provider = result.model, result.provider
            parsed = result.parsed.model_dump(mode="json")
            # Lexical-only canonicalization (v2.0.1) — normalize the
            # model's free-text stage spellings onto the same snake_case
            # vocabulary scoring/fixtures use before they ever reach a
            # `PlaySpec`. See `domain/funding_stage.py`.
            if parsed.get("target_funding_stages"):
                parsed["target_funding_stages"] = canonicalize_funding_stages(
                    parsed["target_funding_stages"]
                )
            # Only inferred fields the model actually populated — an empty
            # list/None means "the objective didn't imply this," not
            # "override the caller's default with nothing."
            inferred = {k: v for k, v in parsed.items() if v not in (None, [], {})}
            # v2.0.3: `min_score` is now server-validated 0..100 on
            # `PlaySpec` (`ValidationError` -> the existing 422 path for an
            # explicit/API value). A model-INFERRED value out of that range
            # must never raise here — this function's whole contract is
            # deterministic degradation on any inference problem, never a
            # raised exception. Discard it before it ever reaches
            # `PlaySpec.model_validate` below; the caller's default (or a
            # valid user override, applied after this) wins instead.
            inferred_min_score = inferred.get("min_score")
            if isinstance(inferred_min_score, int) and not (0 <= inferred_min_score <= 100):
                del inferred["min_score"]
            parse_source = "llm"

    # User overrides always win — applied last, on top of any inference.
    spec_data: dict[str, Any] = {
        **inferred,
        **icp_overrides,
        "objective_text": objective_text,
        "target_count": target_count,
    }
    play_spec = PlaySpec.model_validate(spec_data)
    return ObjectiveParseResult(
        play_spec=play_spec, parse_source=parse_source, attempts=attempts, model=model, provider=provider
    )
