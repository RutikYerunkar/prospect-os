"""V2-E — additive `contact_channels` API serialization (§5/§6/§7/§8).

Exercises `groundwork.api.routers.prospects._load_aggregate` directly against
a repository built from `session_factory`, the same low-level pattern
`tests/test_enrichment_last_known_good.py` already uses to drive
`ContactEnrichmentRepository` scenarios without a full pipeline run — the
canonical Demo run only ever issues ONE enrichment call per prospect, so the
`REFRESH_FAILED`/`REFRESH_FOUND_NOTHING` preserved-state paths can only be
observed by constructing a second call directly.

No new endpoint, no new persistence model, no migration — this file proves
the read-only aggregate serialization the router already extended.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from groundwork.api.routers.prospects import _load_aggregate
from groundwork.engine.runner import Repos
from groundwork.models.enums import EnrichmentAttemptStatus, EnrichmentOperation, EnrichmentOrigin
from groundwork.models.schemas import CompanySeed, Contact, ProviderEmailObservation, ProviderLinkedInObservation
from groundwork.providers.contact_base import (
    EnrichmentAttemptKind,
    EnrichmentAttemptTelemetry,
    PersonEnrichmentResult,
)
from groundwork.providers.demo.contact_enrichment import DEMO_EMAIL_STATUS_MAP
from groundwork.repositories.approvals import ApprovalRepository
from groundwork.repositories.plays import PlayRepository
from groundwork.timeutil import utcnow

# `_load_aggregate` computes staleness against the REAL wall clock
# (`groundwork.timeutil.utcnow()`), so "fresh" fixtures must be anchored to
# it too, not to an arbitrary fixed calendar date — a hardcoded past date
# would silently drift into "stale" as real time moves on.
NOW = utcnow()
OLD = NOW - timedelta(days=90)

_APPROVED_CHANNEL_FIELDS = {
    "channel", "identifier", "discovery_state", "verification_state", "identity_match_state",
    "derivation_version", "observed_at", "last_attempt_at", "last_attempt_status",
    "last_attempt_error_type", "origin", "provider", "stale", "stale_after_days", "preserved_state",
    "provider_confidence", "is_catch_all",
}
_FORBIDDEN_SUBSTRINGS = ("email_provider_status", "raw_digest", "provider_person_id", "api_key", "apikey")


def _telemetry(
    status: EnrichmentAttemptStatus, *, attempt: int = 1, error_type: str | None = None, at: datetime = NOW
) -> list[EnrichmentAttemptTelemetry]:
    return [
        EnrichmentAttemptTelemetry(
            provider="demo_fixture", operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=f"cg-{attempt}", attempt=attempt, attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=status, started_at=at, finished_at=at, latency_ms=1.0, error_type=error_type,
            input_digest="digest",
        )
    ]


def _matched_result(*, at: datetime = NOW) -> PersonEnrichmentResult:
    return PersonEnrichmentResult(
        matched=True,
        provider_person_id="demo-person-x",
        email=ProviderEmailObservation(
            address="priya@x.com", provider_status="verified", provider_confidence=0.92,
            is_catch_all=False, observed_at=at,
        ),
        linkedin=ProviderLinkedInObservation(
            profile_url="demo://linkedin/priya-x", asserted_full_name="Priya X",
            asserted_company_name="X Corp", asserted_company_domain="x.com", observed_at=at,
        ),
        origin=EnrichmentOrigin.DEMO_FIXTURE,
        raw_digest="rd-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, at=at),
    )


def _empty_result(*, at: datetime = NOW) -> PersonEnrichmentResult:
    return PersonEnrichmentResult(
        matched=False, provider_person_id=None, email=None, linkedin=None,
        origin=EnrichmentOrigin.DEMO_FIXTURE, raw_digest="rd-empty",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, at=at),
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


async def _channels_by_name(session_factory, prospect_id: str) -> dict[str, dict]:
    repos = Repos.build(session_factory)
    approvals = ApprovalRepository(session_factory)
    agg = await _load_aggregate(prospect_id, repos, approvals)
    return {c["channel"]: c for c in agg.contact_channels}, agg


async def test_fresh_success_has_no_preserved_state_and_is_not_stale(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)

    email = channels["email"]
    assert email["preserved_state"] is None
    assert email["stale"] is False
    assert email["stale_after_days"] == 30
    assert email["origin"] == "DEMO_FIXTURE"
    assert email["provider"] == "demo_fixture"
    assert email["provider_confidence"] == 0.92
    assert email["is_catch_all"] is False

    linkedin = channels["linkedin"]
    assert linkedin["preserved_state"] is None
    assert linkedin["origin"] == "DEMO_FIXTURE"
    assert linkedin["provider"] == "demo_fixture"
    # confidence/catch-all are email-shaped observations only
    assert linkedin["provider_confidence"] is None
    assert linkedin["is_catch_all"] is None


async def test_no_forbidden_or_unapproved_fields_are_exposed(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    for channel in channels.values():
        assert set(channel.keys()) == _APPROVED_CHANNEL_FIELDS
        serialized = repr(channel).lower()
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in serialized


async def test_stale_after_the_configured_window(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result(at=OLD)
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    assert channels["email"]["stale"] is True
    assert channels["linkedin"]["stale"] is True


async def test_provider_error_only_preserved_state_is_null_not_refresh_failed(session_factory) -> None:
    """No genuine provider-backed state ever existed — `PROVIDER_ERROR` IS
    the honest current state, nothing is being preserved underneath it."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.PROVIDER_ERROR, error_type="EnrichmentProviderUnavailable"),
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    assert channels["email"]["discovery_state"] == "PROVIDER_ERROR"
    assert channels["email"]["preserved_state"] is None
    assert channels["email"]["origin"] is None
    assert channels["email"]["provider"] is None


