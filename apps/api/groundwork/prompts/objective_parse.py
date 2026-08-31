from __future__ import annotations

from pydantic import BaseModel

from groundwork.providers.base import PromptEnvelope

PROMPT_VERSION = "objective_parse-v1"

_SYSTEM = (
    "You turn a plain-language GTM growth objective into structured ICP "
    "(Ideal Customer Profile) criteria for Groundwork. Infer only what the "
    "objective text actually implies — target industries, excluded industries, "
    "funding stages, technologies, buyer persona titles, a minimum company size "
    "band, a minimum ICP score threshold, and a minimum confidence threshold. "
    "Leave a field at its empty/null default if the objective doesn't clearly "
    "imply it — never guess a specific number or industry that isn't supported "
    "by the text. Do not restate the objective text itself; only return the "
    "structured criteria."
)


class ObjectiveParseInput(BaseModel):
    """Built only from the raw objective text the user submitted — this call
    runs before any `Play` row (and therefore any `ProspectContext`) exists."""

    objective_text: str


def build_envelope(ctx_key: str, data: ObjectiveParseInput) -> PromptEnvelope:
    return PromptEnvelope(
        ctx_key=ctx_key,
        system=_SYSTEM,
        user=f"Objective: {data.objective_text}",
        metadata={"objective_text": data.objective_text},
    )
