"""The most valuable test in the project (docs/ARCHITECTURE.md).

Two deliberately confusable prospects (same industry, same size, same
persona title), each carrying a unique canary token in its own funding
claim, are run through the *real* engine concurrently. If `ProspectContext`
isolation ever regressed — a shared dict, a provider call built from the
wrong context, a leaked prompt — one prospect's canary would show up in the
other's evidence, signals, score explanation, or outreach. It must not.
"""

from __future__ import annotations

from sqlalchemy import select

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode
from groundwork.models.tables import EvidenceRow, SignalRow
from groundwork.providers.demo.fixtures import (
    FixtureCompany,
    FixtureFundingEvent,
    FixtureLeadership,
    FixturePack,
    FixtureSource,
)
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository

CANARY_ALPHA = "CANARY-ALPHA-7Q2X9"
CANARY_BETA = "CANARY-BETA-4M8K1"


def _isolation_fixture_pack() -> FixturePack:
    play_spec_kwargs = dict(
        objective_text="isolation test play",
        target_industries=["ai_infrastructure"],
        size_band_min=1,
        size_band_max=10000,
        target_funding_stages=["series_a", "series_b"],
        target_technologies=["kubernetes"],
        persona_titles=["VP of Sales"],
        min_score=0,
        min_confidence=0.0,
        target_count=2,
    )

    def company(slug: str, name: str, domain: str, canary: str, leader: str) -> FixtureCompany:
        return FixtureCompany(
            slug=slug,
            name=name,
            domain=domain,
            industry="ai_infrastructure",
            size_band="51-200",
            employee_count=100,
            sources=[
                FixtureSource(
                    ref="funding-note",
                    title=f"{name} funding note",
                    claim=f"{name} raised a Series A round, reference {canary}",
                    snippet=f"{name} raised a Series A round, reference {canary}, per the filing.",
                    signal_type="FUNDING",
                    confidence=0.9,
                )
            ],
            funding_events=[FixtureFundingEvent(stage="series_a", announced_days_ago=10, source_ref="funding-note")],
            leadership=[
                FixtureLeadership(full_name=leader, title="VP of Sales", is_persona_match=True, source_ref="funding-note")
            ],
        )

    return FixturePack(
        play_spec=play_spec_kwargs,  # type: ignore[arg-type]
        companies=[
            company("alpha-canary", "Alpha Canary Systems", "alphacanary.example", CANARY_ALPHA, "Alice Alpha"),
            company("beta-canary", "Beta Canary Systems", "betacanary.example", CANARY_BETA, "Bob Beta"),
        ],
    )


async def _prospect_text_blob(session_factory, prospect_id: str, outcome) -> str:
    parts: list[str] = []
    async with session_factory() as session:
        ev_rows = (await session.execute(select(EvidenceRow).where(EvidenceRow.prospect_id == prospect_id))).scalars()
        for row in ev_rows:
            parts += [row.title, row.claim, row.snippet]
        sig_rows = (await session.execute(select(SignalRow).where(SignalRow.prospect_id == prospect_id))).scalars()
        for row in sig_rows:
            parts.append(row.summary)
    if outcome.score is not None:
        parts.append(outcome.score.explanation)
    for draft in outcome.drafts:
        parts += [draft.subject, draft.body]
    return " ".join(parts)


async def test_cross_prospect_canary_isolation(session_factory) -> None:
    pack = _isolation_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=7, fixture_pack=pack)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(name="isolation test", objective_text="t", icp_spec={}, mode="demo")
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=7)

    summary = await execute_run(
        run_id=run_id,
        play_spec=pack.play_spec,
        providers=providers,
        repos=repos,
        max_concurrent_prospects=2,
        run_wall_clock_timeout_s=30,
    )

    by_name = {o.company.name: o for o in summary.outcomes}
    alpha = by_name["Alpha Canary Systems"]
    beta = by_name["Beta Canary Systems"]

    # Sanity: both prospects actually ran and produced content worth checking.
    assert alpha.score is not None and beta.score is not None
    assert alpha.drafts and beta.drafts

    alpha_blob = await _prospect_text_blob(session_factory, alpha.prospect_id, alpha)
    beta_blob = await _prospect_text_blob(session_factory, beta.prospect_id, beta)

    # Each prospect's own canary should appear in its own record...
    assert CANARY_ALPHA in alpha_blob
    assert CANARY_BETA in beta_blob

    # ...and never cross over into the other's evidence, signals, score
    # explanation, or outreach.
    assert CANARY_BETA not in alpha_blob
    assert CANARY_ALPHA not in beta_blob
    assert "Beta Canary Systems" not in alpha_blob
    assert "Alpha Canary Systems" not in beta_blob
