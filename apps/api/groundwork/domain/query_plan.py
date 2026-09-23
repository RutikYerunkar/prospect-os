"""Deterministic query-plan primitives (H1 Phase 14; per-company templates
extended in v2.0.2 retrieval alignment).

No real provider calls happen anywhere in this module — this is the
offline, testable machinery an H2 live search adapter would drive. The LLM
never constructs an arbitrary search query string: every query this
pipeline could ever issue is rendered from one of these fixed, versioned
templates, over Play-derived parameters only.

v2.0.2: `build_source_queries()` now takes the full `PlaySpec` (not just a
company name) so per-company retrieval can target the Play's own
persona/technology parameters, never a generic fallback. `COMPANY_LEADERSHIP`
(a generic "leadership team about" query issued unconditionally) is removed
outright — leadership-flavored retrieval only happens when the Play actually
names `persona_titles`, via `COMPANY_PERSONA_LEADERSHIP`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from groundwork.models.schemas import PlaySpec

QUERY_PLAN_VERSION = "v2"


class QueryTemplateId(StrEnum):
    INDUSTRY_FUNDING = "industry_funding"
    INDUSTRY_PERSONA_HIRING = "industry_persona_hiring"
    INDUSTRY_TECHNOLOGY = "industry_technology"
    BREADTH = "breadth"
    OFFICIAL_SITE_DOMAIN = "official_site_domain"
    COMPANY_FUNDING = "company_funding"
    COMPANY_CAREERS = "company_careers"
    COMPANY_SIZE = "company_size"
    COMPANY_PERSONA_LEADERSHIP = "company_persona_leadership"
    COMPANY_TECHNOLOGY = "company_technology"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class QueryPlanEntry:
    template_id: QueryTemplateId
    query: str
    query_digest: str


def render_industry_funding(industry: str) -> str:
    return f"{industry} startup funding round announcement"


def render_industry_persona_hiring(industry: str, persona_titles: list[str]) -> str:
    persona = persona_titles[0] if persona_titles else "sales leadership"
    return f"{industry} company hiring {persona}"


def render_industry_technology(industry: str, technologies: list[str]) -> str:
    tech = " ".join(technologies[:3]) if technologies else "engineering stack"
    return f"{industry} company {tech}"


def render_breadth(industry: str) -> str:
    return f"{industry} companies news"


def render_official_site_domain(company_name: str) -> str:
    return f"{company_name} official website"


def render_company_funding(company_name: str) -> str:
    return f"{company_name} funding round investment"


def render_company_careers(company_name: str) -> str:
    return f"{company_name} careers hiring jobs"


def render_company_size(company_name: str) -> str:
    return f"{company_name} company size employees headcount"


def render_company_persona_leadership(company_name: str, persona_titles: list[str]) -> str:
    """Only ever called when `persona_titles` is non-empty — there is no
    generic leadership fallback (v2.0.2). Deterministic: always the Play's
    first-listed persona title, never an LLM-chosen one."""
    persona = persona_titles[0]
    return f"{company_name} {persona} leadership team"


def render_company_technology(company_name: str, target_technologies: list[str]) -> str:
    """Only ever called when `target_technologies` is non-empty (v2.0.2)."""
    tech = " ".join(target_technologies[:3])
    return f"{company_name} {tech} technology stack"


def build_query_plan(play_spec: PlaySpec, *, max_queries: int) -> list[QueryPlanEntry]:
    """The discovery-phase query plan for one Play — deterministic given
    the same `PlaySpec`, bounded at `max_queries`
    (`LIVE_MAX_PLAN_QUERIES_PER_RUN` in production). Order is fixed:
    funding, persona hiring, technology, breadth — the highest-signal
    templates first, so truncation to `max_queries` drops the least
    specific query first."""
    industry = play_spec.target_industries[0] if play_spec.target_industries else "company"
    candidates: list[tuple[QueryTemplateId, str]] = [
        (QueryTemplateId.INDUSTRY_FUNDING, render_industry_funding(industry)),
        (
            QueryTemplateId.INDUSTRY_PERSONA_HIRING,
            render_industry_persona_hiring(industry, play_spec.persona_titles),
        ),
        (
            QueryTemplateId.INDUSTRY_TECHNOLOGY,
            render_industry_technology(industry, play_spec.target_technologies),
        ),
        (QueryTemplateId.BREADTH, render_breadth(industry)),
    ]
    bounded = candidates[: max(max_queries, 0)]
    return [QueryPlanEntry(template_id=tid, query=q, query_digest=_digest(q)) for tid, q in bounded]


def build_domain_resolution_query(company_name: str) -> QueryPlanEntry:
    query = render_official_site_domain(company_name)
    return QueryPlanEntry(
        template_id=QueryTemplateId.OFFICIAL_SITE_DOMAIN, query=query, query_digest=_digest(query)
    )


def build_source_queries(
    company_name: str, play_spec: PlaySpec, *, max_queries: int
) -> list[QueryPlanEntry]:
    """Per-company category queries for real per-company source retrieval
    (H2 Phase 10; threaded with the full `PlaySpec` in v2.0.2), bounded at
    `LIVE_MAX_SOURCE_QUERIES_PER_PROSPECT`. Three categories are always
    issued — funding, careers/hiring, size — deterministically from the
    company's own display name alone. Two more are issued only when the
    Play actually targets that axis: `company_persona_leadership` iff
    `play_spec.persona_titles` is non-empty, `company_technology` iff
    `play_spec.target_technologies` is non-empty. There is no generic
    leadership fallback — a Play with no persona targets issues no
    leadership-flavored query at all. Order is fixed (funding, careers,
    size, persona_leadership, technology) so truncation below `max_queries`
    always drops the least Play-specific category first. The LLM never
    constructs these queries."""
    candidates: list[tuple[QueryTemplateId, str]] = [
        (QueryTemplateId.COMPANY_FUNDING, render_company_funding(company_name)),
        (QueryTemplateId.COMPANY_CAREERS, render_company_careers(company_name)),
        (QueryTemplateId.COMPANY_SIZE, render_company_size(company_name)),
    ]
    if play_spec.persona_titles:
        candidates.append(
            (
                QueryTemplateId.COMPANY_PERSONA_LEADERSHIP,
                render_company_persona_leadership(company_name, play_spec.persona_titles),
            )
        )
    if play_spec.target_technologies:
        candidates.append(
            (
                QueryTemplateId.COMPANY_TECHNOLOGY,
                render_company_technology(company_name, play_spec.target_technologies),
            )
        )
    bounded = candidates[: max(max_queries, 0)]
    return [QueryPlanEntry(template_id=tid, query=q, query_digest=_digest(q)) for tid, q in bounded]
