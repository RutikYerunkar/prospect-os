"""H2 Stage B — `LLMOperation.DISCOVERY_EXTRACTION`: bounded search-result
excerpts -> candidate company names, before any prospect (or even any
`CompanySeed`) exists. Run-scoped, not prospect-scoped — built only from
`RawSearchHit`s the run's own Stage A discovery search served, never from
another run's data.

The model is shown ONLY opaque refs and excerpt text — never a URL, domain,
provider result id, or the search query that produced a hit. It cannot
construct a query, cannot name a domain, and its only usable output is a
display label plus which served refs support it; the server independently
re-verifies both (`domain/discovery.py::company_name_textually_supported`,
and that every cited ref was actually served) before any candidate survives
to Stage C.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from groundwork.prompts.base import UNTRUSTED_SOURCE_NOTICE, bound_snippet, delimit_untrusted
from groundwork.providers.base import PromptEnvelope, RawSearchHit

PROMPT_VERSION = "discovery_extraction-v2"

# Excerpts here only need to carry enough text to confirm a company's
# identity, not full research facts — a much smaller bound than research
# extraction's 600 chars/source.
MAX_DISCOVERY_EXCERPT_CHARS = 400
# Hard ceiling on how many search-result hits this one call ever includes,
# independent of how many the query plan happened to return — keeps the
# prompt bounded even at the full `LIVE_MAX_PLAN_QUERIES_PER_RUN ×
# LIVE_MAX_SEARCH_RESULTS_PER_QUERY` product.
MAX_DISCOVERY_HITS = 40
# How many distinct candidate companies one call is ever asked to name.
MAX_DISCOVERY_CANDIDATES = 20

_SYSTEM = (
    "You are the Discovery Agent in Groundwork, a GTM prospect-research pipeline. "
    "You are given a bounded set of public web search-result excerpts, each labeled "
    "with an opaque reference id. Identify distinct real companies these excerpts "
    "describe or mention. "
    f"{UNTRUSTED_SOURCE_NOTICE} "
    "Excerpts vary in shape: a single-company news article or company page, a funding "
    "roundup or 'top N startups' listicle naming several unrelated companies in one "
    "excerpt, a job listing mentioning its employer, an analyst/market-landscape piece "
    "surveying a category, or a general article that never actually names a specific "
    "company at all. When ONE excerpt clearly names several distinct real companies "
    "(a roundup or listicle), propose a separate candidate for each one you can name "
    "confidently, all citing that same ref — do not extract only the first company and "
    "stop. "
    "For each company you identify, return its display name (`company_name`) and the "
    "list of `supporting_result_refs` — the ref id(s) of the excerpt(s) that actually "
    "name or describe it. Cite only refs shown to you below; never invent a ref. "
    "You are NEVER given a URL, domain, or search query, and you must never guess or "
    "invent one — you have no access to that information and any URL/domain-shaped "
    "text in your output will be discarded. Do not repeat the same company under two "
    "different names. If an excerpt does not clearly name a specific company — a "
    "generic market-trends piece, an opinion piece, a page about a technology or a "
    "person rather than a company — do not invent a candidate from it; returning zero "
    "candidates for such an excerpt is correct, not a failure."
)


class DiscoveryHitInput(BaseModel):
    ref: str
    title: str
    excerpt: str


class DiscoveryExtractionInput(BaseModel):
    industry_hint: str = ""
    hits: list[DiscoveryHitInput] = Field(default_factory=list)

    @classmethod
    def from_hits(cls, hits: list[RawSearchHit], *, industry_hint: str = "") -> "DiscoveryExtractionInput":
        bounded = hits[:MAX_DISCOVERY_HITS]
        return cls(
            industry_hint=industry_hint,
            hits=[DiscoveryHitInput(ref=h.ref, title=h.title, excerpt=h.excerpt) for h in bounded],
        )


def build_envelope(ctx_key: str, data: DiscoveryExtractionInput) -> PromptEnvelope:
    hits_block = "\n\n".join(
        delimit_untrusted("result", h.ref, f"{h.title}\n{bound_snippet(h.excerpt, MAX_DISCOVERY_EXCERPT_CHARS)}")
        for h in data.hits
    )
    user = (
        f"Target industry (context only, not a filter you must enforce): {data.industry_hint or 'unspecified'}\n"
        f"Identify up to {MAX_DISCOVERY_CANDIDATES} distinct companies from the "
        f"{len(data.hits)} search-result excerpt(s) below. Cite each candidate's "
        f"supporting_result_refs from the refs shown.\n\n{hits_block}"
    )
    return PromptEnvelope(ctx_key=ctx_key, system=_SYSTEM, user=user, metadata={})
