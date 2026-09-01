"""H1 deviation closure #2 — the tri-state exclusion evaluation must be
recoverable from PERSISTED data alone, after a process restart, with zero
in-memory `ICPScore`/`ProspectContext` objects from the original execution
still alive.

Investigation finding (recorded here and in docs/PROGRESS.md): no new DB
column was needed. `domain/scoring.py::compute_score` already persists
enough information in `ICPScoreRow.disqualified` (bool) + `ICPScoreRow.
modifiers` (JSON) to reconstruct all three states unambiguously —
`domain/scoring.py::exclusion_status_from_persisted()`/
`exclusion_reason_from_persisted()` are the small, additive helper
functions that do this reconstruction from the two already-persisted
fields. This file proves that round-trip directly, including a literal
"dispose the engine, open a brand-new one against the same file" step for
the primary UNKNOWN scenario — not just a fresh repository call against a
connection pool that happens to still be warm.
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from groundwork.domain.scoring import exclusion_reason_from_persisted, exclusion_status_from_persisted
from groundwork.engine.runner import Repos, execute_run
from groundwork.evaluation.metrics import compute_run_evaluation
from groundwork.models.enums import ExclusionEvaluation, Mode, ProspectStatus
from groundwork.models.tables import Base
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository
from groundwork.repositories.prospect_data import ProspectDataRepository

_UNKNOWN_REASON = "Exclusion policy could not be evaluated because industry was not established from evidence."


def _enable_wal(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _pack_without_industry_profile() -> FixturePack:
    base = load_fixture_pack()
    company = base.company_by_slug("sable-compute").model_copy(update={"industry_profile_source_ref": None})
    return FixturePack(play_spec=base.play_spec.model_copy(update={"target_count": 1}), companies=[company])


async def test_unknown_exclusion_round_trips_after_engine_disposal_and_reconnect(tmp_path) -> None:
    """The full six-step scenario the task asks for: construct/persist a
    prospect whose grounded industry is unavailable; final status becomes
    NEEDS_REVIEW; dispose the engine (a real analogue of a process
    restart — the connection pool, not just a Python session, goes away);
    reload the prospect through a brand-new engine against the same file;
    exclusion state/reason is still available; evaluation can count it
    using persisted data alone.
    """
    db_path = tmp_path / "exclusion_reload.db"
    url = f"sqlite+aiosqlite:///{db_path}"

    # --- "process 1": execute the run and persist everything ---
    engine1 = create_async_engine(url)
    event.listen(engine1.sync_engine, "connect", _enable_wal)
    async with engine1.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf1 = async_sessionmaker(engine1, expire_on_commit=False)

    pack = _pack_without_industry_profile()
    providers = ProviderBundle(llm=DemoLLMProvider(pack, seed=1), search=DemoSearchProvider(pack, seed=1))
    repos1 = Repos.build(sf1)
    plays1 = PlayRepository(sf1)
    play_id = await plays1.create(
        name="exclusion reload test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos1.runs.create(play_id=play_id, mode="demo", seed=1)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos1,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=30,
    )
    prospect_id = summary.outcomes[0].prospect_id
    assert summary.outcomes[0].status == ProspectStatus.NEEDS_REVIEW  # step 2

    # step 3: dispose every execution object — engine, connection pool,
    # repos, providers, ctx. Nothing from "process 1" survives past this.
    await engine1.dispose()
    del repos1, plays1, providers, summary

    # step 4: reload through a brand-new engine/session against the same
    # on-disk file — this is the closest a single test process can get to
    # simulating an actual restart.
    engine2 = create_async_engine(url)
    event.listen(engine2.sync_engine, "connect", _enable_wal)
    sf2 = async_sessionmaker(engine2, expire_on_commit=False)
    prospect_data2 = ProspectDataRepository(sf2)

    score_row = await prospect_data2.get_score(prospect_id)
    assert score_row is not None

    # step 5: exclusion state/reason is still available, derived only from
    # the two plain fields a repository read actually returns.
    status = exclusion_status_from_persisted(disqualified=score_row.disqualified, modifiers=score_row.modifiers)
    assert status == ExclusionEvaluation.UNKNOWN
    assert exclusion_reason_from_persisted(score_row.modifiers) == _UNKNOWN_REASON

    # step 6: evaluation can count it using persisted data — a fresh Repos
    # bound to the same reconnected engine, computing entirely on read.
    repos2 = Repos.build(sf2)
    evaluation = await compute_run_evaluation(run_id, repos2)
    assert evaluation["search_quality"]["unevaluable_exclusion_count"] == 1

    await engine2.dispose()


async def test_excluded_cobalt_reloads_as_excluded_and_rejected(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="cobalt reload test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
    )
    cobalt = next(o for o in summary.outcomes if o.company.slug == "cobalt-retail-systems")
    assert cobalt.status == ProspectStatus.REJECTED

    # Reload through an independent repository instance — never touching
    # `cobalt.score` (the in-memory object) for the assertion below.
    fresh_prospect_data = ProspectDataRepository(session_factory)
    score_row = await fresh_prospect_data.get_score(cobalt.prospect_id)
    assert score_row is not None
    assert score_row.disqualified is True
    status = exclusion_status_from_persisted(disqualified=score_row.disqualified, modifiers=score_row.modifiers)
    assert status == ExclusionEvaluation.EXCLUDED
    assert exclusion_reason_from_persisted(score_row.modifiers) is None  # EXCLUDED carries no UNKNOWN reason


async def test_grounded_allowed_industry_reloads_as_not_excluded(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="northwind reload test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
    )
    northwind = next(o for o in summary.outcomes if o.company.slug == "northwind-labs")
    assert northwind.status == ProspectStatus.PASS

    fresh_prospect_data = ProspectDataRepository(session_factory)
    score_row = await fresh_prospect_data.get_score(northwind.prospect_id)
    assert score_row is not None
    assert score_row.disqualified is False
    status = exclusion_status_from_persisted(disqualified=score_row.disqualified, modifiers=score_row.modifiers)
    assert status == ExclusionEvaluation.NOT_EXCLUDED
    assert exclusion_reason_from_persisted(score_row.modifiers) is None

    # Still exactly seven review checks, persisted — this remains a
    # scoring/status concern, never an eighth guardrail.
    review_row = await fresh_prospect_data.get_review(northwind.prospect_id)
    assert review_row is not None
    assert len(review_row.checks) == 7


async def test_seven_review_checks_unaffected_for_unknown_exclusion_prospect(session_factory) -> None:
    pack = _pack_without_industry_profile()
    providers = ProviderBundle(llm=DemoLLMProvider(pack, seed=1), search=DemoSearchProvider(pack, seed=1))
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="unknown-exclusion seven-checks test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=30,
    )
    prospect_id = summary.outcomes[0].prospect_id
    assert summary.outcomes[0].status == ProspectStatus.NEEDS_REVIEW

    fresh_prospect_data = ProspectDataRepository(session_factory)
    review_row = await fresh_prospect_data.get_review(prospect_id)
    assert review_row is not None
    assert len(review_row.checks) == 7  # this is a scoring/status concern, not an eighth guardrail
