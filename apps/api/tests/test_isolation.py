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
    FixtureEnrichment,
    FixtureEnrichmentEmail,
    FixtureEnrichmentLinkedIn,
    FixtureFundingEvent,
    FixtureLeadership,
    FixturePack,
    FixtureSource,
)
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository

CANARY_ALPHA = "CANARY-ALPHA-7Q2X9"
CANARY_BETA = "CANARY-BETA-4M8K1"

ALPHA_EMAIL = "alice.alpha@alphacanary.example"
BETA_EMAIL = "bob.beta@betacanary.example"
ALPHA_LINKEDIN = "demo://linkedin/alice-alpha-canary"
BETA_LINKEDIN = "demo://linkedin/bob-beta-canary"


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

    def company(
        slug: str, name: str, domain: str, canary: str, leader: str, email: str, linkedin: str
    ) -> FixtureCompany:
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
            # Distinct, unique-per-company enrichment observations — the
            # canary values a cross-prospect-isolation regression would leak.
            enrichment=FixtureEnrichment(
                matched=True,
                email=FixtureEnrichmentEmail(address=email, provider_status="verified", provider_confidence=0.9),
                linkedin=FixtureEnrichmentLinkedIn(
                    profile_url=linkedin, asserted_full_name=leader, asserted_company_name=name,
                    asserted_company_domain=domain, asserted_title="VP of Sales",
                ),
            ),
        )

    return FixturePack(
        play_spec=play_spec_kwargs,  # type: ignore[arg-type]
        companies=[
            company(
                "alpha-canary", "Alpha Canary Systems", "alphacanary.example", CANARY_ALPHA, "Alice Alpha",
                ALPHA_EMAIL, ALPHA_LINKEDIN,
            ),
            company(
                "beta-canary", "Beta Canary Systems", "betacanary.example", CANARY_BETA, "Bob Beta",
                BETA_EMAIL, BETA_LINKEDIN,
            ),
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
        # v2 §V2-F: a LinkedIn draft's `subject` is `None` by design.
        if draft.subject is not None:
            parts.append(draft.subject)
        parts.append(draft.body)
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

    # v2 §Part 4/§N.4 — contact-enrichment isolation: each prospect's own
    # email/LinkedIn identifier appears on its own contact_channels rows,
    # and NEVER on the other prospect's, under real concurrent fan-out
    # (`asyncio.gather`, bounded by the run's semaphore — same mechanism the
    # rest of this test already exercises).
    alpha_channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(alpha.prospect_id)}
    beta_channels = {c.channel: c for c in await repos.contact_enrichment.get_contact_channels(beta.prospect_id)}

    assert alpha_channels["email"].identifier == ALPHA_EMAIL
    assert alpha_channels["linkedin"].identifier == ALPHA_LINKEDIN
    assert beta_channels["email"].identifier == BETA_EMAIL
    assert beta_channels["linkedin"].identifier == BETA_LINKEDIN

    assert alpha_channels["email"].identifier != beta_channels["email"].identifier
    assert alpha_channels["linkedin"].identifier != beta_channels["linkedin"].identifier
    assert BETA_EMAIL not in (alpha_channels["email"].identifier, alpha_channels["linkedin"].identifier)
    assert ALPHA_EMAIL not in (beta_channels["email"].identifier, beta_channels["linkedin"].identifier)
    assert BETA_LINKEDIN not in (alpha_channels["email"].identifier, alpha_channels["linkedin"].identifier)
    assert ALPHA_LINKEDIN not in (beta_channels["email"].identifier, beta_channels["linkedin"].identifier)

    # Derived states cannot cross prospects either — both resolve
    # independently to the same STRONG_MATCH shape (identical inputs on
    # each side), never influenced by the other prospect's data.
    assert alpha_channels["linkedin"].identity_match_state == "STRONG_MATCH"
    assert beta_channels["linkedin"].identity_match_state == "STRONG_MATCH"
