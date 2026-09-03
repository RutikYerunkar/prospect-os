"""§N.6 — pipeline orchestration: `contact -> contact_enrichment -> personalize`
step order, and `contact_enrichment` stays optional (a provider failure
degrades the one prospect rather than crashing the run).
"""

from __future__ import annotations

from groundwork.engine.budget import PipelineBudget
from groundwork.engine.pipeline import build_prospect_pipeline
from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode, ProspectStatus
from groundwork.providers.demo.fixtures import FixtureFailureSpec, FixturePack, load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository


def test_step_order_is_contact_then_contact_enrichment_then_personalize() -> None:
    pipeline = build_prospect_pipeline()
    names = [s.name for s in pipeline.steps]
    assert names.index("contact") < names.index("contact_enrichment") < names.index("personalize")


def test_contact_enrichment_step_is_optional() -> None:
    pipeline = build_prospect_pipeline()
    step = next(s for s in pipeline.steps if s.name == "contact_enrichment")
    assert step.optional is True
    assert step.depends_on == ("contact",)


async def test_provider_failure_degrades_the_prospect_without_crashing_the_run(session_factory) -> None:
    # `load_fixture_pack()` is `lru_cache`d — a shared singleton every other
    # test in the suite also reads. Build a fresh `FixturePack` with the
    # scripted-failure company substituted in, rather than mutating the
    # cached pack's `.companies` list in place (which would permanently
    # corrupt Northwind's fixture for every later test in the same session).
    base_pack = load_fixture_pack()
    fixture = base_pack.company_by_slug("northwind-labs")
    # Exhaust the step's own bounded retries so contact_enrichment fails
    # permanently for Northwind while every other prospect is untouched.
    scripted = fixture.model_copy(
        update={
            "enrichment_failure_script": {
                "person_enrichment": FixtureFailureSpec(fail_attempts=99, error="EnrichmentProviderUnavailable")
            }
        }
    )
    pack = FixturePack(
        play_spec=base_pack.play_spec,
        companies=[scripted if c.slug == "northwind-labs" else c for c in base_pack.companies],
    )

    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="t", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
        budget=PipelineBudget(contact_enrichment_max_retries=0),
    )

    northwind = next(o for o in summary.outcomes if o.company.slug == "northwind-labs")
    # Degraded, not crashed: the prospect still reaches a real terminal
    # status (never FAILED/TIMED_OUT purely because enrichment failed) and
    # still produced a draft.
    assert northwind.status in (ProspectStatus.PASS, ProspectStatus.NEEDS_REVIEW)
    assert northwind.drafts, "personalize must still have run after contact_enrichment degraded"

    rows = await repos.contact_enrichment.get_contact_channels(northwind.prospect_id)
    channels = {r.channel: r for r in rows}
    assert channels["email"].discovery_state == "PROVIDER_ERROR"
    assert channels["linkedin"].discovery_state == "PROVIDER_ERROR"

    tasks = await repos.tasks.for_run(run_id)
    enrichment_tasks = [t for t in tasks if t.step_name == "contact_enrichment" and t.prospect_id == northwind.prospect_id]
    assert enrichment_tasks and all(t.status == "FAILED" for t in enrichment_tasks)
