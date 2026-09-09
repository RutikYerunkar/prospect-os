"""§N.2 — `ContactEnrichmentRepository`'s last-known-good semantics (§3.6):
success derives channel state; a later failure preserves the identifier/
state/observed_at and updates only `last_attempt_*`; a first-ever failure
(no prior success) derives `PROVIDER_ERROR`; a later success replaces a
`PROVIDER_ERROR` state correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from groundwork.engine.runner import Repos
from groundwork.models.enums import Channel, EnrichmentAttemptStatus, EnrichmentOperation, EnrichmentOrigin
from groundwork.models.schemas import CompanySeed, ContactChannelState, ProviderEmailObservation, ProviderLinkedInObservation
from groundwork.providers.contact_base import (
    EnrichmentAttemptKind,
    EnrichmentAttemptTelemetry,
    PersonEnrichmentResult,
)
from groundwork.providers.demo.contact_enrichment import DEMO_EMAIL_STATUS_MAP
from groundwork.repositories.plays import PlayRepository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _telemetry(status: EnrichmentAttemptStatus, *, attempt: int = 1, error_type: str | None = None) -> list:
    return [
        EnrichmentAttemptTelemetry(
            provider="demo_fixture", operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=f"cg-{attempt}", attempt=attempt, attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=status, started_at=NOW, finished_at=NOW, latency_ms=1.0, error_type=error_type,
            input_digest="digest",
        )
    ]


def _matched_result() -> PersonEnrichmentResult:
    return PersonEnrichmentResult(
        matched=True,
        provider_person_id="demo-person-x",
        email=ProviderEmailObservation(address="priya@x.com", provider_status="verified", observed_at=NOW),
        linkedin=ProviderLinkedInObservation(
            profile_url="demo://linkedin/priya-x", asserted_full_name="Priya X",
            asserted_company_name="X Corp", asserted_company_domain="x.com", observed_at=NOW,
        ),
        origin=EnrichmentOrigin.DEMO_FIXTURE,
        raw_digest="rd-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK),
    )


async def _new_prospect(session_factory, repos: Repos) -> tuple[str, str]:
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="o", icp_spec={}, mode="demo")
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)
    company = CompanySeed(
        slug="x-corp", name="X Corp", domain="x.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=100,
    )
    company_id = await repos.companies.get_or_create(company, "x.com", "x corp", origin="demo_fixture")
    prospect_id = await repos.prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="domain:x.com", duplicate_of=None, status="RUNNING"
    )
    return run_id, prospect_id


async def test_success_creates_derived_channel_state(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()

    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["linkedin"].discovery_state == "RESOLVED"
    assert channels["linkedin"].identity_match_state == "STRONG_MATCH"
    assert channels["linkedin"].identifier == "demo://linkedin/priya-x"

    enrichments = await repos.contact_enrichment.get_contact_enrichments(prospect_id)
    assert len(enrichments) == 1
    assert enrichments[0].matched is True


async def test_first_ever_failure_produces_provider_error(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.PROVIDER_ERROR, error_type="EnrichmentProviderUnavailable"),
    )

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "PROVIDER_ERROR"
    assert channels["email"].identifier is None
    assert channels["linkedin"].discovery_state == "PROVIDER_ERROR"
    assert channels["email"].last_attempt_status == "PROVIDER_ERROR"
    assert channels["email"].last_attempt_error_type == "EnrichmentProviderUnavailable"

    enrichments = await repos.contact_enrichment.get_contact_enrichments(prospect_id)
    assert enrichments == [], "a failed call must never write a contact_enrichments row"


async def test_later_failure_preserves_prior_success_and_only_updates_attempt_telemetry(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()

    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    before = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}

    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=_telemetry(EnrichmentAttemptStatus.TIMEOUT, attempt=1, error_type="EnrichmentTimeout"),
    )
    after = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}

    # State, identifier, observed_at and derived_from_enrichment_id are
    # UNTOUCHED by the later failure.
    assert after["email"].discovery_state == before["email"].discovery_state == "FOUND"
    assert after["email"].verification_state == before["email"].verification_state == "VERIFIED"
    assert after["email"].identifier == before["email"].identifier == "priya@x.com"
    assert after["email"].observed_at == before["email"].observed_at
    assert after["linkedin"].discovery_state == before["linkedin"].discovery_state == "RESOLVED"
    assert after["linkedin"].identity_match_state == before["linkedin"].identity_match_state == "STRONG_MATCH"

    # ONLY last_attempt_* changed.
    assert after["email"].last_attempt_status == "TIMEOUT"
    assert after["email"].last_attempt_error_type == "EnrichmentTimeout"
    assert after["email"].last_attempt_at != before["email"].last_attempt_at


def _empty_result() -> PersonEnrichmentResult:
    """A SUCCESSFUL call that legitimately found nothing — matched=False,
    both channel observations absent. Provider-neutral: nothing here reads a
    provider's name."""
    return PersonEnrichmentResult(
        matched=False, provider_person_id=None, email=None, linkedin=None,
        origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd-empty",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, attempt=1),
    )


