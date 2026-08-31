"""Full six-prospect demo run, headless (§25). This is also "eval-run zero":
the fixture pack has known-correct expected outcomes, so asserting the exact
distribution here is a regression test on the whole engine at once.
"""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode, ProspectStatus
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository

EXPECTED_DISTRIBUTION = {
    ProspectStatus.PASS.value: 2,
    ProspectStatus.NEEDS_REVIEW.value: 2,
    ProspectStatus.REJECTED.value: 1,
    ProspectStatus.DUPLICATE.value: 1,
    ProspectStatus.FAILED.value: 1,
}


async def test_full_demo_run_produces_expected_distribution(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="integration test play", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id,
        play_spec=pack.play_spec,
        providers=providers,
        repos=repos,
        max_concurrent_prospects=3,
        run_wall_clock_timeout_s=60,
    )

    assert len(summary.outcomes) == len(pack.companies)
    assert summary.counters == EXPECTED_DISTRIBUTION
    assert summary.status == "PARTIAL"  # a FAILED prospect makes the run PARTIAL, not silently COMPLETED

    tasks = await repos.tasks.for_run(run_id)
    retries = [t for t in tasks if t.status == "RETRY"]
    assert len(retries) >= 1, "expected at least one retry recorded in the trace"

    failed_research_attempts = [
        t for t in tasks if t.step_name == "research" and t.status == "FAILED"
    ]
    assert failed_research_attempts, "expected Quarry Systems' research step to exhaust its retries"

    # Every score is deterministic and reproducible for identical fixture input.
    northwind = next(o for o in summary.outcomes if o.company.slug == "northwind-labs")
    assert northwind.status == ProspectStatus.PASS
    assert northwind.score is not None and northwind.score.overall >= 60

    duplicate = next(o for o in summary.outcomes if o.company.slug == "northwind-labs-inc")
    assert duplicate.status == ProspectStatus.DUPLICATE
    assert duplicate.duplicate_of == northwind.prospect_id

    quarry = next(o for o in summary.outcomes if o.company.slug == "quarry-systems")
    assert quarry.status == ProspectStatus.FAILED

    events = await repos.events.after(run_id, 0)
    assert events, "expected run_events to have been emitted"
    assert events == sorted(events, key=lambda e: e.seq), "run_events must be strictly ordered by seq"
