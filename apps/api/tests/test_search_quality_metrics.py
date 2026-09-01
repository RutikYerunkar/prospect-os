"""H1 Phase 16 — unambiguous source/quality metric definitions, computed on
read and exposed via `compute_run_evaluation`."""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.evaluation.metrics import compute_run_evaluation
from groundwork.models.enums import Mode
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository


async def test_search_quality_metrics_computed_from_real_run(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="search quality test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)
    await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
    )

    evaluation = await compute_run_evaluation(run_id, repos)
    sq = evaluation["search_quality"]

    assert sq["result_occurrences"] > 0
    assert sq["sources_retrieved_unique"] > 0
    # Demo Mode never produces true duplicate retrievals — every occurrence
    # is its own winner, so utilization/duplicate rates are well-defined
    # extremes.
    assert sq["result_occurrences"] == sq["sources_retrieved_unique"]
    assert sq["duplicate_retrieval_rate"] == 0.0
    assert sq["source_utilization_rate"] is not None and 0.0 <= sq["source_utilization_rate"] <= 1.0
    assert sq["industry_grounded_coverage"] is not None
    assert sq["employee_count_grounded_coverage"] is not None
    assert sq["unevaluable_exclusion_count"] == 0  # every canonical fixture company grounds industry
    assert sq["search_cost_usd"] is None  # never guessed


async def test_search_quality_metrics_null_for_run_with_no_prospects(session_factory) -> None:
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="empty", objective_text="t", icp_spec={}, mode="demo")
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)

    evaluation = await compute_run_evaluation(run_id, repos)
    sq = evaluation["search_quality"]
    assert sq["result_occurrences"] == 0
    assert sq["sources_retrieved_unique"] == 0
    assert sq["source_utilization_rate"] is None
    assert sq["duplicate_retrieval_rate"] is None
