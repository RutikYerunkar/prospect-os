"""§N.8/§M — the Demo reset flow (`make demo-reset` / `scripts/reset.py`,
a wipe-and-recreate of the whole SQLite file via `Base.metadata.drop_all` +
`create_all`) must clear every v2 enrichment row cleanly, and the canonical
Demo run must stay deterministic across repeated resets.
"""

from __future__ import annotations

from sqlalchemy import func, select

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.models.tables import Base, ContactChannelRow, ContactEnrichmentRow, EnrichmentCallRow
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository


async def _run_canonical_demo(session_factory) -> str:
    pack = load_fixture_pack()
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
    )
    northwind = next(o for o in summary.outcomes if o.company.slug == "northwind-labs")
    channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(northwind.prospect_id)}
    return channels["email"].identifier, channels["linkedin"].identifier


async def _row_count(session_factory, model) -> int:
    async with session_factory() as session:
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def test_demo_reset_clears_all_v2_enrichment_rows(session_factory) -> None:
    await _run_canonical_demo(session_factory)

    assert await _row_count(session_factory, ContactChannelRow) > 0
    assert await _row_count(session_factory, ContactEnrichmentRow) > 0
    assert await _row_count(session_factory, EnrichmentCallRow) > 0

    # Mirrors `scripts/reset.py`'s wipe-and-recreate — the same operation
    # `make demo-reset` performs on the real local SQLite file — run here
    # against this test's own isolated engine.
    async with session_factory() as session:
        engine = session.bind
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    assert await _row_count(session_factory, ContactChannelRow) == 0
    assert await _row_count(session_factory, ContactEnrichmentRow) == 0
    assert await _row_count(session_factory, EnrichmentCallRow) == 0


async def test_canonical_demo_is_deterministic_across_a_reset(session_factory) -> None:
    email_before, linkedin_before = await _run_canonical_demo(session_factory)

    async with session_factory() as session:
        engine = session.bind
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    email_after, linkedin_after = await _run_canonical_demo(session_factory)

    assert email_before == email_after == "priya.natarajan@northwindlabs.com"
    assert linkedin_before == linkedin_after == "demo://linkedin/priya-natarajan"
