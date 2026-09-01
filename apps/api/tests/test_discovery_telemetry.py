"""H1 deviation closure #1 — `discover()` is an active execution path
(`engine/runner.py::discover_and_dedupe()` calls it on every run) and must
not bypass the same `engine/search.py` persistence seam `fetch_sources()`
already uses. These tests prove the wiring directly, not by inspection:
one Demo discovery attempt produces the expected `search_calls` row, the
provider's own attempt count reconciles with what's persisted, a scripted
discovery failure's telemetry still persists before the exception
propagates, and the canonical Demo domain output is unaffected.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from groundwork.engine.runner import Repos, discover_and_dedupe, execute_run
from groundwork.models.enums import Mode, ProspectStatus
from groundwork.providers.base import (
    ProviderBundle,
    SearchAttemptStatus,
    SearchAttemptTelemetry,
    SearchOperation,
    SearchProviderError,
)
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import load_fixture_pack
from groundwork.providers.registry import build_provider_bundle
from groundwork.repositories.plays import PlayRepository


class _FlakyDiscovery(DemoSearchProvider):
    """Wraps a real `DemoSearchProvider`; raises a scripted
    `SearchProviderError` (with attached telemetry) the first `fail_times`
    calls to `discover()`, then delegates to the real implementation."""

    def __init__(self, *args, fail_times: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fail_times = fail_times
        self.attempts = 0

    async def discover(self, spec, limit):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            started = datetime.now(timezone.utc)
            telemetry = SearchAttemptTelemetry(
                provider=self.name,
                operation=SearchOperation.DISCOVER,
                query_group_id=f"discover:{self.seed}",
                call_group_id=str(uuid.uuid4()),
                status=SearchAttemptStatus.PROVIDER_ERROR,
                started_at=started,
                finished_at=started,
                error_type="ProviderUnavailable",
                error_message="scripted discovery failure",
            )
            raise SearchProviderError("scripted discovery failure", telemetry=[telemetry])
        return await super().discover(spec, limit)


async def _play_and_run(session_factory, repos: Repos, pack, *, seed: int = 42) -> str:
    plays = PlayRepository(session_factory)
    play_id = await plays.create(
        name="discovery telemetry test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    return await repos.runs.create(play_id=play_id, mode="demo", seed=seed)


async def test_demo_discovery_produces_expected_search_calls_row(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    run_id = await _play_and_run(session_factory, repos, pack)

    await discover_and_dedupe(run_id, pack.play_spec, providers, repos)

    calls = await repos.search.search_calls_for_run(run_id)
    discover_calls = [c for c in calls if c.operation == "discover"]
    assert len(discover_calls) == 1
    row = discover_calls[0]
    assert row.run_id == run_id
    assert row.prospect_id is None  # no prospect exists yet at discovery time
    assert row.provider == "demo_fixture"
    assert row.status == "OK"
    assert row.result_count == len(pack.companies)
    assert row.selected_count == pack.play_spec.target_count
    assert row.cost_usd is None  # never a fabricated cost


async def test_provider_attempt_count_reconciles_with_persisted_count(session_factory) -> None:
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=7, fixture_pack=pack)
    repos = Repos.build(session_factory)
    run_id = await _play_and_run(session_factory, repos, pack, seed=7)

    discovery = await providers.search.discover(pack.play_spec, pack.play_spec.target_count)
    provider_attempt_count = len(discovery.telemetry)

    await discover_and_dedupe(run_id, pack.play_spec, providers, repos)
    calls = await repos.search.search_calls_for_run(run_id)
    discover_calls = [c for c in calls if c.operation == "discover"]

    assert provider_attempt_count == 1  # Demo Mode: always exactly one OK attempt
    assert len(discover_calls) == provider_attempt_count


async def test_discovery_failure_telemetry_persists_before_reraise(session_factory) -> None:
    pack = load_fixture_pack()
    search = _FlakyDiscovery(pack, seed=1, fail_times=1)
    providers = ProviderBundle(llm=DemoLLMProvider(pack, seed=1), search=search)
    repos = Repos.build(session_factory)
    run_id = await _play_and_run(session_factory, repos, pack, seed=1)

    with pytest.raises(SearchProviderError):
        await discover_and_dedupe(run_id, pack.play_spec, providers, repos)

    calls = await repos.search.search_calls_for_run(run_id)
    discover_calls = [c for c in calls if c.operation == "discover"]
    assert len(discover_calls) == 1
    row = discover_calls[0]
    assert row.status == "PROVIDER_ERROR"
    assert row.error_type == "ProviderUnavailable"
    assert row.error_message == "scripted discovery failure"
    assert row.run_id == run_id
    assert row.prospect_id is None

    # Discovery genuinely never produced prospects — the exception
    # propagated before any were created.
    prospects = await repos.prospects.list_for_run(run_id)
    assert prospects == []

    # A subsequent successful discover() call (the retry a caller might
    # attempt) adds a second, independent OK row rather than mutating the
    # failed one.
    prospect_seeds, _ = await discover_and_dedupe(run_id, pack.play_spec, providers, repos)
    assert len(prospect_seeds) == pack.play_spec.target_count
    calls_after = await repos.search.search_calls_for_run(run_id)
    discover_calls_after = [c for c in calls_after if c.operation == "discover"]
    assert len(discover_calls_after) == 2
    assert {c.status for c in discover_calls_after} == {"PROVIDER_ERROR", "OK"}


async def test_canonical_demo_domain_output_unchanged_by_discovery_telemetry(session_factory) -> None:
    """The whole point of routing discover() through the same persistence
    seam is that it must be a pure observability addition — zero effect on
    computed outcomes."""
    pack = load_fixture_pack()
    providers = build_provider_bundle(Mode.DEMO, seed=42, fixture_pack=pack)
    repos = Repos.build(session_factory)
    run_id = await _play_and_run(session_factory, repos, pack)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=3, run_wall_clock_timeout_s=60,
    )

    by_slug = {o.company.slug: o for o in summary.outcomes}
    assert by_slug["northwind-labs"].score.overall == 92
    assert by_slug["riverbend-analytics"].score.overall == 35
    assert by_slug["cobalt-retail-systems"].score.overall == 25
    assert by_slug["cobalt-retail-systems"].status == ProspectStatus.REJECTED
    assert by_slug["ferrous-grid"].score.overall == 58
    assert by_slug["sable-compute"].score.overall == 79
    assert summary.counters == {
        ProspectStatus.PASS.value: 2,
        ProspectStatus.NEEDS_REVIEW.value: 2,
        ProspectStatus.REJECTED.value: 1,
        ProspectStatus.DUPLICATE.value: 1,
        ProspectStatus.FAILED.value: 1,
    }
