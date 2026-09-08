"""V2-I-a — `ContactEnrichmentRepository.record_legal_restriction` persistence
semantics: a historical last-known-good EMAIL identifier/state is preserved
byte-for-byte, local suppression metadata is written, no NEW
`contact_enrichments` observation row is created, and — when a real
identifier was already on record — the GLOBAL `email_suppressions` row is
upserted, keyed by `normalize_email_identity` (the SAME normalization the
recipient-level duplicate-send rule already uses). A repeated restriction is
sticky; a later successful observation never clears either suppression.
LinkedIn only gets attempt-telemetry treatment — it is never suppressed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from groundwork.domain.contact_identity import normalize_email_identity
from groundwork.engine.runner import Repos
from groundwork.models.enums import EnrichmentAttemptStatus, EnrichmentOperation, EnrichmentOrigin
from groundwork.models.schemas import CompanySeed, ProviderEmailObservation, ProviderLinkedInObservation
from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptTelemetry, PersonEnrichmentResult
from groundwork.providers.live.hunter_enrichment import HUNTER_EMAIL_STATUS_MAP
from groundwork.repositories.plays import PlayRepository

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 2, tzinfo=timezone.utc)
EVEN_LATER = datetime(2026, 1, 3, tzinfo=timezone.utc)


def _telemetry(status: EnrichmentAttemptStatus, *, at: datetime = NOW) -> list[EnrichmentAttemptTelemetry]:
    return [
        EnrichmentAttemptTelemetry(
            provider="hunter",
            operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=f"cg-{at.isoformat()}",
            attempt=1,
            attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=status,
            started_at=at,
            finished_at=at,
            latency_ms=1.0,
            input_digest="digest",
        )
    ]


def _matched_result(*, address: str = "priya@x.com", observed_at: datetime = NOW) -> PersonEnrichmentResult:
    return PersonEnrichmentResult(
        matched=True,
        provider_person_id="hunter-person-x",
        email=ProviderEmailObservation(address=address, provider_status="valid", observed_at=observed_at),
        linkedin=None,
        origin=EnrichmentOrigin.LIVE_PROVIDER,
        raw_digest="rd-1",
        telemetry=_telemetry(EnrichmentAttemptStatus.OK, at=observed_at),
    )


async def _new_prospect(session_factory, repos: Repos) -> tuple[str, str]:
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="o", icp_spec={}, mode="live")
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=1)
    company = CompanySeed(
        slug="x-corp", name="X Corp", domain="x.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=100,
    )
    company_id = await repos.companies.get_or_create(company, "x.com", "x corp", origin="live_provider")
    prospect_id = await repos.prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="domain:x.com", duplicate_of=None, status="RUNNING"
    )
    return run_id, prospect_id


async def _record_success(repos: Repos, *, run_id: str, prospect_id: str, call_group_id: str, result) -> None:
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="hunter", call_group_id=call_group_id,
        telemetry=result.telemetry, result=result, email_status_map=HUNTER_EMAIL_STATUS_MAP,
        grounded_full_name="Priya X", grounded_company_name="X Corp", grounded_company_domain="x.com",
    )


async def _record_restriction(repos: Repos, *, run_id: str, prospect_id: str, call_group_id: str, at: datetime) -> None:
    await repos.contact_enrichment.record_legal_restriction(
        run_id=run_id, prospect_id=prospect_id, provider="hunter", call_group_id=call_group_id,
        telemetry=_telemetry(EnrichmentAttemptStatus.LEGAL_RESTRICTION, at=at), provider_code="claimed_email",
    )


async def test_prior_identifier_and_state_are_preserved(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    before = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email_before = before["email"]

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    after = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email_after = after["email"]
    assert email_after.identifier == email_before.identifier == "priya@x.com"
    assert email_after.discovery_state == email_before.discovery_state == "FOUND"
    assert email_after.verification_state == email_before.verification_state == "VERIFIED"
    assert email_after.observed_at == email_before.observed_at
    assert email_after.derived_from_enrichment_id == email_before.derived_from_enrichment_id
    assert email_after.last_attempt_status == "LEGAL_RESTRICTION"
    # `last_attempt_at` is stamped with real wall-clock time by the
    # repository (mirroring `record_failure`'s existing behavior), not the
    # synthetic telemetry timestamp passed in — only ordering is meaningful.
    assert email_after.last_attempt_at is not None
    assert email_after.last_attempt_at > email_before.observed_at


async def test_local_suppression_metadata_written(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email = channels["email"]
    assert email.send_suppressed_at is not None
    assert email.send_suppression_reason == "LEGAL_OR_PRIVACY_RESTRICTION"
    assert email.send_suppression_source == "hunter"
    assert email.send_suppression_provider_code == "claimed_email"


async def test_no_new_contact_enrichments_row_is_written(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    before_count = len(await repos.contact_enrichment.get_contact_enrichments(prospect_id))

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    after_count = len(await repos.contact_enrichment.get_contact_enrichments(prospect_id))
    assert after_count == before_count


async def test_global_suppression_written_keyed_by_normalized_identity(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    key = normalize_email_identity("priya@x.com")
    row = await repos.contact_enrichment.get_email_suppression(key)
    assert row is not None
    assert row.reason == "LEGAL_OR_PRIVACY_RESTRICTION"
    assert row.source == "hunter"
    assert row.provider_code == "claimed_email"
    assert row.observed_prospect_id == prospect_id

    # A case-variant of the same address normalizes to the SAME global key.
    assert await repos.contact_enrichment.get_email_suppression(normalize_email_identity("Priya@X.com")) is not None


async def test_no_prior_identifier_never_writes_global_suppression(session_factory) -> None:
    """No prior successful observation exists yet — 451 alone carries no
    email address, so there is nothing to key a global suppression row on."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", at=NOW)

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email = channels["email"]
    assert email.identifier is None
    assert email.send_suppressed_at is not None
    assert await repos.contact_enrichment.get_email_suppression(normalize_email_identity("priya@x.com")) is None


