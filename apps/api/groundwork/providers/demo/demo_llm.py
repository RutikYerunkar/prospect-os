"""DemoLLMProvider — fixture-backed `LLMProvider` (§11).

Same Protocol as a live LLM provider (`structured(envelope, schema, *,
ctx_key)`), same Pydantic-validated response shapes. For research extraction
it returns the fixture pack's already-structured facts (Demo Mode does not
simulate free-text NLP extraction — see §11's table). For personalization and
the score explanation it deterministically templates from whatever the
calling step placed in `envelope.metadata` — which is built only from that
step's own `ProspectContext`, never from the fixture pack — so there is no
path for one prospect's provider call to see another's data.
"""

from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel

from groundwork.models.llm_io import (
    LinkedInOutreachOutput,
    PersonalizationOutput,
    ResearchExtractionOutput,
    ScoreExplanationOutput,
)
from groundwork.models.schemas import (
    ClaimMapEntry,
    CompanyProfileFacts,
    EmployeeCountProfileFact,
    FundingEvent,
    HiringRole,
    IndustryProfileFact,
    LeadershipCandidate,
    ResearchFacts,
    TechMention,
)
from groundwork.providers.base import (
    FAILURE_TYPES,
    LLMAttemptKind,
    LLMAttemptStatus,
    LLMAttemptTelemetry,
    LLMOperation,
    LLMResult,
    PromptEnvelope,
    ProviderUnavailable,
    digest_of,
    parse_ctx_key,
    stable_seed,
)
from groundwork.providers.demo.fixtures import FixtureCompany, FixturePack

_JITTER_MIN_S = 0.03
_JITTER_MAX_S = 0.15


