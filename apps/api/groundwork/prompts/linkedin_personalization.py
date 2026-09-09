"""v2 §V2-F — the LinkedIn Personalization Agent prompt/output builder.

A SEPARATE LLM operation from `prompts/personalization.py` (email): its own
schema (`LinkedInOutreachOutput`, no subject), its own `LLMOperation`
(`LINKEDIN_PERSONALIZATION`), and its own ctx_key (`personalize:linkedin`).
`prompts/personalization.py` and the email branch it feeds are untouched by
this module's existence — this is additive, not a refactor of it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.prompts.base import MAX_PERSONALIZATION_SIGNALS
from groundwork.providers.base import PromptEnvelope

PROMPT_VERSION = "linkedin-personalization-v1"

_SYSTEM = (
    "You are the Personalization Agent in Groundwork, writing a short LinkedIn "
    "outreach message (NOT an email — there is no subject line) to one buyer at "
    "one company, citing only the grounded signals given below — never invent a "
    "fact, a metric, or a claim not listed. Every sentence that references a "
    "specific fact must appear in `claim_map`, citing the evidence_id it came "
    "from. Each `claim_map` sentence is checked automatically against the "
    "original source text, so when you cite a signal, echo its wording closely "
    "— reuse the signal's key nouns, numbers and phrases rather than "
    "rephrasing loosely. Keep it shorter and more conversational than an email "
    "— LinkedIn messages are read on a phone. If you have no grounded signals "
    "to cite, write a brief, generic-but-professional note with no fabricated "
    "specifics and leave `claim_map` empty."
)


class GroundedSignalInput(BaseModel):
    summary: str
    evidence_id: str


class LinkedInPersonalizationInput(BaseModel):
    """Constructed only from this prospect's own `ProspectContext` — same
    isolation discipline as `prompts.personalization.PersonalizationInput`."""

    company_name: str
    persona_name: str | None = None
    persona_title: str | None = None
    signals: list[GroundedSignalInput] = Field(default_factory=list)


def build_envelope(ctx_key: str, data: LinkedInPersonalizationInput) -> PromptEnvelope:
    persona = data.persona_name or data.persona_title or "there"
    bounded = data.signals[:MAX_PERSONALIZATION_SIGNALS]
    signals_block = "\n".join(f"- ({s.evidence_id}) {s.summary}" for s in bounded) or "(none — write a generic note)"
    user = (
        f"Draft a LinkedIn outreach message to {persona} at {data.company_name}.\n"
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
