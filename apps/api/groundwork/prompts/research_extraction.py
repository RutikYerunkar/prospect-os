from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from groundwork.domain.industry import OTHER_CATEGORY, allowed_categories
from groundwork.models.schemas import PlaySpec
from groundwork.prompts.base import UNTRUSTED_SOURCE_NOTICE, bound_snippet, delimit_untrusted
from groundwork.providers.base import PromptEnvelope, SourceDocument

PROMPT_VERSION = "research_extraction-v2"

_SYSTEM = (
    "You are the Research Agent in Groundwork, a GTM prospect-research pipeline. "
    "Extract structured facts (funding events, hiring roles, technology mentions, "
    "leadership candidates, and a company profile) from the provided source "
    "documents for one company. "
    f"{UNTRUSTED_SOURCE_NOTICE} "
    "Every fact you extract must cite the `source_ref` of the one source it came "
    "from, and its `claim` must be a faithful paraphrase of that source's text — "
    "never invent a fact with no supporting source. "
    "For the company profile: select `profile.industry.category` ONLY from the "
    "served allowed-category list below (never free text) — if the company's "
    "industry is genuinely outside every category on that list, use the literal "
    f"value \"{OTHER_CATEGORY}\"; if you cannot classify it at all, omit the "
    "category. `profile.employee_count.employee_count` must be an exact integer "
    "that is explicitly written in the cited source's text — never estimate or "
    "infer a count from vague language like \"a large team.\" `profile.industry` "
    "and `profile.employee_count` are independent facts: cite whichever "
    "source(s) actually support each one — they need not be the same source, "
    "and citing the same source for both does not mean one automatically proves "
    "the other."
)


class ResearchSourceInput(BaseModel):
    ref: str
    title: str
    text: str


class ResearchExtractionInput(BaseModel):
    """Constructed only from a `ProspectContext` — company + this
    prospect's own fetched sources, nothing from any other prospect."""

    company_slug: str
    company_name: str
    company_domain: str
    reference_date: date
    sources: list[ResearchSourceInput] = Field(default_factory=list)
    # H1 Phase 5 — the closed, server-defined category set this Play's
    # industry classification must select from. Sorted for a stable prompt
    # (never affects behavior — this is presentation only, not a seed).
    allowed_industry_categories: list[str] = Field(default_factory=list)

    @classmethod
    def from_context(
        cls, *, company, reference_date: date, docs: list[SourceDocument], play_spec: PlaySpec
    ) -> "ResearchExtractionInput":
        return cls(
            company_slug=company.slug,
            company_name=company.name,
            company_domain=company.domain,
            reference_date=reference_date,
            sources=[ResearchSourceInput(ref=d.ref, title=d.title, text=d.text) for d in docs],
            allowed_industry_categories=sorted(allowed_categories(play_spec)),
        )


def build_envelope(ctx_key: str, data: ResearchExtractionInput) -> PromptEnvelope:
    sources_block = "\n\n".join(
        delimit_untrusted("source", s.ref, f"{s.title}\n{bound_snippet(s.text)}") for s in data.sources
    )
    categories_block = ", ".join(data.allowed_industry_categories)
    user = (
        f"Company: {data.company_name} ({data.company_domain})\n"
        f"Reference date: {data.reference_date.isoformat()}\n"
        f"Allowed industry categories (select `profile.industry.category` ONLY "
        f"from this list, or omit it): {categories_block}\n\n"
        f"Extract funding events, hiring roles, technology mentions, leadership "
        f"candidates, and a company profile (industry category + employee count) "
        f"from the {len(data.sources)} source(s) below. Cite each fact's "
        f"source_ref.\n\n{sources_block}"
    )
    return PromptEnvelope(
        ctx_key=ctx_key,
        system=_SYSTEM,
        user=user,
        metadata={"company_slug": data.company_slug, "reference_date": data.reference_date.isoformat()},
    )
