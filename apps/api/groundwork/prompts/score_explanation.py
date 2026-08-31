from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.prompts.base import MAX_EXPLANATION_DIMENSIONS
from groundwork.providers.base import PromptEnvelope

PROMPT_VERSION = "score_explanation-v1"

_SYSTEM = (
    "You are writing the one-sentence explanation under an already-computed ICP "
    "score in Groundwork. The numbers below are final and were computed by a "
    "deterministic rubric, not by you — you cannot change `overall` or any "
    "dimension value. Write one concise sentence explaining the score using only "
    "the numbers given. Do not invent evidence, do not mention numbers not listed "
    "below, and do not hedge about whether the score is correct."
)


class TopDimensionInput(BaseModel):
    name: str
    raw: float
    weight: float
    contribution: float


class ScoreExplanationInput(BaseModel):
    """Only the dimensions that actually moved the score — never the full
    eight-dimension corpus (token minimization, Phase 2)."""

    company_name: str
    overall: int
    disqualified: bool
    disqualifier_reason: str | None = None
    top_dimensions: list[TopDimensionInput] = Field(default_factory=list)


def build_envelope(ctx_key: str, data: ScoreExplanationInput) -> PromptEnvelope:
    if data.disqualified:
        user = (
            f"{data.company_name} scored {data.overall}/100 — capped by a hard "
            f"disqualifier ({data.disqualifier_reason or 'excluded criterion'})."
        )
    else:
        dims = "\n".join(
            f"- {d.name}: raw={d.raw:.2f} weight={d.weight:.2f} contribution={d.contribution:+.2f}"
            for d in data.top_dimensions[:MAX_EXPLANATION_DIMENSIONS]
        )
        user = f"{data.company_name} scored {data.overall}/100. Top contributing dimensions:\n{dims}"
    return PromptEnvelope(
        ctx_key=ctx_key,
        system=_SYSTEM,
        user=user,
        metadata={
            "overall": data.overall,
            "disqualified": data.disqualified,
            "top_dimensions": [
                {"name": d.name, "contribution": d.contribution} for d in data.top_dimensions
            ],
        },
    )
