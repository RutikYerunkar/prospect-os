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

    # H1 canonical invariants — Cobalt's hard disqualifier still fires via
    # the independently grounded industry profile fact (Phase 7 deleted the
    # old CompanySeed-based exemption; this proves the replacement still
    # produces the same real-world outcome).
    cobalt = next(o for o in summary.outcomes if o.company.slug == "cobalt-retail-systems")
    assert cobalt.status == ProspectStatus.REJECTED
    assert cobalt.score is not None and cobalt.score.disqualified is True
    assert cobalt.score.overall == 25

    # Canonical H1 scores, byte-identical to the pre-H1 baseline.
    riverbend = next(o for o in summary.outcomes if o.company.slug == "riverbend-analytics")
    ferrous = next(o for o in summary.outcomes if o.company.slug == "ferrous-grid")
    sable = next(o for o in summary.outcomes if o.company.slug == "sable-compute")
    assert northwind.score.overall == 92
    assert riverbend.score is not None and riverbend.score.overall == 35
    assert ferrous.score is not None and ferrous.score.overall == 58
    assert sable.score is not None and sable.score.overall == 79

    events = await repos.events.after(run_id, 0)
    assert events, "expected run_events to have been emitted"
    assert events == sorted(events, key=lambda e: e.seq), "run_events must be strictly ordered by seq"

    # v2 §Part 7 — contact_channels matches the frozen Demo matrix exactly,
    # and every V1 outcome/score above stayed byte-identical alongside it.
    async def _channels_by_name(slug: str) -> dict[str, object]:
        outcome = next(o for o in summary.outcomes if o.company.slug == slug)
        rows = await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)
        return {row.channel: row for row in rows}

    northwind_channels = await _channels_by_name("northwind-labs")
    assert northwind_channels["email"].identifier == "priya.natarajan@northwindlabs.com"
    assert northwind_channels["email"].discovery_state == "FOUND"
    assert northwind_channels["email"].verification_state == "VERIFIED"
    assert northwind_channels["linkedin"].identifier == "demo://linkedin/priya-natarajan"
    assert northwind_channels["linkedin"].discovery_state == "RESOLVED"
    assert northwind_channels["linkedin"].identity_match_state == "STRONG_MATCH"

    sable_channels = await _channels_by_name("sable-compute")
    assert sable_channels["email"].discovery_state == "FOUND"
    assert sable_channels["email"].verification_state == "RISKY"
    assert sable_channels["linkedin"].discovery_state == "RESOLVED"
    assert sable_channels["linkedin"].identity_match_state == "STRONG_MATCH"

    # Never attempted: Riverbend (PERSONA_ONLY, no named person), Ferrous
    # (UNAVAILABLE, nothing to enrich), Cobalt (hard-disqualified — excluded
    # industry, never actionable). No row at all — NOT_ATTEMPTED by omission.
    for slug in ("riverbend-analytics", "ferrous-grid", "cobalt-retail-systems"):
        outcome = next(o for o in summary.outcomes if o.company.slug == slug)
        rows = await repos.contact_enrichment.get_contact_channels(outcome.prospect_id)
        assert rows == [], f"{slug} must never reach contact_enrichment"

    # Quarry never reaches contact_enrichment at all — research exhausts its
    # retries first.
    rows = await repos.contact_enrichment.get_contact_channels(quarry.prospect_id)
    assert rows == []
