from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from groundwork.prompts.base import UNTRUSTED_SOURCE_NOTICE, bound_snippet, delimit_untrusted
from groundwork.providers.base import PromptEnvelope, SourceDocument

PROMPT_VERSION = "research_extraction-v1"

_SYSTEM = (
    "You are the Research Agent in Groundwork, a GTM prospect-research pipeline. "
    "Extract structured facts (funding events, hiring roles, technology mentions, "
    "leadership candidates) from the provided source documents for one company. "
    f"{UNTRUSTED_SOURCE_NOTICE} "
    "Every fact you extract must cite the `source_ref` of the one source it came "
    "from, and its `claim` must be a faithful paraphrase of that source's text — "
    "never invent a fact with no supporting source."
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
    industry: str
    size_band: str
    reference_date: date
    sources: list[ResearchSourceInput] = Field(default_factory=list)

    @classmethod
    def from_context(cls, *, company, reference_date: date, docs: list[SourceDocument]) -> "ResearchExtractionInput":
        return cls(
            company_slug=company.slug,
            company_name=company.name,
            company_domain=company.domain,
            industry=company.industry,
            size_band=company.size_band,
            reference_date=reference_date,
            sources=[ResearchSourceInput(ref=d.ref, title=d.title, text=d.text) for d in docs],
        )


def build_envelope(ctx_key: str, data: ResearchExtractionInput) -> PromptEnvelope:
    sources_block = "\n\n".join(
        delimit_untrusted("source", s.ref, f"{s.title}\n{bound_snippet(s.text)}") for s in data.sources
    )
    user = (
        f"Company: {data.company_name} ({data.company_domain})\n"
        f"Industry: {data.industry} · Size band: {data.size_band}\n"
        f"Reference date: {data.reference_date.isoformat()}\n\n"
        f"Extract funding events, hiring roles, technology mentions, and leadership "
        f"candidates from the {len(data.sources)} source(s) below. Cite each fact's "
        f"source_ref.\n\n{sources_block}"
    )
    return PromptEnvelope(
        ctx_key=ctx_key,
        system=_SYSTEM,
        user=user,
        metadata={"company_slug": data.company_slug, "reference_date": data.reference_date.isoformat()},
    )
