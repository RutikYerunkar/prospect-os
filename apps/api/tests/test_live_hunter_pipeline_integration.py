"""V2-DH: a scripted Live Hunter run through the REAL engine machinery —
`contact -> contact_enrichment -> personalize` step order unchanged,
`HunterEnrichmentProvider` writes `LIVE_PROVIDER` rows through the exact same
`engine/enrichment.py::call_enrichment` / `ContactEnrichmentRepository` seam
Apollo already exercises (never a second enrichment path), and last-known-good
is preserved on a failed re-attempt. No network calls anywhere in this file
— `HunterEnrichmentProvider` is wired against a scripted `httpx.MockTransport`
via `tests/live_hunter_helpers.py`, and the LLM/search halves stay
`DemoLLMProvider`/`DemoSearchProvider` (V2-DH never touches those) so this
test isolates the one thing V2-DH actually adds.
"""

from __future__ import annotations

from datetime import datetime, timezone

from groundwork.engine.budget import PipelineBudget
from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.engine.pipeline import build_prospect_pipeline
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import EnrichmentOrigin
from groundwork.providers.base import ProviderBundle
from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptStatus, EnrichmentAttemptTelemetry
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.repositories.plays import PlayRepository
from tests.live_hunter_helpers import email_finder_response, make_hunter_provider


async def _run_with_hunter(session_factory, steps, *, budget: EnrichmentCallBudget | None = None):
    pack = load_fixture_pack()
    hunter_provider, transport = make_hunter_provider(steps, budget=budget)
    providers = ProviderBundle(
        llm=DemoLLMProvider(pack, seed=42), search=DemoSearchProvider(pack, seed=42), enrichment=hunter_provider,
    )
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="t", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="live",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
        budget=PipelineBudget(contact_enrichment_max_retries=0),
    )
    return summary, repos, transport, run_id


async def _find_enriched_outcome(repos, outcomes):
    for outcome in outcomes:
        rows = await repos.contact_enrichment.get_contact_enrichments(outcome.prospect_id)
        if rows:
            return outcome, rows
    raise AssertionError("no prospect in this run produced a contact_enrichments row")


def test_pipeline_ordering_unchanged() -> None:
    pipeline = build_prospect_pipeline()
    names = [s.name for s in pipeline.steps]
    assert names.index("contact") < names.index("contact_enrichment") < names.index("personalize")


async def test_scripted_live_hunter_run_writes_live_provider_rows(session_factory) -> None:
    summary, repos, transport, _run_id = await _run_with_hunter(session_factory, [(200, email_finder_response())])

    outcome, enrichments = await _find_enriched_outcome(repos, summary.outcomes)
    assert all(e.origin == EnrichmentOrigin.LIVE_PROVIDER.value for e in enrichments)
    assert all(e.provider == "hunter" for e in enrichments)

    channels = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["linkedin"].discovery_state == "RESOLVED"

    calls = await repos.contact_enrichment.get_enrichment_calls(outcome.prospect_id)
    assert calls and all(c.provider == "hunter" for c in calls)
    assert transport.calls == 1


async def test_repository_remains_the_only_persistence_owner(session_factory) -> None:
    summary, repos, _, _run_id = await _run_with_hunter(session_factory, [(200, email_finder_response())])
    outcome, enrichments = await _find_enriched_outcome(repos, summary.outcomes)
    calls = await repos.contact_enrichment.get_enrichment_calls(outcome.prospect_id)
    assert len(enrichments) == 1
    call_groups = {c.call_group_id for c in calls}
    assert enrichments[0].call_group_id in call_groups


async def test_last_known_good_preserved_after_a_later_hunter_failure(session_factory) -> None:
    """§3.6: a successful Hunter observation, once derived, must survive a
    LATER failed call — only `last_attempt_*` moves."""
    summary, repos, _, run_id = await _run_with_hunter(session_factory, [(200, email_finder_response())])
    outcome, _ = await _find_enriched_outcome(repos, summary.outcomes)

    now = datetime.now(timezone.utc)
    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=outcome.prospect_id, provider="hunter", call_group_id="cg-2",
        telemetry=[
            EnrichmentAttemptTelemetry(
                provider="hunter", operation="person_enrichment", call_group_id="cg-2", attempt=1,
                attempt_kind=EnrichmentAttemptKind.INITIAL, status=EnrichmentAttemptStatus.PROVIDER_ERROR,
                started_at=now, finished_at=now, latency_ms=1.0, error_type="EnrichmentProviderUnavailable",
            )
        ],
    )

    channels = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].last_attempt_status == "PROVIDER_ERROR"


async def test_last_known_good_preserved_after_a_later_successful_but_empty_hunter_call(session_factory) -> None:
    """V2-DH's repository fix (§Part 7): a later SUCCESSFUL Hunter call that
    legitimately finds nothing must never erase a previously observed real
    email/LinkedIn identifier."""
    summary, repos, _, run_id = await _run_with_hunter(session_factory, [(200, email_finder_response())])
    outcome, _ = await _find_enriched_outcome(repos, summary.outcomes)
    before = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}

    now = datetime.now(timezone.utc)
    empty_telemetry = [
        EnrichmentAttemptTelemetry(
            provider="hunter", operation="person_enrichment", call_group_id="cg-empty", attempt=1,
            attempt_kind=EnrichmentAttemptKind.INITIAL, status=EnrichmentAttemptStatus.OK,
            started_at=now, finished_at=now, latency_ms=1.0,
        )
    ]
    from groundwork.providers.contact_base import PersonEnrichmentResult
    from groundwork.providers.demo.contact_enrichment import DEMO_EMAIL_STATUS_MAP

    empty_result = PersonEnrichmentResult(
        matched=False, provider_person_id=None, email=None, linkedin=None,
        origin=EnrichmentOrigin.LIVE_PROVIDER, raw_digest="rd-empty", telemetry=empty_telemetry,
    )
    await repos.contact_enrichment.record_success(
        run_id=run_id, prospect_id=outcome.prospect_id, provider="hunter", call_group_id="cg-empty",
        telemetry=empty_telemetry, result=empty_result, email_status_map=DEMO_EMAIL_STATUS_MAP,
        grounded_full_name=None, grounded_company_name=None, grounded_company_domain=None,
    )

    after = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}
    assert after["email"].identifier == before["email"].identifier
    assert after["email"].verification_state == before["email"].verification_state == "VERIFIED"
    assert after["email"].observed_at == before["email"].observed_at
    assert after["email"].last_attempt_status == "OK"