async def test_first_ever_empty_success_stores_not_found(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=_empty_result().telemetry, result=_empty_result(), email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "NOT_FOUND"
    assert channels["email"].identifier is None
    assert channels["linkedin"].discovery_state == "NOT_FOUND"
    enrichments = await repos.contact_enrichment.get_contact_enrichments(prospect_id)
    assert len(enrichments) == 1, "an empty success is still an observation, and IS persisted"


async def test_later_successful_but_empty_call_preserves_prior_email_and_linkedin(session_factory) -> None:
    """§Part 7 fix: a prior success followed by a LATER successful call that
    itself found nothing must preserve the prior identifier/state/
    observed_at — only `last_attempt_*` moves, exactly like a failed call."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()

    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    before = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}

    empty = _empty_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=empty.telemetry, result=empty, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    after = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}

    assert after["email"].discovery_state == before["email"].discovery_state == "FOUND"
    assert after["email"].verification_state == before["email"].verification_state == "VERIFIED"
    assert after["email"].identifier == before["email"].identifier == "priya@x.com"
    assert after["email"].observed_at == before["email"].observed_at
    assert after["email"].last_attempt_status == "OK"
    assert after["email"].last_attempt_at != before["email"].last_attempt_at

    assert after["linkedin"].discovery_state == before["linkedin"].discovery_state == "RESOLVED"
    assert after["linkedin"].identity_match_state == before["linkedin"].identity_match_state == "STRONG_MATCH"
    assert after["linkedin"].identifier == before["linkedin"].identifier == "demo://linkedin/priya-x"

    # The empty call is still a real observation row, persisted alongside
    # the earlier one — only `contact_channels` is preserved, never
    # `contact_enrichments`.
    enrichments = await repos.contact_enrichment.get_contact_enrichments(prospect_id)
    assert len(enrichments) == 2


async def test_empty_success_then_later_real_success_becomes_current(session_factory) -> None:
    """The mirror case: an empty-first call followed by a later call that
    DOES find something must become the current state — the empty-guard
    only blocks a later EMPTY call, never a later real one."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    empty = _empty_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=empty.telemetry, result=empty, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "NOT_FOUND"

    result = _matched_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["email"].identifier == "priya@x.com"
    assert channels["linkedin"].discovery_state == "RESOLVED"
    assert channels["linkedin"].identifier == "demo://linkedin/priya-x"


async def test_success_after_prior_failure_replaces_provider_error_state(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.PROVIDER_ERROR, error_type="EnrichmentProviderUnavailable"),
    )
    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "PROVIDER_ERROR"

    result = _matched_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["email"].identifier == "priya@x.com"


# --- v2 §V2-F: `record_success`/`record_failure` return the AUTHORITATIVE
# post-write `ContactChannelState` list — the exact hand-off
# `EnrichmentCallRecorder` -> `engine/enrichment.py::call_enrichment` ->
# `ctx.contact_channels` -> `domain/review.py::run_checks` threads through,
# never re-derived or re-queried downstream.


async def test_record_success_returns_authoritative_channel_states(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()

    states = await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    assert {s.channel for s in states} == {Channel.EMAIL, Channel.LINKEDIN}
    email_state = next(s for s in states if s.channel is Channel.EMAIL)
    linkedin_state = next(s for s in states if s.channel is Channel.LINKEDIN)
    assert isinstance(email_state, ContactChannelState)
    assert email_state.identifier == "priya@x.com"
    assert email_state.discovery_state == "FOUND"
    assert email_state.verification_state == "VERIFIED"
    assert email_state.derived_from_enrichment_id is not None
    assert linkedin_state.discovery_state == "RESOLVED"
    assert linkedin_state.identity_match_state == "STRONG_MATCH"

    # The rows this call wrote must match what it handed back — no drift
    # between "what was persisted" and "what the caller was told".
    persisted = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    assert email_state.identifier == persisted["email"].identifier
    assert email_state.derived_from_enrichment_id == persisted["email"].derived_from_enrichment_id


async def test_record_failure_returns_authoritative_channel_states(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    states = await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.PROVIDER_ERROR, error_type="EnrichmentProviderUnavailable"),
    )

    assert {s.channel for s in states} == {Channel.EMAIL, Channel.LINKEDIN}
    email_state = next(s for s in states if s.channel is Channel.EMAIL)
    assert email_state.discovery_state == "PROVIDER_ERROR"
    assert email_state.identifier is None
    assert email_state.derived_from_enrichment_id is None
