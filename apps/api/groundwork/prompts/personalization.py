from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.prompts.base import MAX_PERSONALIZATION_SIGNALS
from groundwork.providers.base import PromptEnvelope

PROMPT_VERSION = "personalization-v2"

_SYSTEM = (
    "You are the Personalization Agent in Groundwork. Write a short, personalized "
    "outreach email to one buyer at one company, citing only the grounded signals "
    "given below — never invent a fact, a metric, or a claim not listed. Every "
    "sentence that references a specific fact must appear in `claim_map`, citing "
    "the evidence_id it came from. Each `claim_map` sentence is checked automatically "
    "against the original source text, so when you cite a signal, echo its wording "
    "closely — reuse the signal's key nouns, numbers and phrases rather than "
    "rephrasing loosely or writing generic marketing language around it; a sentence "
    "that drifts too far from the signal's own wording will be rejected even if the "
    "fact is correct. If you have no grounded signals to cite, write a brief, "
    "generic-but-professional note with no fabricated specifics and leave "
    "`claim_map` empty."
)


class GroundedSignalInput(BaseModel):
    summary: str
    evidence_id: str


class PersonalizationInput(BaseModel):
    """Constructed only from this prospect's own `ProspectContext` — its
    own contact and its own grounded signals, nothing from any sibling
    prospect in the same run (the isolation boundary, made structural)."""

    company_name: str
    persona_name: str | None = None
    persona_title: str | None = None
    signals: list[GroundedSignalInput] = Field(default_factory=list)


def build_envelope(ctx_key: str, data: PersonalizationInput) -> PromptEnvelope:
    persona = data.persona_name or data.persona_title or "there"
    bounded = data.signals[:MAX_PERSONALIZATION_SIGNALS]
    signals_block = "\n".join(f"- ({s.evidence_id}) {s.summary}" for s in bounded) or "(none — write a generic note)"
    user = (
        f"Draft outreach to {persona} at {data.company_name}.\n"
        f"Grounded signals available to cite:\n{signals_block}"
    )
    return PromptEnvelope(
        ctx_key=ctx_key,
        system=_SYSTEM,
        user=user,
        metadata={
            "company_name": data.company_name,
            "persona_name": data.persona_name,
            "persona_title": data.persona_title,
            "signals": [{"summary": s.summary, "evidence_id": s.evidence_id} for s in bounded],
        },
    )