class DemoLLMProvider:
    name = "demo_llm"
    model = "demo-llm-v1"

    def __init__(self, pack: FixturePack, seed: int) -> None:
        self.pack = pack
        self.seed = seed
        self._attempt_counts: dict[str, int] = {}

    def _jitter(self, ctx_key: str) -> float:
        rng = random.Random(stable_seed(str(self.seed), "llm", ctx_key))
        return _JITTER_MIN_S + rng.random() * (_JITTER_MAX_S - _JITTER_MIN_S)

    def _maybe_fail(self, ctx_key: str, step_name: str) -> None:
        slug = None
        # Only research extraction is scripted to fail in the base fixtures,
        # but the mechanism is generic over any step name a fixture names.
        for company in self.pack.companies:
            if step_name in company.failure_script and ctx_key.endswith(f":{company.slug}:{step_name}"):
                slug = company.slug
                break
        if slug is None:
            return
        failure = self.pack.company_by_slug(slug).failure_script[step_name]
        attempt = self._attempt_counts.get(ctx_key, 0) + 1
        self._attempt_counts[ctx_key] = attempt
        if attempt <= failure.fail_attempts:
            exc_cls = FAILURE_TYPES.get(failure.error, ProviderUnavailable)
            raise exc_cls(f"scripted {failure.error} for {slug}/{step_name}, attempt {attempt}")

    async def structured(
        self, envelope: PromptEnvelope, schema: type[BaseModel], *, ctx_key: str, operation: LLMOperation
    ) -> LLMResult:
        _, _, step_name = parse_ctx_key(ctx_key) if ctx_key.count(":") >= 2 else (None, None, ctx_key)
        started = datetime.now(timezone.utc)
        await asyncio.sleep(self._jitter(ctx_key))

        if schema is ResearchExtractionOutput:
            output: BaseModel = self._research_extraction(envelope)
        elif schema is PersonalizationOutput:
            output = self._personalization(envelope)
        elif schema is LinkedInOutreachOutput:
            output = self._linkedin_personalization(envelope)
        elif schema is ScoreExplanationOutput:
            output = self._explanation(envelope)
        else:
            raise ValueError(f"DemoLLMProvider has no handler for schema {schema!r}")

        finished = datetime.now(timezone.utc)
        tokens_in = len(envelope.user.split())
        tokens_out = len(str(output).split())
        # DemoLLMProvider produces exactly one INITIAL/OK attempt per
        # logical call (Phase 3) — there is nothing to retry or repair
        # against a fixture-derived response.
        attempt = LLMAttemptTelemetry(
            attempt=1,
            attempt_kind=LLMAttemptKind.INITIAL,
            schema_round=0,
            transport_retry_index=0,
            status=LLMAttemptStatus.OK,
            started_at=started,
            finished_at=finished,
            latency_ms=(finished - started).total_seconds() * 1000,
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=tokens_in + tokens_out,
            input_digest=digest_of(envelope.user),
            output_digest=digest_of(output.model_dump(mode="json")),
        )
        return LLMResult(
            parsed=output,
            raw=output.model_dump(mode="json"),
            operation=operation,
            model=self.model,
            provider=self.name,
            prompt_version="demo-v1",
            attempts=[attempt],
        )

    def _claim_for(self, fixture: FixtureCompany, source_ref: str | None) -> str:
        if not source_ref:
            return ""
        source = fixture.source_by_ref(source_ref)
        return source.claim if source else ""

    def _research_extraction(self, envelope: PromptEnvelope) -> ResearchExtractionOutput:
        slug = envelope.metadata["company_slug"]
        reference_date = date.fromisoformat(envelope.metadata["reference_date"])
        fixture = self.pack.company_by_slug(slug)

        funding_events = [
            FundingEvent(
                stage=f.stage,
                amount_usd=f.amount_usd,
                announced_at=reference_date - timedelta(days=f.announced_days_ago),
                claim=self._claim_for(fixture, f.source_ref),
                source_ref=f.source_ref,
                evidence_ids=[],
            )
            for f in fixture.funding_events
        ]
        hiring_roles = [
            HiringRole(
                title=h.title,
                is_gtm=h.is_gtm,
                posted_at=reference_date - timedelta(days=h.posted_days_ago),
                claim=self._claim_for(fixture, h.source_ref),
                source_ref=h.source_ref,
                evidence_ids=[],
            )
            for h in fixture.hiring_roles
        ]
        tech_mentions = [
            TechMention(
                name=t.name,
                claim=self._claim_for(fixture, t.source_ref),
                source_ref=t.source_ref,
                evidence_ids=[],
            )
            for t in fixture.tech_mentions
        ]
        leadership = [
            LeadershipCandidate(
                full_name=leader.full_name,
                title=leader.title,
                is_persona_match=leader.is_persona_match,
                claim=self._claim_for(fixture, leader.source_ref),
                source_ref=leader.source_ref,
                evidence_ids=[],
            )
            for leader in fixture.leadership
        ]
        facts = ResearchFacts(
            company=fixture.to_company_seed(),
            funding_events=funding_events,
            hiring_roles=hiring_roles,
            tech_mentions=tech_mentions,
            leadership=leadership,
            profile=self._profile(fixture),
        )
        return ResearchExtractionOutput(facts=facts)

    def _profile(self, fixture: FixtureCompany) -> CompanyProfileFacts:
        """H1 Phase 8 — Demo Mode's profile facts are derived from the
        fixture's own `industry`/`employee_count` fields (its authored
        "ground truth"), citing whichever existing source ref the fixture
        names, and pass through the identical deterministic grounding path
        (`engine/steps/signals.py`) any other fact does — nothing here
        bypasses grounding; this is simulating what a real extraction
        would find, not a scoring shortcut. A company with no configured
        `*_profile_source_ref` deliberately gets no fact at all, exercising
        the UNKNOWN path.
        """
        industry = IndustryProfileFact()
        if fixture.industry_profile_source_ref:
            label = fixture.industry.replace("_", " ")
            industry = IndustryProfileFact(
                category=fixture.industry,
                claim=f"{fixture.name} operates in the {label} industry.",
                source_ref=fixture.industry_profile_source_ref,
                evidence_ids=[],
            )

        employee_count = EmployeeCountProfileFact()
        if fixture.employee_profile_source_ref:
            employee_count = EmployeeCountProfileFact(
                employee_count=fixture.employee_count,
                claim=f"{fixture.name} has approximately {fixture.employee_count} employees.",
                source_ref=fixture.employee_profile_source_ref,
                evidence_ids=[],
            )

        return CompanyProfileFacts(industry=industry, employee_count=employee_count)

    def _personalization(self, envelope: PromptEnvelope) -> PersonalizationOutput:
        meta = envelope.metadata
        company = meta["company_name"]
        persona_name = meta.get("persona_name") or meta.get("persona_title") or "there"
        signals: list[dict] = meta.get("signals", [])

        claim_map = [
            ClaimMapEntry(sentence=s["summary"], evidence_ids=[s["evidence_id"]]) for s in signals[:2]
        ]
        if claim_map:
            highlight = " ".join(entry.sentence for entry in claim_map)
            subject = f"Congrats on the news, {company}"
            body = (
                f"Hi {persona_name},\n\n"
                f"Congrats on the momentum at {company} — {highlight}\n\n"
                "Worth a quick conversation about how we could support your team?\n\n"
                "Best,\nThe Groundwork Team"
            )
        else:
            subject = f"Quick note for {company}"
            body = (
                f"Hi {persona_name},\n\n"
                f"I've been following {company}'s progress and would love to connect.\n\n"
                "Best,\nThe Groundwork Team"
            )
        return PersonalizationOutput(subject=subject, body=body, claim_map=claim_map)

    def _linkedin_personalization(self, envelope: PromptEnvelope) -> LinkedInOutreachOutput:
        """Deterministic templating, distinct wording from `_personalization`
        (no subject; shorter, more conversational) — same "template from
        `envelope.metadata`, never the fixture pack" isolation discipline."""
        meta = envelope.metadata
        company = meta["company_name"]
        persona_name = meta.get("persona_name") or meta.get("persona_title") or "there"
        signals: list[dict] = meta.get("signals", [])

        claim_map = [
            ClaimMapEntry(sentence=s["summary"], evidence_ids=[s["evidence_id"]]) for s in signals[:2]
        ]
        if claim_map:
            highlight = " ".join(entry.sentence for entry in claim_map)
            body = (
                f"Hi {persona_name} — saw the news at {company}: {highlight} "
                "Would love to connect and share how we could help. Open to a quick chat?"
            )
        else:
            body = (
                f"Hi {persona_name} — I've been following {company}'s progress and would love to "
                "connect. Open to a quick chat?"
            )
        return LinkedInOutreachOutput(body=body, claim_map=claim_map)

    def _explanation(self, envelope: PromptEnvelope) -> ScoreExplanationOutput:
        meta = envelope.metadata
        overall = meta["overall"]
        top_dimensions: list[dict] = meta.get("top_dimensions", [])
        if meta.get("disqualified"):
            text = f"Scored {overall}/100 — capped by a hard industry disqualifier regardless of other signals."
        elif top_dimensions:
            parts = ", ".join(f"{d['name']} ({d['contribution']:+.2f})" for d in top_dimensions)
            text = f"Scored {overall}/100, driven mainly by {parts}."
        else:
            text = f"Scored {overall}/100."
        return ScoreExplanationOutput(explanation=text)
