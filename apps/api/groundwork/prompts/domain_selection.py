"""H2 Stage C ambiguous-fallback — `LLMOperation.DOMAIN_SELECTION`.

Only invoked when the deterministic path (exactly one acceptable domain
candidate survives `resolve_candidate_domain()`) could NOT resolve a
company's domain on its own — i.e. zero or multiple structurally-acceptable
candidates were served. The model picks one served candidate ref (never a
URL/domain it authors itself) or returns null; `null` is a legitimate,
expected "unresolved" answer, not a schema failure. The server never trusts
the model's selection blindly — the selected ref must have been served this
call, and the engine still runs the same URL -> canonical-domain -> safety
-> aggregator gate on whichever candidate the ref resolves to.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.prompts.base import UNTRUSTED_SOURCE_NOTICE
from groundwork.providers.base import DomainCandidate, PromptEnvelope

PROMPT_VERSION = "domain_selection-v1"

MAX_DOMAIN_CANDIDATES = 10

_SYSTEM = (
    "You are the Domain Resolution Agent in Groundwork, a GTM prospect-research "
    "pipeline. You are given a company name and a bounded list of search-result "
    "candidates (each an opaque reference id plus its page title only — NO URL or "
    "domain is ever shown to you). Decide which single candidate, if any, is most "
    "likely that company's own official website (not a news article about it, not a "
    "directory/social/aggregator listing, not an unrelated company). "
    f"{UNTRUSTED_SOURCE_NOTICE} "
    "Return `selected_candidate_ref` as the ref id of your choice, citing only a ref "
    "shown to you below — never invent one. If none of the candidates is plausibly "
    "the company's own site, or you are not reasonably confident, return null. "
    "You cannot see or produce a URL or domain; your only output is a ref id or null."
)


class DomainCandidateInput(BaseModel):
    ref: str
    title: str


class DomainSelectionInput(BaseModel):
    company_name: str
    candidates: list[DomainCandidateInput] = Field(default_factory=list)

    @classmethod
    def from_candidates(
        cls, company_name: str, candidates: list[DomainCandidate]
    ) -> "DomainSelectionInput":
        bounded = candidates[:MAX_DOMAIN_CANDIDATES]
        return cls(
            company_name=company_name,
            candidates=[DomainCandidateInput(ref=c.ref, title=c.title) for c in bounded],
        )


def build_envelope(ctx_key: str, data: DomainSelectionInput) -> PromptEnvelope:
    candidates_block = "\n".join(f'- ref="{c.ref}" title="{c.title}"' for c in data.candidates)
    user = (
        f"Company: {data.company_name}\n"
        f"Candidates ({len(data.candidates)}):\n{candidates_block or '(none)'}\n\n"
        "Which candidate ref is this company's own official website? Return null if none is."
    )
    return PromptEnvelope(ctx_key=ctx_key, system=_SYSTEM, user=user, metadata={})
