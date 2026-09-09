"""V2-I-a — `domain/action_policy.py::evaluate()` clause 15
(`recipient_suppressed`). Applies to EMAIL_SEND in BOTH `DEMO_SIMULATED` and
`LIVE_EXTERNAL` origins (unlike clause 12, which is LIVE_EXTERNAL-only); has
no override; and is verified never to affect `LINKEDIN_COPY_AND_OPEN`.
Cross-prospect/cross-run global suppression is exercised at the repository
level in `test_send_suppression_persistence.py` and end-to-end in
`test_suppression_execute_time.py` — this file is pure-function coverage of
the policy clause itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from groundwork.domain.action_policy import evaluate
from groundwork.domain.content_hash import HASH_VERSION, content_hash
from groundwork.engine.runner import Repos
from groundwork.models.enums import (
    ActionExecutionOrigin,
    ActionPolicyVerdict,
    ActionType,
    Channel,
    EmailDiscoveryState,
    EmailVerificationState,
    EnrichmentAttemptStatus,
    EnrichmentOperation,
    EnrichmentOrigin,
    LinkedInIdentityState,
    LinkedInResolutionState,
    ProspectStatus,
    ReviewVerdict,
)
from groundwork.models.schemas import CompanySeed, ProviderEmailObservation
from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptTelemetry, PersonEnrichmentResult
from groundwork.providers.live.hunter_enrichment import HUNTER_EMAIL_STATUS_MAP
from groundwork.repositories.plays import PlayRepository
from tests.action_helpers import find_draft, get_aggregate, propose, run_demo_play_to_completion

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _email_kwargs(**overrides) -> dict:
    h = content_hash(
        channel=Channel.EMAIL,
        sender_identifier="demo-sender@groundwork.invalid",
        recipient_identifier="priya.natarajan@northwindlabs.com",
        subject="Hi",
        body="Hi Priya",
    )
    base = dict(
        action_type=ActionType.EMAIL_SEND,
        origin=ActionExecutionOrigin.LIVE_EXTERNAL,
        review_verdict=ReviewVerdict.PASS,
        prospect_status=ProspectStatus.PASS,
        draft_channel=Channel.EMAIL,
        draft_subject="Hi",
        draft_body="Hi Priya",
        proposal_content_hash=h,
        recomputed_content_hash=h,
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        email_discovery_state=EmailDiscoveryState.FOUND,
        email_verification_state=EmailVerificationState.VERIFIED,
        email_observed_at=NOW,
        recipient_identifier="priya.natarajan@northwindlabs.com",
        connected_sender_identifier="demo-sender@groundwork.invalid",
        proposal_sender_identifier="demo-sender@groundwork.invalid",
        send_provider_configured=True,
        now=NOW,
    )
    base.update(overrides)
    return base


def _linkedin_kwargs(**overrides) -> dict:
    h = content_hash(
        channel=Channel.LINKEDIN,
        sender_identifier=None,
        recipient_identifier="demo://linkedin/priya-natarajan",
        subject=None,
        body="Hi Priya",
    )
    base = dict(
        action_type=ActionType.LINKEDIN_COPY_AND_OPEN,
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
        review_verdict=ReviewVerdict.PASS,
        prospect_status=ProspectStatus.PASS,
        draft_channel=Channel.LINKEDIN,
        draft_subject=None,
        draft_body="Hi Priya",
        proposal_content_hash=h,
        recomputed_content_hash=h,
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        linkedin_discovery_state=LinkedInResolutionState.RESOLVED,
        linkedin_identity_match_state=LinkedInIdentityState.STRONG_MATCH,
        linkedin_observed_at=NOW,
        now=NOW,
    )
    base.update(overrides)
    return base


def test_not_suppressed_is_eligible():
    result = evaluate(**_email_kwargs(recipient_suppressed=False))
    assert result.verdict is ActionPolicyVerdict.ELIGIBLE
    assert "recipient_suppressed" not in result.blocked_reasons


def test_suppressed_blocks_live_external():
    result = evaluate(**_email_kwargs(origin=ActionExecutionOrigin.LIVE_EXTERNAL, recipient_suppressed=True))
    assert result.verdict is ActionPolicyVerdict.BLOCKED
    assert "recipient_suppressed" in result.blocked_reasons


def test_suppressed_blocks_demo_simulated_too():
    """Unlike clause 12 (LIVE_EXTERNAL-only), clause 15 applies to BOTH
    origins — a legal/privacy restriction is a fact about the real-world
    recipient identity, not about which origin is sending."""
    result = evaluate(**_email_kwargs(origin=ActionExecutionOrigin.DEMO_SIMULATED, recipient_suppressed=True))
    assert result.verdict is ActionPolicyVerdict.BLOCKED
    assert "recipient_suppressed" in result.blocked_reasons


def test_suppressed_is_independent_of_every_other_clause():
    """Even an otherwise fully-eligible proposal (VERIFIED email, PASS
    verdict, matching sender/hash) is blocked, alone, by suppression — no
    other clause needs to fail."""
    result = evaluate(**_email_kwargs(recipient_suppressed=True))
    assert result.blocked_reasons == ["recipient_suppressed"]


def test_no_override_field_exists_to_lift_suppression():
    """There is no kwarg that lifts a suppressed verdict back to ELIGIBLE
    other than `recipient_suppressed` itself being False — no override path
    (D7) is threaded through `evaluate()` for this clause."""
    result = evaluate(**_email_kwargs(recipient_suppressed=True, email_verification_state=EmailVerificationState.VERIFIED))
    assert "recipient_suppressed" in result.blocked_reasons


def test_recipient_suppressed_never_appears_for_linkedin_copy_and_open():
    """`recipient_suppressed` is only ever evaluated inside the EMAIL_SEND
    branch — passing it True must never affect a LINKEDIN_COPY_AND_OPEN
    proposal (LinkedIn has no send-suppression concept at all)."""
    result = evaluate(**_linkedin_kwargs(recipient_suppressed=True))
    assert result.verdict is ActionPolicyVerdict.ELIGIBLE
    assert "recipient_suppressed" not in result.blocked_reasons


def test_recipient_suppressed_defaults_to_false():
    """The kwarg is optional — omitting it entirely must never suppress."""
    kwargs = _email_kwargs()
    kwargs.pop("recipient_suppressed", None)
    result = evaluate(**kwargs)
    assert result.verdict is ActionPolicyVerdict.ELIGIBLE


# --- cross-prospect/cross-run GLOBAL suppression, exercised end-to-end
# through the real API against the canonical Demo (`api/routers/actions.py`'s
# `_evaluate_policy` is what actually threads `email_suppressions` into the
# clause above — these tests prove that wiring, not just the pure clause).


def _ok_telemetry(at: datetime) -> list[EnrichmentAttemptTelemetry]:
    return [
        EnrichmentAttemptTelemetry(
            provider="hunter", operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=f"cg-ok-{at.isoformat()}", attempt=1, attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=EnrichmentAttemptStatus.OK, started_at=at, finished_at=at, latency_ms=1.0,
            input_digest="digest",
        )
    ]


def _restriction_telemetry(at: datetime) -> list[EnrichmentAttemptTelemetry]:
    return [
        EnrichmentAttemptTelemetry(
            provider="hunter", operation=EnrichmentOperation.PERSON_ENRICHMENT,
            call_group_id=f"cg-restrict-{at.isoformat()}", attempt=1, attempt_kind=EnrichmentAttemptKind.INITIAL,
            status=EnrichmentAttemptStatus.LEGAL_RESTRICTION, started_at=at, finished_at=at, latency_ms=1.0,
            input_digest="digest",
        )
    ]


async def _new_synthetic_prospect(session_factory, repos: Repos) -> tuple[str, str]:
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="o", icp_spec={}, mode="live")
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=1)
    company = CompanySeed(
        slug="other-co", name="Other Co", domain="other.example.com", industry="ai_infrastructure",
        size_band="51-200", employee_count=100,
    )
    company_id = await repos.companies.get_or_create(company, "other.example.com", "other co", origin="live_provider")
    prospect_id = await repos.prospects.create(
        run_id=run_id, company_id=company_id, dedupe_key="domain:other.example.com", duplicate_of=None,
        status="RUNNING",
    )
    return run_id, prospect_id


async def _restrict_address_on_a_different_prospect(session_factory, address: str, *, also_reobserve: bool = False) -> None:
    """Observes `address` as a successful email for a brand-new, unrelated
    prospect, then reports a legal/privacy restriction against it — the
    ONLY way the real V2-I-a flow ever populates `email_suppressions`."""
    repos = Repos.build(session_factory)
    run_id, prospect_id = await _new_synthetic_prospect(session_factory, repos)
    result = PersonEnrichmentResult(
        matched=True, provider_person_id="hunter-person-other",
        email=ProviderEmailObservation(address=address, provider_status="valid", observed_at=NOW),
        linkedin=None, origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest="rd-other",
        telemetry=_ok_telemetry(NOW),
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=prospect_id, provider="hunter", call_group_id="cg-1",
        telemetry=result.telemetry, result=result, email_status_map=HUNTER_EMAIL_STATUS_MAP,
        grounded_full_name="Someone Else", grounded_company_name="Other Co",
        grounded_company_domain="other.example.com",
    )
    restricted_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    await repos.contact_enrichment.record_legal_restriction(
        run_id=run_id, prospect_id=prospect_id, provider="hunter", call_group_id="cg-2",
        telemetry=_restriction_telemetry(restricted_at), provider_code="claimed_email",
    )
    if also_reobserve:
        # A LATER successful call on this SAME other prospect finds the
        # address again — must not clear the global suppression it just set.
        later = datetime(2026, 1, 3, tzinfo=timezone.utc)
        later_result = result.model_copy(
            update={"email": ProviderEmailObservation(address=address, provider_status="valid", observed_at=later)}
        )
        await repos.contact_enrichment.record_success(
            run_id=run_id, prospect_id=prospect_id, provider="hunter", call_group_id="cg-3",
            telemetry=_ok_telemetry(later), result=later_result, email_status_map=HUNTER_EMAIL_STATUS_MAP,
            grounded_full_name="Someone Else", grounded_company_name="Other Co",
            grounded_company_domain="other.example.com",
        )


async def test_same_address_suppressed_on_another_prospect_run_blocks_globally(client, session_factory) -> None:
    """A legal/privacy restriction observed against a completely different,
    unrelated (synthetic) prospect suppresses the SAME normalized email
    identity for the real canonical Demo's Northwind Labs — whose OWN
    `contact_channels` row was never itself locally suppressed."""
    _run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    email_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "email")
    assert email_channel["verification_state"] == "VERIFIED"
    assert email_channel["send_suppressed_at"] is None
    northwind_email = email_channel["identifier"]
    assert northwind_email

    await _restrict_address_on_a_different_prospect(session_factory, northwind_email)

    email_draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, email_draft["id"])
    assert proposal["policy_verdict"] == "BLOCKED", proposal
    assert "recipient_suppressed" in proposal["blocked_reasons"]


async def test_later_provider_success_on_the_other_prospect_cannot_clear_global_suppression(
    client, session_factory
) -> None:
    _run_id, prospects = await run_demo_play_to_completion(client)
    northwind = prospects["Northwind Labs"]
    aggregate = await get_aggregate(client, northwind["id"])
    email_channel = next(c for c in aggregate["contact_channels"] if c["channel"] == "email")
    northwind_email = email_channel["identifier"]
    assert northwind_email

    await _restrict_address_on_a_different_prospect(session_factory, northwind_email, also_reobserve=True)

    email_draft = find_draft(aggregate, channel="email")
    proposal = await propose(client, email_draft["id"])
    assert proposal["policy_verdict"] == "BLOCKED", proposal
    assert "recipient_suppressed" in proposal["blocked_reasons"]
