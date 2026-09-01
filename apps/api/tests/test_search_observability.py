"""H2 Phase 22 — search observability reconciliation: every real provider
attempt persists to `search_calls`, discovery/domain-resolution counters
reconcile from `run_events`, extraction failures are visible, secrets never
leak into persisted telemetry, and usage/credits are distinguishable from
cost. No network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import groundwork.config as config_module
from groundwork.engine.runner import Repos
from groundwork.evaluation.metrics import compute_run_evaluation
from groundwork.observability.events import EventEmitter
from groundwork.observability.search_calls import SearchCallRecorder
from groundwork.providers.base import SearchAttemptKind, SearchAttemptStatus, SearchAttemptTelemetry, SearchOperation
from tests.search_live_helpers import make_search_provider, search_response, search_result
from groundwork.models.schemas import PlaySpec


async def _make_run(session_factory) -> tuple[Repos, str, str]:
    from groundwork.repositories.plays import PlayRepository

    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)
    play_id = await plays.create(name="t", objective_text="obj", icp_spec={}, mode="live")
    run_id = await repos.runs.create(play_id=play_id, mode="live", seed=1, provider_profile={})
    return repos, play_id, run_id


async def test_search_calls_persisted_match_provider_attempts(session_factory) -> None:
    repos, play_id, run_id = await _make_run(session_factory)
    provider, transport = make_search_provider(
        [httpx_timeout(), (200, search_response())], settings_overrides={"search_max_transport_retries": 1}
    )
    spec = PlaySpec(objective_text="find robotics companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key=f"{run_id}:discovery", max_queries=1)
    recorder = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)
    await recorder.record(telemetry=raw.telemetry, documents=raw.documents)

    persisted = await repos.search.search_calls_for_run(run_id)
    assert len(persisted) == len(raw.telemetry) == 2  # 1 timeout attempt + 1 successful retry
    assert {row.status for row in persisted} == {"TIMEOUT", "OK"}


async def test_secret_never_leaks_into_persisted_search_calls(session_factory, monkeypatch) -> None:
    repos, play_id, run_id = await _make_run(session_factory)
    monkeypatch.setattr(config_module.settings, "tavily_api_key", "super-secret-tvly-key")
    now = datetime.now(timezone.utc)
    telemetry = SearchAttemptTelemetry(
        provider="tavily", operation=SearchOperation.RESOLVE_DOMAIN, query_group_id="g1",
        call_group_id="c1", attempt=1, attempt_kind=SearchAttemptKind.INITIAL,
        status=SearchAttemptStatus.AUTH_ERROR, started_at=now, finished_at=now,
        error_message="401: invalid key super-secret-tvly-key rejected",
    )
    recorder = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)
    await recorder.record(telemetry=[telemetry], documents=[])
    persisted = await repos.search.search_calls_for_run(run_id)
    assert len(persisted) == 1
    assert "super-secret-tvly-key" not in (persisted[0].error_message or "")
    assert "[REDACTED]" in (persisted[0].error_message or "")


async def test_discovery_metrics_reconcile_from_run_events(session_factory) -> None:
    repos, play_id, run_id = await _make_run(session_factory)
    events = EventEmitter(run_id=run_id, events=repos.events)
    await events.emit("discovery.candidate_rejected", reason="unsupported_refs", company="Ghost Co")
    await events.emit("discovery.candidate_rejected", reason="unsupported_refs", company="Vapor Inc")
    await events.emit("discovery.candidate_rejected", reason="unresolved_domain", company="No Domain Co")
    await events.emit("discovery.domain_resolved", company="Acme Robotics", method="deterministic")
    await events.emit("discovery.domain_resolved", company="Beta Systems", method="llm")

    evaluation = await compute_run_evaluation(run_id, repos)
    sq = evaluation["search_quality"]
    assert sq["discovery_rejection_reasons"] == {"unsupported_refs": 2, "unresolved_domain": 1}
    assert sq["domain_resolution_method_counts"] == {"deterministic": 1, "llm": 1}


async def test_extraction_failure_and_usage_metrics(session_factory) -> None:
    repos, play_id, run_id = await _make_run(session_factory)
    now = datetime.now(timezone.utc)
    extract_ok = SearchAttemptTelemetry(
        provider="tavily", operation=SearchOperation.EXTRACT, query_group_id="g1", call_group_id="c1",
        attempt=1, attempt_kind=SearchAttemptKind.INITIAL, status=SearchAttemptStatus.PARTIAL_EXTRACTION,
        started_at=now, finished_at=now, credits_used=2.0,
    )
    recorder = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)
    await recorder.record(telemetry=[extract_ok], documents=[])

    evaluation = await compute_run_evaluation(run_id, repos)
    sq = evaluation["search_quality"]
    assert sq["extraction_calls"] == 1
    assert sq["partial_extractions"] == 1
    # No trustworthy USD rate configured in tests -> cost stays null even
    # though real credits usage was reported and summed.
    assert sq["search_cost_usd"] is None
    assert sq["search_credits_used"] == 2.0


def httpx_timeout():
    import httpx

    return httpx.ConnectTimeout("boom")


async def test_duplicate_real_url_collapses_at_discovery_stage_persistence(session_factory) -> None:
    """H2 post-smoke item 8: the real first smoke's 35 result occurrences
    all being unique sources is plausibly just true for those particular
    diverse queries — not evidence dedupe was bypassed. This proves
    canonicalization IS actually applied for run-scoped (prospect_id=None)
    Stage-A discovery occurrences specifically, the one path this
    checkpoint newly added persistence for, not just the already-covered
    per-prospect retrieval path."""
    repos, play_id, run_id = await _make_run(session_factory)
    same_url = "https://acme.example.com/news/funding"

    provider, transport = make_search_provider(
        [
            (200, search_response(results=[search_result(id="a", url=same_url, content="Acme raised funding.")])),
            (200, search_response(results=[search_result(id="b", url=same_url, content="Acme raised funding.")])),
            (200, search_response(results=[])),
            (200, search_response(results=[])),
        ]
    )
    spec = PlaySpec(objective_text="find companies", target_industries=["robotics"])
    raw = await provider.raw_discover(spec, ctx_key=f"{run_id}:discovery", max_queries=4)
    assert len(raw.documents) == 2  # two occurrences of the same URL

    recorder = SearchCallRecorder(run_id=run_id, prospect_id=None, repo=repos.search)
    await recorder.record(telemetry=raw.telemetry, documents=raw.documents)

    persisted = await repos.search.source_documents_for_run(run_id)
    assert len(persisted) == 2
    winners = [d for d in persisted if d.is_winner]
    losers = [d for d in persisted if not d.is_winner]
    assert len(winners) == 1  # collapsed to exactly one winner
    assert len(losers) == 1
    assert losers[0].canonical_source_id == winners[0].id
    assert winners[0].prospect_id is None  # run-scoped, no prospect exists yet
