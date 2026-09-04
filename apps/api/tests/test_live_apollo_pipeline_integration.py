"""V2-D: a scripted Live Apollo run through the REAL engine machinery —
`contact -> contact_enrichment -> personalize` step order unchanged,
`ApolloEnrichmentProvider` writes `LIVE_PROVIDER` rows through the exact
same `engine/enrichment.py::call_enrichment` / `ContactEnrichmentRepository`
seam V2-C already built (never a second enrichment path), and last-known-good
is preserved on a failed re-attempt. No network calls anywhere in this file
— `ApolloEnrichmentProvider` is wired against a scripted `httpx.MockTransport`
via `tests/live_enrichment_helpers.py`, and the LLM/search halves stay
`DemoLLMProvider`/`DemoSearchProvider` (V2-D never touches those) so this
test isolates the one thing V2-D actually changed.
"""

from __future__ import annotations

from groundwork.engine.budget import PipelineBudget
from groundwork.engine.enrichment_budget import EnrichmentCallBudget
from groundwork.engine.pipeline import build_prospect_pipeline
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import EnrichmentOrigin
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.repositories.plays import PlayRepository
from tests.live_enrichment_helpers import make_enrichment_provider, match_response


async def _run_with_apollo(session_factory, steps, *, budget: EnrichmentCallBudget | None = None):
    pack = load_fixture_pack()
    apollo_provider, transport = make_enrichment_provider(steps, budget=budget)
    providers = ProviderBundle(
        llm=DemoLLMProvider(pack, seed=42), search=DemoSearchProvider(pack, seed=42), enrichment=apollo_provider,
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
    return summary, repos, transport


async def _find_enriched_outcome(repos, outcomes):
    """The scripted Apollo response is not company-keyed (unlike the Demo
    fixture pack) — whichever prospect has a named contact and isn't
    disqualified reaches `contact_enrichment` first and consumes the single
    scripted transport step. Find it generically rather than assuming a
    specific canonical company, since that assignment is an implementation
    detail of `asyncio.gather`'s scheduling order, not a contract."""
    for outcome in outcomes:
        rows = await repos.contact_enrichment.get_contact_enrichments(outcome.prospect_id)
        if rows:
            return outcome, rows
    raise AssertionError("no prospect in this run produced a contact_enrichments row")


def test_pipeline_ordering_unchanged() -> None:
    pipeline = build_prospect_pipeline()
    names = [s.name for s in pipeline.steps]
    assert names.index("contact") < names.index("contact_enrichment") < names.index("personalize")


async def test_scripted_live_apollo_run_writes_live_provider_rows(session_factory) -> None:
    summary, repos, transport = await _run_with_apollo(session_factory, [(200, match_response())])

    outcome, enrichments = await _find_enriched_outcome(repos, summary.outcomes)
    assert all(e.origin == EnrichmentOrigin.LIVE_PROVIDER.value for e in enrichments)
    assert all(e.provider == "apollo" for e in enrichments)

    channels = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}
    assert channels["email"].discovery_state == "FOUND"
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["linkedin"].discovery_state == "RESOLVED"

    calls = await repos.contact_enrichment.get_enrichment_calls(outcome.prospect_id)
    assert calls and all(c.provider == "apollo" for c in calls)
    assert transport.calls == 1


async def test_repository_remains_the_only_persistence_owner(session_factory) -> None:
    """`ApolloEnrichmentProvider`/`ApolloRuntime` never import a repository
    or SQLAlchemy — asserted directly in `test_apollo_adapter.py`'s provider-
    purity test. Here: after a scripted run, every persisted enrichment row
    ties back to `ContactEnrichmentRepository`'s own writes, never a second
    ad-hoc write path (there is exactly one `contact_enrichments` row per
    successful call, matching `call_group_id`s 1:1 with `enrichment_calls`
    attempts)."""
    summary, repos, _ = await _run_with_apollo(session_factory, [(200, match_response())])
    outcome, enrichments = await _find_enriched_outcome(repos, summary.outcomes)
    calls = await repos.contact_enrichment.get_enrichment_calls(outcome.prospect_id)
    assert len(enrichments) == 1
    call_groups = {c.call_group_id for c in calls}
    assert enrichments[0].call_group_id in call_groups


async def test_last_known_good_preserved_after_a_later_apollo_failure(session_factory) -> None:
    """§3.6: a successful Apollo observation, once derived, must survive a
    LATER failed call — only `last_attempt_*` moves."""
    pack = load_fixture_pack()
    apollo_provider, transport = make_enrichment_provider([(200, match_response())])
    providers = ProviderBundle(
        llm=DemoLLMProvider(pack, seed=42), search=DemoSearchProvider(pack, seed=42), enrichment=apollo_provider,
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
    outcome, _ = await _find_enriched_outcome(repos, summary.outcomes)

    # A second, independent call against the SAME prospect fails outright —
    # exercised directly against the repository (mirrors how
    # `record_failure` is exercised elsewhere), since re-running the whole
    # pipeline a second time would create a brand-new prospect row.
    from groundwork.providers.contact_base import EnrichmentAttemptKind, EnrichmentAttemptStatus, EnrichmentAttemptTelemetry
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    await repos.contact_enrichment.record_failure(
        run_id=run_id, prospect_id=outcome.prospect_id, provider="apollo", call_group_id="cg-2",
        telemetry=[
            EnrichmentAttemptTelemetry(
                provider="apollo", operation="person_enrichment", call_group_id="cg-2", attempt=1,
                attempt_kind=EnrichmentAttemptKind.INITIAL, status=EnrichmentAttemptStatus.PROVIDER_ERROR,
                started_at=now, finished_at=now, latency_ms=1.0, error_type="EnrichmentProviderUnavailable",
            )
        ],
    )

    channels = {r.channel: r for r in await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)}
    # State from the successful call is untouched...
    assert channels["email"].verification_state == "VERIFIED"
    assert channels["email"].discovery_state == "FOUND"
    # ...only the attempt-telemetry columns moved.
    assert channels["email"].last_attempt_status == "PROVIDER_ERROR"