async def test_repeated_restriction_is_sticky_and_never_clears(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email = channels["email"]
    first_suppressed_at = email.send_suppressed_at
    assert first_suppressed_at is not None

    key = normalize_email_identity("priya@x.com")
    first_row = await repos.contact_enrichment.get_email_suppression(key)
    assert first_row is not None
    first_observed_at = first_row.first_observed_at

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-3", at=EVEN_LATER)

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email = channels["email"]
    # Sticky: never cleared, and the local audit timestamp moves forward.
    assert email.send_suppressed_at is not None
    assert email.send_suppressed_at >= first_suppressed_at
    assert email.send_suppression_reason == "LEGAL_OR_PRIVACY_RESTRICTION"

    row = await repos.contact_enrichment.get_email_suppression(key)
    assert row is not None
    # `first_observed_at` never moves once set; `last_observed_at` does.
    assert row.first_observed_at == first_observed_at
    assert row.last_observed_at >= first_observed_at


async def test_later_successful_observation_never_clears_suppression(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=_matched_result())
    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    # A later SUCCESSFUL call (e.g. a fresh lookup that finds the same
    # address again) must not clear the suppression it just set — locally
    # or globally.
    await _record_success(
        repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-3",
        result=_matched_result(observed_at=EVEN_LATER),
    )

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    email = channels["email"]
    assert email.send_suppressed_at is not None
    assert email.send_suppression_reason == "LEGAL_OR_PRIVACY_RESTRICTION"

    key = normalize_email_identity("priya@x.com")
    assert await repos.contact_enrichment.get_email_suppression(key) is not None


async def test_linkedin_channel_is_never_suppressed(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", at=NOW)

    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    linkedin = channels["linkedin"]
    assert linkedin.last_attempt_status == "LEGAL_RESTRICTION"
    assert linkedin.send_suppressed_at is None
    assert linkedin.send_suppression_reason is None
    assert linkedin.send_suppression_source is None
    assert linkedin.send_suppression_provider_code is None


async def test_linkedin_channel_still_not_suppressed_even_with_a_prior_resolved_profile(session_factory) -> None:
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_prospect(session_factory, repos)
    result = _matched_result()
    result = result.model_copy(
        update={
            "linkedin": ProviderLinkedInObservation(
                profile_url="https://www.linkedin.com/in/priya-x",
                asserted_full_name="Priya X",
                asserted_company_name="X Corp",
                asserted_company_domain="x.com",
                observed_at=NOW,
            )
        }
    )
    await _record_success(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-1", result=result)
    before = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    linkedin_before = before["linkedin"]

    await _record_restriction(repos, run_id=run_id, prospect_id=prospect_id, call_group_id="cg-2", at=LATER)

    after = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(prospect_id)}
    linkedin_after = after["linkedin"]
    assert linkedin_after.identifier == linkedin_before.identifier
    assert linkedin_after.discovery_state == linkedin_before.discovery_state == "RESOLVED"
    assert linkedin_after.send_suppressed_at is None
