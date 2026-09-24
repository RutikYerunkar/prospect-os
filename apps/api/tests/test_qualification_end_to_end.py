"""v2.0.3 — a real below-minimum prospect reaches NOT_QUALIFIED through the
full engine (real deterministic scoring, real review checks), not just the
`_derive_final_status` unit-level proof in `test_qualification_status.py`.

Northwind Labs is the canonical Demo pack's highest-scoring company (overall
92, review verdict PASS at the pack's own `min_score: 60`) — raising
`min_score` past its score here proves a clean, review-PASS prospect becomes
NOT_QUALIFIED end-to-end, with the review verdict itself untouched (still
PASS, still exactly seven checks).
"""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import ProspectStatus, ReviewVerdict
from groundwork.providers.base import ProviderBundle
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.repositories.plays import PlayRepository


def _northwind_only_pack(min_score: int) -> FixturePack:
    base = load_fixture_pack()
    company = base.company_by_slug("northwind-labs")
    play_spec = base.play_spec.model_copy(update={"target_count": 1, "min_score": min_score})
    return FixturePack(play_spec=play_spec, companies=[company])


async def _run_northwind(session_factory, *, min_score: int):
    pack = _northwind_only_pack(min_score)
    providers = ProviderBundle(llm=DemoLLMProvider(pack, seed=42), search=DemoSearchProvider(pack, seed=42))
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="qualification test play", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=42)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=30,
    )
    return summary.outcomes[0]


async def test_below_raised_minimum_is_not_qualified_end_to_end(session_factory) -> None:
    outcome = await _run_northwind(session_factory, min_score=95)  # Northwind scores 92
    assert outcome.score is not None and outcome.score.overall == 92
    assert outcome.review is not None
    assert outcome.review.verdict == ReviewVerdict.PASS
    assert len(outcome.review.checks) == 7  # review itself is untouched
    assert outcome.status == ProspectStatus.NOT_QUALIFIED


async def test_at_default_minimum_still_passes_end_to_end(session_factory) -> None:
    outcome = await _run_northwind(session_factory, min_score=60)  # canonical Demo default
    assert outcome.score is not None and outcome.score.overall == 92
    assert outcome.status == ProspectStatus.PASS


async def test_not_qualified_status_is_not_actionable_end_to_end(session_factory) -> None:
    """§Action safety — a NOT_QUALIFIED prospect must never be proposal/send
    eligible. Proven through the real `domain/action_policy.py::evaluate()`
    call, not just membership in the frozenset."""
    from datetime import datetime, timezone

    from groundwork.domain.action_policy import evaluate
    from groundwork.domain.content_hash import HASH_VERSION
    from groundwork.models.enums import ActionExecutionOrigin, ActionPolicyVerdict, ActionType, Channel

    outcome = await _run_northwind(session_factory, min_score=95)
    assert outcome.status == ProspectStatus.NOT_QUALIFIED

    result = evaluate(
        action_type=ActionType.EMAIL_SEND,
        origin=ActionExecutionOrigin.DEMO_SIMULATED,
        review_verdict=ReviewVerdict.PASS,
        prospect_status=outcome.status,
        draft_channel=Channel.EMAIL,
        draft_subject="subject",
        draft_body="body",
        proposal_content_hash="h",
        recomputed_content_hash="h",
        proposal_hash_version=HASH_VERSION,
        approval_hash_version=HASH_VERSION,
        recipient_identifier="person@example.com",
        connected_sender_identifier="demo-sender@groundwork.invalid",
        proposal_sender_identifier="demo-sender@groundwork.invalid",
        send_provider_configured=True,
        now=datetime.now(timezone.utc),
    )
    assert result.verdict == ActionPolicyVerdict.BLOCKED
    assert "prospect_not_actionable" in result.blocked_reasons
