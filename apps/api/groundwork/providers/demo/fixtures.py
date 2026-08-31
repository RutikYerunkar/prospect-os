"""Loader + typed schema for the Demo Mode fixture pack (§23).

The fixture pack contains evidence, not verdicts: sources, structured facts,
and scripted provider failures. Every score, status, duplicate flag and
review result is computed by the real engine at run time from this evidence —
nothing here is a precomputed outcome.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from groundwork.models.enums import SignalType
from groundwork.models.schemas import CompanySeed, PlaySpec

DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "demo_pack.yaml"


class FixtureSource(BaseModel):
    ref: str
    title: str
    claim: str
    snippet: str
    signal_type: SignalType | None = None
    confidence: float = 0.85


class FixtureFundingEvent(BaseModel):
    stage: str
    amount_usd: float | None = None
    announced_days_ago: int
    source_ref: str


class FixtureHiringRole(BaseModel):
    title: str
    is_gtm: bool = True
    posted_days_ago: int
    source_ref: str


class FixtureTechMention(BaseModel):
    name: str
    source_ref: str


class FixtureLeadership(BaseModel):
    full_name: str | None = None
    title: str
    is_persona_match: bool = False
    source_ref: str


class FixtureFailureSpec(BaseModel):
    fail_attempts: int = 0
    error: str = "ProviderTimeout"


class FixtureCompany(BaseModel):
    slug: str
    name: str
    domain: str
    industry: str
    size_band: str
    employee_count: int
    hq_country: str = "US"
    description: str = ""
    sources: list[FixtureSource] = Field(default_factory=list)
    funding_events: list[FixtureFundingEvent] = Field(default_factory=list)
    hiring_roles: list[FixtureHiringRole] = Field(default_factory=list)
    tech_mentions: list[FixtureTechMention] = Field(default_factory=list)
    leadership: list[FixtureLeadership] = Field(default_factory=list)
    failure_script: dict[str, FixtureFailureSpec] = Field(default_factory=dict)

    def source_by_ref(self, ref: str) -> FixtureSource | None:
        return next((s for s in self.sources if s.ref == ref), None)

    def to_company_seed(self) -> CompanySeed:
        return CompanySeed(
            slug=self.slug,
            name=self.name,
            domain=self.domain,
            industry=self.industry,
            size_band=self.size_band,
            employee_count=self.employee_count,
            hq_country=self.hq_country,
            description=self.description,
        )


class FixturePack(BaseModel):
    play_spec: PlaySpec
    companies: list[FixtureCompany]

    def company_by_slug(self, slug: str) -> FixtureCompany:
        for company in self.companies:
            if company.slug == slug:
                return company
        raise KeyError(f"no fixture company with slug {slug!r}")


@lru_cache(maxsize=4)
def load_fixture_pack(path: Path | str = DEFAULT_FIXTURE_PATH) -> FixturePack:
    raw = yaml.safe_load(Path(path).read_text())
    return FixturePack(**raw)
