"""V2-J §1 — `evaluation.enrichment` metric definitions, pinned exactly per
the approved V2-J test matrix: zero-denominator -> None, empty counts/maps,
the FOUND denominator for `email_verified_rate`, catch-all NULL exclusion,
`NOT_FOUND` is not a provider error, `NOT_ATTEMPTED_BUDGET` excluded from
`attempted`, malformed `LIVE_PROVIDER` LinkedIn grammar rejection, preserved
last-known-good, stale-vs-never-observed, and cost/credit completeness.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from groundwork.engine.runner import Repos
from groundwork.models.enums import EnrichmentAttemptStatus, EnrichmentOperation, EnrichmentOrigin
from groundwork.models.schemas import CompanySeed, ProviderEmailObservation, ProviderLinkedInObservation
from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptTelemetry, PersonEnrichmentResult
from groundwork.providers.demo.contact_enrichment import DEMO_EMAIL_STATUS_MAP
from groundwork.repositories.actions import ActionRepository
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.repositories.plays import PlayRepository
from groundwork.evaluation.metrics import compute_run_evaluation

NOW = datetime.now(timezone.utc)


def _telemetry(status: EnrichmentAttemptStatus, *, provider: str = "demo_fixture", cg: str, cost=None, credits=None, latency=1.0):
    return [
        EnrichmentAttemptTelemetry(
            provider=provider, operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=cg, attempt=1, attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=status, started_at=NOW, finished_at=NOW, latency_ms=latency,
            cost_usd=cost, credits_used=credits, input_digest="d",
        )
    ]


async def _new_run(session_factory, repos: Repos) -> str:
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="o", icp_spec={}, mode="demo")
    return await repos.runs.create(play_id=play_id, mode="demo", seed=1)


async def _add_prospect(repos: Repos, run_id: str, *, slug: str) -> str:
    company = CompanySeed(
        slug=slug, name="X Corp", domain="x.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=100,
    )
    company_id = await repos.companies.get_or_create(company, "x.com", "x corp", origin="demo_fixture")
    return await repos.prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key=f"domain:x.com:{slug}", duplicate_of=None, status="RUNNING"
    )


async def _new_prospect(session_factory, repos: Repos, *, slug: str = "x-corp") -> tuple[str, str]:
    """Single-prospect convenience wrapper — its own fresh run."""
    run_id = await _new_run(session_factory, repos)
    prospect_id = await _add_prospect(repos, run_id, slug=slug)
    return run_id, prospect_id


async def _evaluate(session_factory, repos: Repos, run_id: str) -> dict:
    return await compute_run_evaluation(
        run_id, repos, actions=ActionRepository(session_factory), approvals=ApprovalRepository(session_factory)
    )


async def test_zero_denominator_returns_none_and_empty(session_factory):
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="empty", objective_text="t", icp_spec={}, mode="demo")
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["attempted"] == 0
    assert e["matched"] == 0
    assert e["match_rate"] is None
    assert e["email_found_rate"] is None
    assert e["email_verified_rate"] is None
    assert e["catch_all_rate"] is None
    assert e["linkedin_resolved_rate"] is None
    assert e["identity_match_distribution"] == {}
    assert e["identifier_grammar_rejections"] == 0
    assert e["provider_error_rate"] is None
    assert e["not_attempted_budget_count"] == 0
    assert e["enrichment_attempts_by_status"] == {}
    assert e["stale_channel_count"] == 0
    assert e["preserved_last_known_good_count"] == 0
    assert e["preserved_last_known_good_breakdown"] == {}
    assert e["p50_enrichment_latency_ms"] is None
    assert e["p95_enrichment_latency_ms"] is None
    assert e["enrichment_credits_used"] is None
    assert e["enrichment_cost_usd"] is None
    assert e["enrichment_calls"] == 0


async def test_attempted_matched_and_email_verified_rate_denominator_is_found(session_factory):
    repos = Repos.build(session_factory)
    run_id = await _new_run(session_factory, repos)
    prospect_id = await _add_prospect(repos, run_id, slug="v1")

    # One matched call with a VERIFIED email (found + verified).
    result_ok = PersonEnrichmentResult(
        matched=True, provider_person_id="p1",
        email=ProviderEmailObservation(address="a@x.com", provider_status="verified", observed_at=NOW),
        linkedin=None, origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, cg="cg-1"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result_ok.telemetry, result=result_ok, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    # A second prospect in the SAME run: matched, but email NOT_FOUND
    # (found_rate denominator dilution) and NOT verified.
    prospect_id2 = await _add_prospect(repos, run_id, slug="v2")
    result_no_email = PersonEnrichmentResult(
        matched=True, provider_person_id="p2", email=None, linkedin=None,
        origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd2",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, cg="cg-2"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id2, provider="demo_fixture", call_group_id="cg-2",
        telemetry=result_no_email.telemetry, result=result_no_email, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["attempted"] == 2
    assert e["matched"] == 2
    assert e["match_rate"] == 1.0
    # FOUND denominator = 1 (only the first prospect found an email);
    # attempted denominator = 2. found_rate uses `attempted`, verified_rate
    # uses FOUND — pinned distinction.
    assert e["email_found_rate"] == 0.5
    assert e["email_verified_rate"] == 1.0


async def test_catch_all_null_excluded_from_rate(session_factory):
    repos = Repos.build(session_factory)
    run_id = await _new_run(session_factory, repos)
    p1 = await _add_prospect(repos, run_id, slug="c1")
    p2 = await _add_prospect(repos, run_id, slug="c2")
    p3 = await _add_prospect(repos, run_id, slug="c3")

    async def _record(prospect_id, cg, is_catch_all):
        result = PersonEnrichmentResult(
            matched=True, provider_person_id="p",
            email=ProviderEmailObservation(
                address=f"{cg}@x.com", provider_status="verified", is_catch_all=is_catch_all, observed_at=NOW
            ),
            linkedin=None, origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest=cg,
            telemetry=_telemetry(EnrichmentAttemptStatus.OK, cg=cg),
        )
        await repos.contact_enrichment.record_success(
            run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id=cg,
            telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
            grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
        )

    await _record(p1, "cg-1", True)
    await _record(p2, "cg-2", False)
    await _record(p3, "cg-3", None)  # unknown -> excluded from both numerator and denominator

    evaluation = await _evaluate(session_factory, repos, run_id)
    assert evaluation["enrichment"]["catch_all_rate"] == 0.5


async def test_not_found_is_not_provider_error(session_factory):
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    result = PersonEnrichmentResult(
        matched=False, provider_person_id=None, email=None, linkedin=None,
        origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd",
        telemetry=_telemetry(EnrichmentAttemptStatus.NOT_FOUND, cg="cg-1"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["enrichment_attempts_by_status"] == {"NOT_FOUND": 1}
    assert e["provider_error_rate"] == 0.0
    assert e["attempted"] == 1


async def test_not_attempted_budget_excluded_from_attempted(session_factory):
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-budget",
        telemetry=_telemetry(EnrichmentAttemptStatus.NOT_ATTEMPTED_BUDGET, cg="cg-budget", latency=0.0),
    )

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["attempted"] == 0
    assert e["not_attempted_budget_count"] == 1
    assert e["enrichment_attempts_by_status"] == {"NOT_ATTEMPTED_BUDGET": 1}
    # NOT_ATTEMPTED_BUDGET never contributes to latency/cost/credits/calls.
    assert e["enrichment_calls"] == 0
    assert e["p50_enrichment_latency_ms"] is None
    assert e["enrichment_cost_usd"] is None
    assert e["enrichment_credits_used"] is None


async def test_malformed_live_provider_linkedin_grammar_rejected(session_factory):
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    # A LIVE_PROVIDER row carrying a demo:// identifier — rejected by both
    # the schema validator's own check and (independently) re-checked here
    # by the metric via `domain/contact_identity.py::validate_linkedin_identifier`.
    result = PersonEnrichmentResult(
        matched=True, provider_person_id="p1", email=None,
        linkedin=ProviderLinkedInObservation(profile_url="https://evil.example.com/in/x", observed_at=NOW),
        origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest="rd",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, provider="apollo", cg="cg-1"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="apollo", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map={},
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["identifier_grammar_rejections"] == 1
    # The rejected identifier never becomes RESOLVED.
    assert e["linkedin_resolved_rate"] == 0.0


async def test_preserved_last_known_good_counted(session_factory):
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    # First: a successful, real observation.
    result = PersonEnrichmentResult(
        matched=True, provider_person_id="p1",
        email=ProviderEmailObservation(address="a@x.com", provider_status="verified", observed_at=NOW),
        linkedin=None, origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, cg="cg-1"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    # Then: a failed refresh attempt — preserves state (REFRESH_FAILED).
    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=_telemetry(EnrichmentAttemptStatus.PROVIDER_ERROR, cg="cg-2"),
    )

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    # Both channels are provider-backed after the first successful call —
    # EMAIL found an address (FOUND), LINKEDIN definitively found none
    # (NOT_FOUND is itself a provider-backed, preservable state, distinct
    # from PROVIDER_ERROR/NOT_ATTEMPTED). The failed refresh preserves BOTH.
    assert e["preserved_last_known_good_count"] == 2
    assert e["preserved_last_known_good_breakdown"] == {"REFRESH_FAILED": 2}


async def test_stale_vs_never_observed(session_factory):
    """A channel that was observed once and has since aged past the 30-day
    threshold counts as stale; a channel with no observation at all
    (`observed_at is None`, e.g. NOT_ATTEMPTED) must never count as stale —
    it is simply not-yet-attempted."""
    from datetime import timedelta

    from sqlalchemy import select

    from groundwork.models.tables import ContactChannelRow

    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos, slug="stale1")

    result = PersonEnrichmentResult(
        matched=True, provider_person_id="p1",
        email=ProviderEmailObservation(address="a@x.com", provider_status="verified", observed_at=NOW),
        linkedin=None, origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, cg="cg-1"),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    # Age the EMAIL channel's observed_at well past the 30-day default.
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(ContactChannelRow).where(
                    ContactChannelRow.prospect_id == prospect_id, ContactChannelRow.channel == "email"
                )
            )
        ).scalars().all()
        for row in rows:
            row.observed_at = NOW - timedelta(days=60)
        await session.commit()

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    # EMAIL is stale (observed, aged past threshold); LINKEDIN was also
    # observed (NOT_FOUND) but is recent -> not stale.
    assert e["stale_channel_count"] == 1


async def test_cost_and_credit_completeness(session_factory):
    repos = Repos.build(session_factory)
    run_id = await _new_run(session_factory, repos)
    p1 = await _add_prospect(repos, run_id, slug="cost1")
    p2 = await _add_prospect(repos, run_id, slug="cost2")

    async def _record(prospect_id, cg, provider, cost, credits):
        result = PersonEnrichmentResult(
            matched=False, provider_person_id=None, email=None, linkedin=None,
            origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest=cg,
            telemetry=_telemetry(EnrichmentAttemptStatus.NOT_FOUND, provider=provider, cg=cg, cost=cost, credits=credits),
        )
        await repos.contact_enrichment.record_success(
            run_id=run_id, prospect_id=prospect_id, provider=provider, call_group_id=cg,
            telemetry=result.telemetry, result=result, email_status_map={},
            grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
        )

    # Single provider, complete cost+credits -> both sum.
    await _record(p1, "cg-1", "apollo", 0.10, 1.0)
    await _record(p2, "cg-2", "apollo", 0.20, 2.0)

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    assert e["enrichment_cost_usd"] == pytest.approx(0.30)
    assert e["enrichment_credits_used"] == pytest.approx(3.0)
    assert e["enrichment_calls"] == 2


async def test_cross_provider_credits_incomparable_returns_none(session_factory):
    repos = Repos.build(session_factory)
    run_id = await _new_run(session_factory, repos)
    p1 = await _add_prospect(repos, run_id, slug="xp1")
    p2 = await _add_prospect(repos, run_id, slug="xp2")

    async def _record(prospect_id, cg, provider, cost, credits):
        result = PersonEnrichmentResult(
            matched=False, provider_person_id=None, email=None, linkedin=None,
            origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest=cg,
            telemetry=_telemetry(EnrichmentAttemptStatus.NOT_FOUND, provider=provider, cg=cg, cost=cost, credits=credits),
        )
        await repos.contact_enrichment.record_success(
            run_id=run_id, prospect_id=prospect_id, provider=provider, call_group_id=cg,
            telemetry=result.telemetry, result=result, email_status_map={},
            grounded_full_name=None, grounded_company_name="X Corp", grounded_company_domain="x.com",
        )

    await _record(p1, "cg-1", "apollo", 0.10, 1.0)
    await _record(p2, "cg-2", "hunter", 0.05, 5.0)

    evaluation = await _evaluate(session_factory, repos, run_id)
    e = evaluation["enrichment"]
    # Cost (USD) is universally comparable -> still sums.
    assert e["enrichment_cost_usd"] == pytest.approx(0.15)
    # Credits are provider-native units -> multiple distinct providers -> None.
    assert e["enrichment_credits_used"] is None
