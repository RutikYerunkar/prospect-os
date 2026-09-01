"""H1 Phase 1 (Bug A) / Phase 11 — retrieval state (`ctx.sources`) vs
accepted Evidence state (`ctx.evidence`) regression tests.

These use a custom LLM provider stub that fails the `research_extraction`
call a controlled number of times, wrapping the real `DemoLLMProvider` for
everything else, alongside a `DemoSearchProvider` subclass that counts real
`fetch_sources` invocations — proving directly, not by inference, that a
step-level research retry never calls the search provider a second time and
never appends evidence twice.
"""

from __future__ import annotations

from groundwork.engine.runner import Repos, execute_run
from groundwork.models.enums import Mode, ProspectStatus
from groundwork.providers.base import LLMOperation, ProviderTimeout, ProviderUnavailable
from groundwork.providers.demo.demo_llm import DemoLLMProvider
from groundwork.providers.demo.demo_search import DemoSearchProvider
from groundwork.providers.demo.fixtures import FixturePack, load_fixture_pack
from groundwork.providers.base import ProviderBundle
from groundwork.repositories.plays import PlayRepository


def _single_company_pack(slug: str) -> FixturePack:
    base = load_fixture_pack()
    company = base.company_by_slug(slug)
    return FixturePack(play_spec=base.play_spec.model_copy(update={"target_count": 1}), companies=[company])


class _CountingSearch(DemoSearchProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fetch_calls = 0

    async def fetch_sources(self, company, *, ctx_key):  # type: ignore[override]
        self.fetch_calls += 1
        return await super().fetch_sources(company, ctx_key=ctx_key)


class _FlakyLLM:
    """Wraps a real `DemoLLMProvider`; raises a scripted, step-retryable
    error the first N times `research_extraction` is called for a given
    `ctx_key`, then delegates to the real provider."""

    name = "flaky_demo_llm"

    def __init__(self, inner: DemoLLMProvider, *, fail_times: int, exc_cls=ProviderTimeout) -> None:
        self.inner = inner
        self.fail_times = fail_times
        self.exc_cls = exc_cls
        self._attempts: dict[str, int] = {}

    async def structured(self, envelope, schema, *, ctx_key, operation):
        if operation == LLMOperation.RESEARCH_EXTRACTION:
            attempt = self._attempts.get(ctx_key, 0) + 1
            self._attempts[ctx_key] = attempt
            if attempt <= self.fail_times:
                raise self.exc_cls(f"scripted failure attempt {attempt} for {ctx_key}")
        return await self.inner.structured(envelope, schema, ctx_key=ctx_key, operation=operation)


async def _run_single_company(session_factory, *, slug: str, llm, search) -> tuple:
    pack = _single_company_pack(slug)
    providers = ProviderBundle(llm=llm, search=search)
    repos = Repos.build(session_factory)
    plays = PlayRepository(session_factory)

    play_id = await plays.create(
        name="retrieval-state test", objective_text=pack.play_spec.objective_text,
        icp_spec=pack.play_spec.model_dump(mode="json"), mode="demo",
    )
    run_id = await repos.runs.create(play_id=play_id, mode="demo", seed=99)

    summary = await execute_run(
        run_id=run_id, play_spec=pack.play_spec, providers=providers, repos=repos,
        max_concurrent_prospects=1, run_wall_clock_timeout_s=30,
    )
    return summary, repos, run_id


async def test_search_ok_llm_timeout_then_retry_ok_no_second_search_call(session_factory) -> None:
    pack = load_fixture_pack()
    single = _single_company_pack("sable-compute")
    demo_llm = DemoLLMProvider(single, seed=99)
    search = _CountingSearch(single, seed=99)
    llm = _FlakyLLM(demo_llm, fail_times=1, exc_cls=ProviderTimeout)

    summary, repos, run_id = await _run_single_company(
        session_factory, slug="sable-compute", llm=llm, search=search
    )

    outcome = summary.outcomes[0]
    assert outcome.status in (ProspectStatus.PASS, ProspectStatus.NEEDS_REVIEW, ProspectStatus.REJECTED)
    assert search.fetch_calls == 1, "a research retry must reuse cached ctx.sources, never call search again"

    expected_source_count = len(pack.company_by_slug("sable-compute").sources)
    assert outcome.evidence_count == expected_source_count, "retry must not duplicate evidence"

    tasks = await repos.tasks.for_run(run_id)
    research_tasks = sorted((t for t in tasks if t.step_name == "research"), key=lambda t: t.attempt)
    assert [t.status for t in research_tasks] == ["RETRY", "OK"]

    docs = await repos.search.source_documents_for_run(run_id)
    assert len(docs) == expected_source_count, "no duplicate source_documents rows from the retry"


async def test_search_ok_all_llm_retries_fail_evidence_stays_empty(session_factory) -> None:
    pack = load_fixture_pack()
    single = _single_company_pack("sable-compute")
    demo_llm = DemoLLMProvider(single, seed=99)
    search = _CountingSearch(single, seed=99)
    # research has max_retries=2 in DEMO_BUDGET -> 3 total attempts; fail all of them.
    llm = _FlakyLLM(demo_llm, fail_times=10, exc_cls=ProviderUnavailable)

    summary, repos, run_id = await _run_single_company(
        session_factory, slug="sable-compute", llm=llm, search=search
    )

    outcome = summary.outcomes[0]
    assert outcome.status == ProspectStatus.FAILED
    assert outcome.evidence_count == 0, "a prospect that never completes extraction must have zero accepted evidence"

    evidence_rows = await repos.prospect_data.evidence_for_run(run_id)
    assert evidence_rows == []

    # Retrieval telemetry may still exist for observability even though
    # extraction never succeeded (H1 Phase 11 — retrieval state persists
    # independent of accepted-Evidence outcome).
    assert search.fetch_calls == 1, "still only one real search call across all research retries"
    docs = await repos.search.source_documents_for_run(run_id)
    assert len(docs) == len(pack.company_by_slug("sable-compute").sources)


async def test_duplicate_source_refs_do_not_produce_duplicate_evidence(session_factory) -> None:
    """A successful extraction followed by nothing else must commit
    evidence exactly once — the plain-assignment commit in
    `engine/steps/research.py` guarantees this structurally."""
    pack = load_fixture_pack()
    single = _single_company_pack("northwind-labs")
    demo_llm = DemoLLMProvider(single, seed=7)
    search = _CountingSearch(single, seed=7)

    summary, repos, run_id = await _run_single_company(
        session_factory, slug="northwind-labs", llm=demo_llm, search=search
    )

    # Northwind's own fixture-scripted search failure (fail_attempts=1)
    # still exercises a real step-level retry, this time entirely on the
    # search side (the pre-existing scripted mechanism) rather than the LLM
    # side — evidence must still land exactly once.
    outcome = summary.outcomes[0]
    expected_source_count = len(pack.company_by_slug("northwind-labs").sources)
    assert outcome.evidence_count == expected_source_count
    evidence_rows = await repos.prospect_data.evidence_for_run(run_id)
    assert len(evidence_rows) == expected_source_count
    assert len({e.id for e in evidence_rows}) == expected_source_count  # all ids unique, no dupes
