"""H1 Phase 7 — a company whose industry was never independently grounded
must never silently PASS. `_derive_final_status` downgrades it to
NEEDS_REVIEW, and `ICPScore.modifiers` carries the `exclusion_not_evaluable`
reason — proven end-to-end (not just at the `domain/scoring.py` unit
level), and confirmed the seven review checks are unaffected (still exactly
seven, all independently passing).
"""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import ExclusionEvaluation, ProspectStatus
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.repositories.plays import PlayRepository


def _pack_without_industry_profile() -> FixturePack:
    base = load_fixture_pack()
    company = base.company_by_slug("sable-compute").model_copy(
        update={"industry_profile_source_ref": None}
    )
    return FixturePack(play_spec=base.play_spec.model_copy(update={"target_count": 1}), companies=[company])


async def test_ungrounded_industry_forces_needs_review_not_silent_pass(session_factory) -> None:
    pack = _pack_without_industry_profile()
    providers = ProviderBundle(llm=DemoLLMProvider(pack, seed=1), search=DemoSearchProvider(pack, seed=1))
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="unknown-exclusion test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=1)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=30,
    )

    outcome = summary.outcomes[0]
    assert outcome.score is not None
    assert outcome.score.exclusion_status == ExclusionEvaluation.UNKNOWN
    assert outcome.score.disqualified is False  # UNKNOWN is not EXCLUDED
    assert any(m.name == "exclusion_not_evaluable" for m in outcome.score.modifiers)

    # The review verdict itself is unaffected (still exactly seven checks,
    # all independently correct) — it's the FINAL STATUS derivation that
    # downgrades PASS -> NEEDS_REVIEW, not an eighth guardrail.
    assert outcome.review is not None
    assert len(outcome.review.checks) == 7

    assert outcome.status == ProspectStatus.NEEDS_REVIEW