async def test_refresh_failed_preserves_a_real_prior_state(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=_telemetry(EnrichmentAttemptStatus.TIMEOUT, error_type="EnrichmentTimeout"),
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    email = channels["email"]
    assert email["discovery_state"] == "FOUND"
    assert email["verification_state"] == "VERIFIED"
    assert email["preserved_state"] == "REFRESH_FAILED"
    assert email["last_attempt_status"] == "TIMEOUT"

    linkedin = channels["linkedin"]
    assert linkedin["preserved_state"] == "REFRESH_FAILED"


async def test_refresh_found_nothing_preserves_a_real_prior_identifier(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    first = _matched_result(at=OLD)
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=first.telemetry, result=first, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )
    second = _empty_result(at=NOW)
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-2",
        telemetry=second.telemetry, result=second, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    email = channels["email"]
    assert email["identifier"] == "priya@x.com"
    assert email["last_attempt_status"] == "OK"
    assert email["preserved_state"] == "REFRESH_FOUND_NOTHING"

    linkedin = channels["linkedin"]
    assert linkedin["identifier"] == "demo://linkedin/priya-x"
    assert linkedin["preserved_state"] == "REFRESH_FOUND_NOTHING"


async def test_timestamp_tie_is_not_preserved(session_factory) -> None:
    """A degenerate but real edge case: the single observation this state
    derives from is (trivially) also the latest enrichment observation for
    the prospect — `latest_enrichment_observed_at == observed_at`, a tie,
    must read as `None`, never `REFRESH_FOUND_NOTHING`."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result(at=NOW)
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="demo_fixture", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )

    channels, _ = await _channels_by_name(session_factory, prospect_id)
    assert channels["email"]["preserved_state"] is None
    assert channels["linkedin"]["preserved_state"] is None


async def test_contact_channels_empty_when_no_enrichment_ever_ran(session_factory) -> None:
    repos = Repos.build(session_factory)
    _, prospect_id = await _new_prospect(session_factory, repos)

    channels, agg = await _channels_by_name(session_factory, prospect_id)
    assert channels == {}
    assert agg.contact_channels == []


async def test_contact_null_when_no_person_identity_row_exists(session_factory) -> None:
    repos = Repos.build(session_factory)
    _, prospect_id = await _new_prospect(session_factory, repos)

    _, agg = await _channels_by_name(session_factory, prospect_id)
    assert agg.contact is None


async def test_persona_is_a_boolean_at_the_api_layer(session_factory) -> None:
    """§15 regression: `ProspectContact.persona` is `bool` end to end — the
    frontend type bug fixed by this checkpoint mirrors this real shape."""
    repos = Repos.build(session_factory)
    _, prospect_id = await _new_prospect(session_factory, repos)
    await repos.prospect_data.upsert_contact(
        Contact(
            prospect_id=prospect_id, full_name="Priya X", title="VP Sales", persona_match=True,
            linkedin_url=None, email=None, verification="PERSONA_ONLY", evidence_ids=[],
        )
    )

    _, agg = await _channels_by_name(session_factory, prospect_id)
    assert agg.contact["persona"] is True
    assert isinstance(agg.contact["persona"], bool)
