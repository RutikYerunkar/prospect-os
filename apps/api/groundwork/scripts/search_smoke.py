"""`python -m groundwork.scripts.search_smoke --i-understand-this-costs-money`

The OPTIONAL real-live H2 smoke test. Exercises the FULL `LIVE LLM · LIVE
SEARCH` path — real OpenAI + real Tavily — against 1-2 REAL, newly-discovered
companies (never the demo fixture pack). This costs real money and makes
real network calls to two providers — it must NEVER run accidentally:

- Requires the exact `--i-understand-this-costs-money` flag.
- Requires BOTH `OPENAI_API_KEY` and `TAVILY_API_KEY` to actually be
  configured.
- Refuses to proceed (before making any paid call) if the local SQLite
  schema predates H2's `search_calls`/`source_documents`/`llm_calls`
  tables.
- Never runs as part of `make test`, CI, `make search-spike`, or any other
  automated path.

Does NOT require the final status to be PASS — a deterministic
REJECTED/NEEDS_REVIEW outcome is correct safety behavior, not a smoke
failure. FAILS loudly (nonzero exit) only if a real structural invariant is
violated: synthetic (`DEMO_FIXTURE`) evidence appearing anywhere in a Live
run, a model-authored string somehow becoming a canonical domain, a
clickable evidence URL that didn't originate from provider data, the
transport retry ceiling being exceeded, a fatal provider-wiring error, or
any uncaught exception.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from groundwork.config import settings
from groundwork.db import SessionLocal, create_all, engine
from groundwork.domain.query_plan import QUERY_PLAN_VERSION
from groundwork.engine.budget import PipelineBudget
from groundwork.engine.discovery import DiscoveryBounds
from groundwork.engine.run_budget import RunBudget
from groundwork.engine.runner import Repos, execute_run
from groundwork.engine.search_budget import SearchCallBudget
from groundwork.models.enums import EvidenceOrigin, Mode
from groundwork.models.schemas import PlaySpec
from groundwork.providers.base import ProviderBundle
from groundwork.providers.live.openai_llm import OpenAILLMProvider
from groundwork.providers.live.runtime import LiveProviderRuntime
from groundwork.providers.live.search_runtime import LiveSearchRuntime
from groundwork.providers.live.tavily_search import TavilySearchProvider
from groundwork.repositories.plays import PlayRepository

# The real, live objective this smoke discovers against — deliberately NOT
# a fixture company name; H2's whole point is discovering real companies
# the codebase has never seen before.
_DEFAULT_OBJECTIVE = (
    "Find early-stage AI infrastructure companies that have recently raised "
    "funding or are hiring go-to-market roles."
)


def _print_preamble(prospect_cap: int) -> None:
    print("=== Groundwork H2 search smoke test — REAL OpenAI + REAL Tavily, REAL cost ===")
    print(f"OpenAI model:            {settings.openai_model}")
    print(f"OpenAI reasoning_effort: {settings.openai_reasoning_effort or '(omitted)'}")
    print("Tavily provider:         tavily (see docs/PROGRESS.md for pinned tavily-python version)")
    print(f"query plan version:      {QUERY_PLAN_VERSION}")
    print(f"prospect cap:            {prospect_cap}")
    print(f"plan query cap:          {settings.live_max_plan_queries_per_run}")
    print(f"domain resolution cap:   {settings.live_max_domain_resolution_queries_per_run}")
    print(f"source query cap/prospect: {settings.live_max_source_queries_per_prospect}")
    print(f"search logical call cap: {settings.live_max_search_calls_per_run}")
    print(f"results/query cap:       {settings.live_max_search_results_per_query}")
    print(f"unique sources/prospect: {settings.live_max_sources_per_prospect}")
    print(f"extract call cap:        {settings.live_max_extract_calls_per_run}")
    max_llm_attempts = 1 + settings.llm_max_transport_retries + settings.llm_max_schema_retries
    print(f"LLM attempt bound (per logical call): {max_llm_attempts}")

    openai_pricing_ok = (
        settings.openai_price_input_usd_per_mtok is not None and settings.openai_price_output_usd_per_mtok is not None
    )
    tavily_pricing_ok = settings.tavily_price_usd_per_credit is not None
    if openai_pricing_ok and tavily_pricing_ok:
        print("dollar bound: available for BOTH providers (see per-run telemetry after the run)")
    else:
        missing = []
        if not openai_pricing_ok:
            missing.append("OPENAI_PRICE_*_USD_PER_MTOK")
        if not tavily_pricing_ok:
            missing.append("TAVILY_PRICE_USD_PER_CREDIT")
        print(f"dollar bound: UNAVAILABLE for {', '.join(missing)} — hard call/query/result/extract caps above are the real safety controls")
    print()


_QUOTA_EXHAUSTED_MESSAGE = (
    "OpenAI provider quota/credit exhausted (permanent — not retried). "
    "Add API credits or use a funded project/key before rerunning."
)


def _describe_error(error_text: str | None) -> str | None:
    """H2 second post-smoke fix: never echo a raw provider error that may
    carry a billing/upgrade URL — this is a best-effort keyword check for
    display contexts (`outcome.error`) that only carry a flat string, not
    the structured `type`/`code` the classifier itself uses
    (`providers/live/openai_llm.py::_is_quota_exhausted`)."""
    if error_text and ("insufficient_quota" in error_text or "credit_balance_exhausted" in error_text):
        return _QUOTA_EXHAUSTED_MESSAGE
    return error_text


def _print_review(review) -> None:
    print(f"\nreview verdict: {review.verdict.value}")
    for check in review.checks:
        mark = "PASS" if check.passed else "FAIL"
        print(f"  [{mark}] {check.id:<20} ({check.severity:<4}) — {check.detail}")


async def _print_discovery_funnel(repos, run_id: str) -> int:
    """H2 post-smoke: the real first smoke could only tell us zero
    prospects survived, not WHERE in the discovery funnel they were lost.
    Reconstructed entirely from `run_events` — the same architecture
    `evaluation/metrics.py::search_quality` already reads, never a second
    telemetry system. Returns the final CompanySeed count so the caller can
    decide the smoke's own pass/fail.
    """
    events = await repos.events.after(run_id, 0)
    extraction_completed = [e for e in events if e.type == "discovery.extraction_completed"]
    rejected = [e for e in events if e.type == "discovery.candidate_rejected"]
    resolved = [e for e in events if e.type == "discovery.domain_resolved"]

    print("\n--- discovery funnel ---")
    if extraction_completed:
        payload = extraction_completed[-1].payload
        print(f"search-result hits fed to DISCOVERY_EXTRACTION: {payload.get('hits')}")
        print(f"candidates proposed by DISCOVERY_EXTRACTION:     {payload.get('candidates_proposed')}")
        print(f"candidates surviving server-side support check:  {payload.get('candidates_valid')}")
    else:
        print("DISCOVERY_EXTRACTION never completed for this run (see rejection reasons below)")

    reason_counts: dict[str, int] = {}
    for e in rejected:
        reason = e.payload.get("reason", "unknown")
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if reason_counts:
        print("candidates rejected, by reason:")
        for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:<40} {count}")
    else:
        print("candidates rejected, by reason: none")

    # The one non-generic rejection reason worth surfacing in full — it
    # means the LLM call itself failed, not that it found nothing.
    unavailable = [e for e in rejected if e.payload.get("reason") == "discovery_extraction_unavailable"]
    for e in unavailable:
        last_status = e.payload.get("last_attempt_status")
        if last_status == "QUOTA_EXHAUSTED":
            # H2 second post-smoke fix: a real OpenAI 429 body can carry a
            # billing/upgrade URL in its message text — never echoed here.
            # This is the one actionable, unambiguous case worth a plain-
            # language line instead of the raw provider error.
            print(f"  -> {_QUOTA_EXHAUSTED_MESSAGE}")
        else:
            print(
                f"  -> DISCOVERY_EXTRACTION unavailable: attempts_made={e.payload.get('attempts_made')} "
                f"last_status={last_status} "
                f"last_error={_describe_error(e.payload.get('last_attempt_error'))!r}"
            )

    method_counts: dict[str, int] = {}
    for e in resolved:
        method = e.payload.get("method", "unknown")
        method_counts[method] = method_counts.get(method, 0) + 1
    print(f"domains resolved deterministically: {method_counts.get('deterministic', 0)}")
    print(f"domains resolved via DOMAIN_SELECTION fallback: {method_counts.get('llm', 0)}")
    print(f"final CompanySeed count: {len(resolved)}")

    return len(resolved)


async def main(prospect_cap: int) -> int:
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1
    if not settings.tavily_api_key:
        print("TAVILY_API_KEY is not configured — aborting.", file=sys.stderr)
        return 1

    prospect_cap = min(prospect_cap, 2, settings.live_max_prospects_per_run)
    _print_preamble(prospect_cap)

    await create_all()
    repos = Repos.build(SessionLocal)

    llm_runtime = LiveProviderRuntime.create(settings)
    search_runtime = LiveSearchRuntime.create(settings)
    run_budget = RunBudget(settings.live_run_soft_budget_usd)
    search_budget = SearchCallBudget(
        max_search_calls=settings.live_max_search_calls_per_run,
        max_extract_calls=settings.live_max_extract_calls_per_run,
    )
    providers = ProviderBundle(
        llm=OpenAILLMProvider(runtime=llm_runtime, run_budget=run_budget),
        search=TavilySearchProvider(
            runtime=search_runtime, search_budget=search_budget,
            max_results_per_query=settings.live_max_search_results_per_query,
            max_source_queries_per_prospect=settings.live_max_source_queries_per_prospect,
            max_result_occurrences_per_prospect=settings.live_max_result_occurrences_per_prospect,
            max_sources_per_prospect=settings.live_max_sources_per_prospect,
            max_source_excerpt_chars=settings.live_max_source_excerpt_chars,
        ),
    )

    play_spec = PlaySpec(objective_text=_DEFAULT_OBJECTIVE, target_industries=["ai_infrastructure"], target_count=prospect_cap)
    play_id = await PlayRepository(SessionLocal).create(
        name="H2 search smoke test", objective_text=play_spec.objective_text,
        icp_spec=play_spec.model_dump(mode="json"), mode=Mode.LIVE.value,
    )
    run_id = await repos.runs.create(play_id=play_id, mode=Mode.LIVE.value, seed=1)

    budget = PipelineBudget(
        default_step_timeout_s=settings.live_step_timeout_s, research_timeout_s=settings.live_step_timeout_s,
        research_max_retries=2, personalize_timeout_s=settings.live_step_timeout_s, personalize_max_retries=1,
        backoffs_s=(0.4, 0.8, 1.6), max_concurrent_prospects=prospect_cap,
        run_wall_clock_timeout_s=settings.live_run_wall_clock_timeout_s,
    )
    discovery_bounds = DiscoveryBounds(
        max_plan_queries=settings.live_max_plan_queries_per_run,
        max_domain_resolution_queries=settings.live_max_domain_resolution_queries_per_run,
        discovery_llm_call_deadline_s=settings.llm_discovery_call_deadline_s,
    )

    failures: list[str] = []
    try:
        summary = await execute_run(
            run_id=run_id, play_spec=play_spec, providers=providers, repos=repos,
            max_concurrent_prospects=prospect_cap, run_wall_clock_timeout_s=budget.run_wall_clock_timeout_s,
            budget=budget, discovery_bounds=discovery_bounds,
        )
    except Exception as exc:  # noqa: BLE001 — a fatal wiring error must FAIL this smoke loudly
        print(f"\nFATAL: uncaught exception during execute_run: {exc!r}", file=sys.stderr)
        await llm_runtime.close()
        await search_runtime.close()
        await engine.dispose()
        return 1

    await llm_runtime.close()
    await search_runtime.close()

    search_calls = await repos.search.search_calls_for_run(run_id)
    source_documents = await repos.search.source_documents_for_run(run_id)
    llm_calls = await repos.llm_calls.for_run(run_id)

    print("\n--- search telemetry ---")
    for c in search_calls:
        print(f"{c.operation:<15} attempt={c.attempt} status={c.status:<10} request_id={c.provider_request_id} results={c.result_count}")
    request_ids = sorted({c.provider_request_id for c in search_calls if c.provider_request_id})
    print(f"\nprovider request ids: {request_ids}")
    credits_used = [c.credits_used for c in search_calls if c.credits_used is not None]
    if credits_used:
        print(f"Tavily usage/credits reported: {sum(credits_used)}")
    else:
        print("Tavily usage/credits: not reported by this response shape")

    unique_sources = sorted({d.canonical_url or d.url for d in source_documents if d.is_winner and (d.canonical_url or d.url)})
    occurrence_count = len(source_documents)
    print(f"\nresult occurrences: {occurrence_count}")
    print(f"unique sources: {len(unique_sources)}")
    for url in unique_sources:
        print(f"  - {url}")

    resolved_company_count = await _print_discovery_funnel(repos, run_id)

    print("\n--- discovered prospects ---")
    if not summary.outcomes:
        print("(none)")
    for outcome in summary.outcomes:
        print(f"\n{outcome.company.name} ({outcome.company.domain})")
        print(f"  status: {outcome.status.value}")
        if outcome.error:
            print(f"  error: {_describe_error(outcome.error)}")
        if outcome.score:
            industry_dim = next((d for d in outcome.score.dimensions if d.name == "industry_fit"), None)
            size_dim = next((d for d in outcome.score.dimensions if d.name == "size_fit"), None)
            print(f"  score: {outcome.score.overall}  confidence: {outcome.score.confidence:.2f}")
            print(f"  industry_fit support: {industry_dim.support.value if industry_dim else 'n/a'}")
            print(f"  size_fit support:     {size_dim.support.value if size_dim else 'n/a'}")
        for draft in outcome.drafts:
            print(f"  outreach subject: {draft.subject}")
            print(f"  claim_map entries: {len(draft.claim_map)}")
        if outcome.review:
            _print_review(outcome.review)

        # --- structural safety assertions (Phase 23 FAIL conditions) ---
        evidence_rows = [e for e in await repos.prospect_data.get_evidence(outcome.prospect_id)]
        for e in evidence_rows:
            if e.origin == EvidenceOrigin.DEMO_FIXTURE:
                failures.append(f"{outcome.company.name}: synthetic DEMO_FIXTURE evidence found in a Live run ({e.id})")
            if e.origin == EvidenceOrigin.LIVE_FETCH:
                served_urls = {d.url for d in source_documents if d.url}
                if e.source_url not in served_urls:
                    failures.append(
                        f"{outcome.company.name}: evidence URL {e.source_url!r} did not originate from provider data"
                    )
        served_domains = {d.domain for d in source_documents if d.domain}
        if outcome.company.domain and served_domains and outcome.company.domain not in served_domains:
            failures.append(f"{outcome.company.name}: canonical domain {outcome.company.domain!r} did not come from a served provider URL")

    max_transport_attempts = 1 + settings.search_max_transport_retries
    over_budget = [c for c in search_calls if c.attempt > max_transport_attempts]
    if over_budget:
        failures.append(f"retry ceiling exceeded on {len(over_budget)} search_calls row(s)")

    print(f"\nfinal run status: {summary.status}")
    print(f"llm calls: {len(llm_calls)}  search calls: {len(search_calls)}  source occurrences: {occurrence_count}")

    await engine.dispose()

    if failures:
        print("\nFAILURE — structural invariant violated:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    # H2 post-smoke acceptance rule — SMOKE-SCRIPT ONLY, not a production
    # invariant: a run that performed real discovery (live search actually
    # ran, `search_calls` is non-empty) but produced zero surviving
    # prospects does not prove this checkpoint end-to-end — domain
    # resolution, per-company retrieval, extraction, and the whole
    # per-prospect pipeline never got exercised. A legitimate empty result
    # set is still valid *product* behavior (a genuinely quiet search
    # topic) and must never make a normal run fail — see the discovery
    # funnel printed above for exactly where candidates were lost before
    # concluding this is a bug rather than a truthful empty result.
    if prospect_cap >= 1 and search_calls and resolved_company_count == 0:
        print(
            "\nH2 smoke incomplete: live search succeeded, but no real prospect survived "
            "discovery/domain resolution. See the discovery funnel above for exactly where "
            "candidates were lost.",
            file=sys.stderr,
        )
        return 1

    print("\nOK — no structural invariant violated. (Final status need not be PASS.)")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--i-understand-this-costs-money", action="store_true", dest="confirmed",
        help="required — this makes real, billed OpenAI + Tavily API calls",
    )
    parser.add_argument("--prospects", type=int, default=1, help="how many prospects to discover (max 2)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if not args.confirmed:
        print("Refusing to run without --i-understand-this-costs-money.", file=sys.stderr)
        sys.exit(1)
    sys.exit(asyncio.run(main(args.prospects)))
