"""H1 Phase 9/17 — `search_calls`/`source_documents` persistence, the
Evidence FK linkage between them, and `signals.grounded`/`occurred_at`
persistence, all proven against a real (Demo Mode) end-to-end run rather
than mocked repository calls.
"""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository


async def _run_full_demo(session_factory):
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="provenance test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
    )
    return summary, repos, run_id, pack


async def test_search_calls_and_source_documents_persist(session_factory) -> None:
    summary, repos, run_id, pack = await _run_full_demo(session_factory)

    docs = await repos.search.source_documents_for_run(run_id)
    calls = await repos.search.search_calls_for_run(run_id)

    assert docs, "expected source_documents rows from the real demo run"
    assert calls, "expected search_calls telemetry rows from the real demo run"

    # Every non-duplicate, non-FAILED-before-search prospect that actually
    # fetched sources should have at least one occurrence row.
    researched_prospects = {
        o.prospect_id for o in summary.outcomes if o.evidence_count > 0
    }
    doc_prospects = {d.prospect_id for d in docs}
    assert researched_prospects <= doc_prospects


async def test_evidence_fk_linkage_winner_rows_point_at_real_evidence(session_factory) -> None:
    summary, repos, run_id, pack = await _run_full_demo(session_factory)

    docs = await repos.search.source_documents_for_run(run_id)
    evidence_rows = await repos.prospect_data.evidence_for_run(run_id)
    evidence_ids = {e.id for e in evidence_rows}

    winners_with_evidence = [d for d in docs if d.is_winner and d.evidence_id is not None]
    assert winners_with_evidence, "expected at least one winner row to carry an evidence_id"
    for winner in winners_with_evidence:
        assert winner.evidence_id in evidence_ids, "winner.evidence_id must reference a real, persisted Evidence row"


async def test_no_duplicate_retrieval_in_demo_mode_every_occurrence_is_its_own_winner(session_factory) -> None:
    """Demo Mode's fixture sources never collide (distinct refs, no URLs) —
    every occurrence should be its own winner, proving the dedupe path is a
    true no-op here rather than accidentally collapsing distinct sources."""
    summary, repos, run_id, pack = await _run_full_demo(session_factory)
    docs = await repos.search.source_documents_for_run(run_id)
    assert docs
    assert all(d.is_winner for d in docs)
    assert all(d.canonical_source_id is None for d in docs)


async def test_signals_persist_grounded_and_occurred_at(session_factory) -> None:
    summary, repos, run_id, pack = await _run_full_demo(session_factory)

    northwind = next(o for o in summary.outcomes if o.company.slug == "northwind-labs")
    signal_rows = await repos.prospect_data.get_signals(northwind.prospect_id)
    assert signal_rows, "expected signals for Northwind Labs"

    grounded_values = {row.grounded for row in signal_rows}
    assert grounded_values == {True} or grounded_values == {True, False}
    # At least the funding signal (which cites a well-grounded fixture
    # claim) must have a real occurred_at date persisted, not left null.
    dated = [row for row in signal_rows if row.occurred_at is not None]
    assert dated, "expected at least one signal to persist a real occurred_at"


async def test_riverbend_demoted_signal_persists_grounded_false(session_factory) -> None:
    summary, repos, run_id, pack = await _run_full_demo(session_factory)
    riverbend = next(o for o in summary.outcomes if o.company.slug == "riverbend-analytics")
    signal_rows = await repos.prospect_data.get_signals(riverbend.prospect_id)
    funding_signals = [row for row in signal_rows if row.type == "FUNDING"]
    assert funding_signals
    assert any(row.grounded is False for row in funding_signals), (
        "Riverbend's ungrounded funding claim must persist grounded=False"
    )
